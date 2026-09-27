from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: Union[str, None] = "0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("bot_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    # History was never recorded, so backfill by the best signal available: every Telegram user
    # has used the bot, except panel imports that still carry the import defaults (name is the
    # telegram id, no username). The first message to the bot overwrites both.
    op.execute(
        """
        UPDATE users SET bot_started_at = created_at
        WHERE telegram_id IS NOT NULL
          AND NOT (
            username IS NULL
            AND name = telegram_id::text
            AND EXISTS (
                SELECT 1 FROM subscriptions s
                WHERE s.user_id = users.id AND s.plan_snapshot->>'name' = 'IMPORTED'
            )
          )
        """
    )


def downgrade() -> None:
    op.drop_column("users", "bot_started_at")
