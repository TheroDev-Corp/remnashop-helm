from typing import Optional

import aiohttp
from loguru import logger


class AiohttpClient:
    async def get_text(self, url: str, timeout: float = 15.0) -> Optional[str]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    if resp.status != 200:
                        logger.error(f"Failed to fetch '{url}': HTTP {resp.status}")
                        return None
                    return await resp.text()
        except Exception as exc:
            logger.error(f"Failed to fetch '{url}': {exc}")
            return None
