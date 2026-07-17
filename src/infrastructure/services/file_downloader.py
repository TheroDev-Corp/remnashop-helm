from pathlib import Path

from aiogram import Bot
from loguru import logger


class AiogramFileDownloader:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def download_to_path(self, file_id: str, destination: Path) -> None:
        file = await self.bot.get_file(file_id)
        if not file.file_path:
            raise ValueError(f"File path not found for file_id '{file_id}'")
        await self.bot.download_file(file.file_path, destination=destination)
        logger.debug(f"Downloaded file '{file_id}' to '{destination}'")
