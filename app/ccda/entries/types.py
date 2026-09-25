from dataclasses import dataclass
from typing import Any, List, Optional

Cell = str


Row = List[Cell]


@dataclass(frozen=True)
class EntryWithRow:
    entry: Any  # C-CDA entry section
    row: Optional[Row]  # row data for summary table in section
