import threading, queue, serial
from typing import Union, List, Tuple
from data import PointFrame


class SerialReader(threading.Thread):

    def __init__(self, ser: serial.Serial,
                 out_q: "queue.Queue[Union[str, PointFrame]]"):
        super().__init__(daemon=True)
        self._ser, self._q = ser, out_q
        self._stop = threading.Event()

    # ------------------------------------------------------------------ #
    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ #
    def run(self) -> None:
        buf = bytearray()
        while not self._stop.is_set():
            buf.extend(self._ser.read(self._ser.in_waiting or 1))
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                txt = line.decode("utf-8", errors="replace").strip()
                self._q.put(self._parse(txt))

    # ------------------------------------------------------------------ #
    def _parse(self, txt: str) -> Union[str, PointFrame]:
        """
        Frame format (no Z)  →  P,x1,y1,x2,y2,…[,D,foo,bar]
        Any other line is passed through verbatim.
        """
        if not txt.startswith("P,"):
            return txt

        tokens = txt.rstrip(",").split(",")[1:]   # drop leading 'P'

        if "D" in tokens:
            d_idx          = tokens.index("D")
            coord_tokens   = tokens[:d_idx]
            extra_tokens   = tokens[d_idx + 1 :]
        else:
            coord_tokens, extra_tokens = tokens, []

        # convert coord_tokens to floats
        floats: List[float] = []
        for tok in coord_tokens:
            try:
                floats.append(float(tok))
            except ValueError:
                return f"# non-float '{tok}' in: {txt}"

        if len(floats) < 2:
            return f"# no coordinate data: {txt}"

        # Pack into (x, y) pairs  — ignore a dangling single float if present
        coords: List[Tuple[float, float]] = [
            (floats[i], floats[i + 1])
            for i in range(0, len(floats) - len(floats) % 2, 2)
        ]

        return PointFrame(ts_ms=None, coords=coords, extra=extra_tokens)
