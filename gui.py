"""
gui.py — Serial Data Logging GUI
--------------------------------
XY plot (left) | Serial-IN, live tables, multi-series time plot (right)
Plots show **only the last 10 seconds** of data.

pip install pyqtgraph PySide6 pyserial numpy
"""

import queue, bisect, serial, numpy as np, time, itertools
from pathlib import Path
from typing  import Dict, List, Optional

from PySide6.QtCore    import Qt, Signal, QObject, QTimer
from PySide6.QtWidgets import (
    QWidget, QSplitter, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QInputDialog, QFileDialog, QMessageBox, QListWidget, QListWidgetItem, QComboBox
)
import pyqtgraph as pg

from data          import PointFrame
from serial_reader import SerialReader

_COLORS = itertools.cycle(['y', 'c', 'm', 'g', 'r', 'w'])  # curve colours


class _Bridge(QObject):
    new_raw = Signal(str)
    new_obj = Signal(object)


class _RawReader(SerialReader):
    def run(self):
        buf = bytearray()
        while not self._stop.is_set():
            buf.extend(self._ser.read(self._ser.in_waiting or 1))
            while b'\n' in buf:
                line, _, buf = buf.partition(b'\n')
                txt = line.decode('utf-8', 'replace').strip()
                self._q.put(('raw', txt))
                parsed = self._parse(txt)
                if not isinstance(parsed, str):
                    self._q.put(('obj', parsed))


class SerialGui(QWidget):
    # ────────────────────────── init ──────────────────────────────────
    def __init__(self, port: str, baud: int):
        super().__init__()
        self.setWindowTitle(f"Serial Data Logging — {port} @ {baud}")

        # serial --------------------------------------------------------
        try: self._ser = serial.Serial(port, baud, timeout=0.1)
        except serial.SerialException as e: raise SystemExit(f"❌ {e}")

        self._q = queue.Queue()
        br = _Bridge(); br.new_raw.connect(lambda s: self._serial_in.append(s)); br.new_obj.connect(self._handle_obj)
        self._bridge = br
        self._reader = _RawReader(self._ser, self._q); self._reader.start()

        # state ---------------------------------------------------------
        self._conn_pairs, self._line_items = [], []
        self._labels, self._fixed_pts = [], []
        self._custom_lbl: Dict[int,str] = {}
        self._lbl_color = "white"
        self._csv_pts: Optional[open] = None; self._csv_ex: Optional[open] = None

        self._ts_time:  List[float] = []
        self._ts_data:  Dict[int, List[float]] = {}
        self._ts_scale: Dict[int, float]       = {}
        self._ts_curves:Dict[int, pg.PlotDataItem] = {}

        # XY plot -------------------------------------------------------
        self._plot = pg.PlotWidget(); self._plot.setAspectLocked(True)
        vb=self._plot.getViewBox(); vb.setRange(xRange=(-2,2),yRange=(0,2)); vb.setMouseEnabled(False,False)
        self._plot.showGrid(x=True,y=True)
        self._scatter=pg.ScatterPlotItem(size=6)
        self._fixed_scatter=pg.ScatterPlotItem(size=6, brush='g')
        self._plot.addItem(self._scatter); self._plot.addItem(self._fixed_scatter)

        # right widgets -------------------------------------------------
        self._serial_in = QTextEdit(readOnly=True, maximumHeight=120)
        self._serial_in.setPlaceholderText("Raw serial input appears here…")
        self._last_tx   = QLineEdit(readOnly=True); self._last_tx.setPlaceholderText("Last TX message")

        self._tbl_pts = self._make_tbl(["Label","X","Y"], editable=True)
        self._tbl_ex  = self._make_tbl([])
        self._tbl_ex.horizontalHeader().sectionDoubleClicked.connect(self._rename_ex_col)

        self._ts_list = QListWidget(maximumHeight=110)
        self._ts_list.itemChanged.connect(self._refresh_ts_plot)
        self._ts_list.itemDoubleClicked.connect(self._edit_scale)  # set multiplier
        self._ts_plot = pg.PlotWidget(minimumHeight=120); self._ts_plot.showGrid(x=True,y=True)

        # controls
        self._clr_combo = QComboBox(); self._clr_combo.addItems(['white','yellow','cyan','magenta'])
        self._clr_combo.currentTextChanged.connect(lambda c: [lab.setColor(c) for lab in self._labels] or setattr(self,'_lbl_color',c))

        self._tx_entry = QLineEdit(); self._tx_entry.setPlaceholderText("Type text & Enter")
        self._tx_btn   = QPushButton("Send")
        self._tx_entry.returnPressed.connect(self._tx); self._tx_btn.clicked.connect(self._tx)

        self._conn_entry = QLineEdit(placeholderText="1-3")
        self._add_conn   = QPushButton("Add Line"); self._clr_conn = QPushButton("Clear Lines")
        self._add_conn.clicked.connect(self._add_conn_pair); self._clr_conn.clicked.connect(self._conn_pairs.clear)

        self._fix_entry = QLineEdit(placeholderText="x,y")
        self._add_fix   = QPushButton("Add Point"); self._add_fix.clicked.connect(self._add_fixed_pt)

        self._log_btn = QPushButton("Start Log → CSV"); self._log_btn.clicked.connect(self._toggle_log)

        # layout --------------------------------------------------------
        right=QVBoxLayout(); right.setContentsMargins(0,0,0,0)
        right.addWidget(QLabel("Serial IN (raw):")); right.addWidget(self._serial_in)
        right.addWidget(QLabel("Last TX:"));         right.addWidget(self._last_tx)
        right.addWidget(self._tbl_pts); right.addWidget(self._tbl_ex)
        right.addWidget(QLabel("Plot columns (double-click to scale):")); right.addWidget(self._ts_list); right.addWidget(self._ts_plot)
        right.addLayout(self._row(QLabel("Label colour:"), self._clr_combo))
        right.addLayout(self._row(self._tx_entry, self._tx_btn))
        right.addLayout(self._row(QLabel("Connect:"), self._conn_entry, self._add_conn, self._clr_conn))
        right.addLayout(self._row(QLabel("Add point:"), self._fix_entry, self._add_fix))
        right.addWidget(self._log_btn)
        right_widget=QWidget(); right_widget.setLayout(right)

        split=QSplitter(Qt.Horizontal); split.addWidget(self._plot); split.addWidget(right_widget)
        split.setStretchFactor(0,4); split.setStretchFactor(1,1)
        QVBoxLayout(self).addWidget(split)

        # signals / timer ----------------------------------------------
        self._tbl_pts.itemChanged.connect(self._tbl_label_edited)
        self._timer=QTimer(self); self._timer.setInterval(50); self._timer.timeout.connect(self._pump); self._timer.start()

    # ---------- helper GUI builders -----------------------------------
    def _make_tbl(self,hdr,*,editable=False):
        t=QTableWidget(0,len(hdr)); t.verticalHeader().setVisible(False)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.setEditTriggers(QTableWidget.DoubleClicked if editable else QTableWidget.NoEditTriggers)
        t.setHorizontalHeaderLabels(hdr); t.setMaximumHeight(150); return t
    def _row(self,*w): h=QHBoxLayout(); [h.addWidget(x) for x in w]; return h

    # ---------- queue pump --------------------------------------------
    def _pump(self):
        try:
            while True:
                typ,payload=self._q.get_nowait()
                if typ=='raw': self._serial_in.append(payload)
                else:          self._handle_obj(payload)
        except queue.Empty: pass

    # ---------- incoming PointFrame -----------------------------------
    def _handle_obj(self,f:PointFrame):
        self._draw(f); self._update_tables(f); self._store_ts(f); self._refresh_ts_plot(); self._log(f)

    # ---------- XY drawing --------------------------------------------
    def _draw(self,f:PointFrame):
        self._scatter.setData([{'pos':(x,y),'brush':'r' if i==0 else 'b'} for i,(x,y) in enumerate(f.coords)])
        total=len(f.coords)+len(self._fixed_pts)
        while len(self._labels)<total:
            lab=pg.TextItem(anchor=(0.5,-0.3),color=self._lbl_color); self._plot.addItem(lab); self._labels.append(lab)
        for i,(x,y) in enumerate(f.coords,1):
            self._labels[i-1].setText(self._custom_lbl.get(i-1,str(i))); self._labels[i-1].setPos(x,y); self._labels[i-1].show()
        for j,(fx,fy) in enumerate(self._fixed_pts,start=len(f.coords)+1):
            self._labels[j-1].setText(self._custom_lbl.get(j-1,str(j))); self._labels[j-1].setPos(fx,fy); self._labels[j-1].show()
        for k in range(total,len(self._labels)): self._labels[k].hide()
        self._fixed_scatter.setData(pos=np.asarray(self._fixed_pts).reshape(-1,2)) if self._fixed_pts else self._fixed_scatter.clear()
        while len(self._line_items)<len(self._conn_pairs):
            li=pg.PlotDataItem(pen=pg.mkPen('b',width=5)); self._plot.addItem(li); self._line_items.append(li)
        px=[p[0] for p in f.coords]+[p[0] for p in self._fixed_pts]
        py=[p[1] for p in f.coords]+[p[1] for p in self._fixed_pts]
        for k,(a,b) in enumerate(self._conn_pairs):
            if a<=len(px) and b<=len(px): self._line_items[k].setData([px[a-1],px[b-1]],[py[a-1],py[b-1]])
            else: self._line_items[k].clear()

    # ---------- live tables -------------------------------------------
    def _update_tables(self,f:PointFrame):
        # Points
        n=len(f.coords)+len(self._fixed_pts); self._tbl_pts.blockSignals(True)
        self._tbl_pts.setRowCount(n); self._tbl_pts.clearContents()
        for i,(x,y) in enumerate(f.coords):
            self._tbl_pts.setItem(i,0,QTableWidgetItem(self._custom_lbl.get(i,f"P{i+1}")))
            self._tbl_pts.setItem(i,1,QTableWidgetItem(f"{x:+.3f}")); self._tbl_pts.setItem(i,2,QTableWidgetItem(f"{y:+.3f}"))
        off=len(f.coords)
        for j,(x,y) in enumerate(self._fixed_pts,start=off):
            self._tbl_pts.setItem(j,0,QTableWidgetItem(self._custom_lbl.get(j,f"P{j+1}")))
            self._tbl_pts.setItem(j,1,QTableWidgetItem(f"{x:+.3f}")); self._tbl_pts.setItem(j,2,QTableWidgetItem(f"{y:+.3f}"))
        self._tbl_pts.blockSignals(False)

        # Extra single row
        cols=len(f.extra)
        if self._tbl_ex.columnCount()<cols:
            self._tbl_ex.setColumnCount(cols)
            hdrs=[f"D{i+1}" for i in range(cols)]
            self._tbl_ex.setHorizontalHeaderLabels(hdrs)
            for h in hdrs[self._ts_list.count():]:
                chk=QListWidgetItem(h); chk.setFlags(chk.flags()|Qt.ItemIsUserCheckable); chk.setCheckState(Qt.Unchecked)
                self._ts_list.addItem(chk)
        if self._tbl_ex.rowCount()!=1: self._tbl_ex.setRowCount(1)
        self._tbl_ex.clearContents()
        for c,tok in enumerate(f.extra): self._tbl_ex.setItem(0,c,QTableWidgetItem(tok))

    # ---------- time-series buffers -----------------------------------
    def _store_ts(self,f:PointFrame,MAX=10000):
        t=f.ts_ms/1000 if getattr(f,"ts_ms",None) not in (None,0) else time.time()
        self._ts_time.append(t)
        for col,tok in enumerate(f.extra):
            try: val=float(tok)
            except ValueError: val=np.nan
            self._ts_data.setdefault(col,[]).append(val)
        for lst in self._ts_data.values():
            if len(lst)<len(self._ts_time): lst.append(np.nan)
        if len(self._ts_time)>MAX:
            self._ts_time=self._ts_time[-MAX:];  # trim all lists equally
            for col in self._ts_data: self._ts_data[col]=self._ts_data[col][-MAX:]

    # ---------- efficient redraw (last 10 s) ---------------------------
    def _refresh_ts_plot(self,*_):
        if not self._ts_time: return
        t_end=self._ts_time[-1]; t_start=t_end-10.0  # last 10 s
        idx=bisect.bisect_left(self._ts_time,t_start)
        t_slice=self._ts_time[idx:]

        checked={i for i in range(self._ts_list.count())
                 if self._ts_list.item(i).checkState()==Qt.Checked and i in self._ts_data}

        # add / update
        for col in checked:
            if col not in self._ts_curves:
                self._ts_curves[col]=self._ts_plot.plot(pen=next(_COLORS),name=self._ts_list.item(col).text())
            scale=self._ts_scale.get(col,1.0)
            data=np.asarray(self._ts_data[col][idx:])*scale
            self._ts_curves[col].setData(t_slice,data)

        # remove unchecked
        for col in list(self._ts_curves):
            if col not in checked:
                self._ts_plot.removeItem(self._ts_curves[col]); del self._ts_curves[col]

    # ---------- edit scale factor -------------------------------------
    def _edit_scale(self,item:QListWidgetItem):
        col=self._ts_list.row(item)
        cur=self._ts_scale.get(col,1.0)
        val,ok=QInputDialog.getDouble(self,"Scale factor",f"Multiply values of '{item.text()}' by:",value=cur,decimals=6)
        if ok:
            self._ts_scale[col]=val; item.setToolTip(f"scale ×{val}"); self._refresh_ts_plot()

    # rename column / combo sync
    def _rename_ex_col(self,col:int):
        old=self._tbl_ex.horizontalHeaderItem(col).text() if self._tbl_ex.horizontalHeaderItem(col) else f"D{col+1}"
        new,ok=QInputDialog.getText(self,"Rename column","New name:",text=old)
        if ok and new.strip():
            self._tbl_ex.setHorizontalHeaderItem(col,QTableWidgetItem(new.strip()))
            if col<self._ts_list.count(): self._ts_list.item(col).setText(new.strip())
            if col in self._ts_curves: self._ts_curves[col].setName(new.strip())

    # label edits
    def _tbl_label_edited(self,item:QTableWidgetItem):
        if item.column()!=0: return
        idx,txt=item.row(),item.text().strip()
        if txt: self._custom_lbl[idx]=txt; self._labels[idx].setText(txt)

    # CSV logging (unchanged) ------------------------------------------
    def _toggle_log(self):
        if self._csv_pts:
            self._csv_pts.close(); self._csv_ex.close(); self._csv_pts=self._csv_ex=None
            self._log_btn.setText("Start Log → CSV"); QMessageBox.information(self,"Log","CSV files closed."); return
        base,ok=QFileDialog.getSaveFileName(self,"CSV base",".","CSV (*.csv)")
        if not ok: return
        b=Path(base).with_suffix("")
        self._csv_pts=open(b.with_name(b.name+"_points.csv"),"a",buffering=1)
        self._csv_ex =open(b.with_name(b.name+"_extra.csv" ),"a",buffering=1)
        if self._csv_pts.tell()==0: self._csv_pts.write("label,x,y\n")
        self._log_btn.setText("Stop Log")

    def _log(self,f:PointFrame):
        if not self._csv_pts: return
        for i,(x,y) in enumerate(f.coords):
            self._csv_pts.write(f"{self._custom_lbl.get(i,f'P{i+1}')},{x},{y}\n")
        base=len(f.coords)
        for j,(x,y) in enumerate(self._fixed_pts,start=base):
            self._csv_pts.write(f"{self._custom_lbl.get(j,f'P{j+1}')},{x},{y}\n")
        self._csv_ex.write(",".join(f.extra)+"\n")

    # ---------- misc ---------------------------------------------------
    def _tx(self):
        s=self._tx_entry.text().strip()
        if s: self._ser.write((s+'\n').encode()); self._last_tx.setText(s); self._tx_entry.clear()

    def _add_conn_pair(self):
        try: a,b=map(int,self._conn_entry.text().strip().split('-',1)); assert a>0 and b>0
        except Exception: return self._flash(self._conn_entry)
        self._conn_pairs.append((a,b)); self._conn_entry.clear()

    def _add_fixed_pt(self):
        try: x,y,*_=map(float,self._fix_entry.text().replace(';',',').split(','))
        except Exception: return self._flash(self._fix_entry)
        self._fixed_pts.append((x,y)); self._fix_entry.clear()

    def _flash(self,w): w.setStyleSheet("background:#ffb"); QTimer.singleShot(600,lambda:w.setStyleSheet(""))
    def closeEvent(self,e):
        if self._csv_pts: self._csv_pts.close(); self._csv_ex.close()
        self._reader.stop(); self._reader.join(timeout=1); self._ser.close(); e.accept()
