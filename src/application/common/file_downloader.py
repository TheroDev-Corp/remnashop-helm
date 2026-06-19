from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class FileDownloader(Protocol):
    async def download_to_path(self, file_id: str, destination: Path) -> None: ...
