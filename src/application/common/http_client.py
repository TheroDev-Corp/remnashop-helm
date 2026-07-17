from typing import Optional, Protocol


class HttpClient(Protocol):
    async def get_text(self, url: str, timeout: float = 15.0) -> Optional[str]: ...
