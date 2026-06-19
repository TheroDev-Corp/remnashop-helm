from pathlib import Path
from typing import Protocol


class XuiDbReader(Protocol):
    async def read_inbounds(self, db_path: Path) -> list[tuple[int, str]]: ...
