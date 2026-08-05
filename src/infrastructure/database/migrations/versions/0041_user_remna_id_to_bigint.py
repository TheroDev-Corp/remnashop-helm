from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "subscriptions",
        "user_remna_id",
        existing_type=sa.UUID(),
        type_=sa.BigInteger(),
        postgresql_using="CASE WHEN user_remna_id::text ~ '^[0-9]+$' THEN user_remna_id::text::bigint ELSE 0 END",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "subscriptions",
        "user_remna_id",
        existing_type=sa.BigInteger(),
        type_=sa.UUID(),
        postgresql_using="user_remna_id::text::uuid",
        existing_nullable=False,
    )
