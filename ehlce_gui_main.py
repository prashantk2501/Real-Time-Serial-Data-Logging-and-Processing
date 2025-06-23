# ehlce_gui_main.py
import sys, serial.tools.list_ports, pyqtgraph as pg
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QMessageBox
)
from PySide6.QtCore import Qt
from gui import SerialGui


# ────────────────── small modal dialog ────────────────────────────────
class ConnectDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Connect to serial port")
        self._port_cb  = QComboBox(editable=True)
        self._baud_edit= QLineEdit("115200")
        self._btn_ok   = QPushButton("Connect"); self._btn_ok.clicked.connect(self.accept)
        self._btn_ref  = QPushButton("Refresh"); self._btn_ref.clicked.connect(self._fill_ports)
        self._btn_cancel=QPushButton("Cancel");  self._btn_cancel.clicked.connect(self.reject)

        lay = QVBoxLayout(self)
        port_row=QHBoxLayout(); port_row.addWidget(QLabel("Port:")); port_row.addWidget(self._port_cb); port_row.addWidget(self._btn_ref)
        baud_row=QHBoxLayout(); baud_row.addWidget(QLabel("Baud:")); baud_row.addWidget(self._baud_edit)
        btn_row =QHBoxLayout(); btn_row.addStretch(); btn_row.addWidget(self._btn_ok); btn_row.addWidget(self._btn_cancel)
        lay.addLayout(port_row); lay.addLayout(baud_row); lay.addLayout(btn_row)
        self._fill_ports()

    # fill the combo with current ports
    def _fill_ports(self):
        self._port_cb.clear()
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self._port_cb.addItems(ports)
        if not ports:
            QMessageBox.warning(self,"No ports","No serial ports detected — plug in your device and click Refresh.")

    # helpers the caller will read
    @property
    def selected_port(self):  return self._port_cb.currentText().strip()
    @property
    def selected_baud(self):
        try: return int(self._baud_edit.text())
        except ValueError: return None


# ────────────────── entry point ───────────────────────────────────────
def main(argv=None):
    argv = sys.argv if argv is None else argv
    app  = QApplication(argv)
    pg.setConfigOptions(antialias=True)

    # 1) show connect dialog
    dlg = ConnectDialog()
    if dlg.exec() != QDialog.Accepted:
        sys.exit(0)                       # user hit Cancel / closed

    port, baud = dlg.selected_port, dlg.selected_baud
    if not port or baud is None:
        QMessageBox.critical(None,"Error","Invalid port or baud rate."); sys.exit(1)

    # 2) launch the main GUI
    gui = SerialGui(port, baud); gui.resize(800, 550); gui.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
