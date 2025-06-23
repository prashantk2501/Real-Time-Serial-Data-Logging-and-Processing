from dataclasses import dataclass
from typing import List, Tuple, Optional

@dataclass(slots=True)
class PointFrame:
    ts_ms: Optional[int]                   # still available if you ever need it
    coords: List[Tuple[float, float]]      # (x, y) pairs only
    extra:  List[str]                      # tokens after an optional “D,”
