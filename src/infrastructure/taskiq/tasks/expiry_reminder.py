from dishka.integrations.taskiq import FromDishka, inject

from src.application.services import ExpiryReminderService
from src.infrastructure.taskiq.broker import broker


@broker.task(schedule=[{"cron": "15 * * * *"}])
@inject(patch_module=True)
async def expiry_reminder_task(expiry_reminder: FromDishka[ExpiryReminderService]) -> None:
    # Fallback for panels that never send `user.expires_in_*` webhooks; duplicates are deduped.
    await expiry_reminder.check_expiring()
