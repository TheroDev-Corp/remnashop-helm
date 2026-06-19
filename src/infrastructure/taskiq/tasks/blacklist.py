from dishka.integrations.taskiq import FromDishka, inject

from src.application.use_cases.blacklist.commands.sources import SyncBlacklistSources
from src.infrastructure.taskiq.broker import broker


@broker.task(schedule=[{"cron": "0 */6 * * *"}])
@inject(patch_module=True)
async def auto_blacklist_sync_task(
    sync_blacklist_sources: FromDishka[SyncBlacklistSources],
) -> None:
    await sync_blacklist_sources.system()
