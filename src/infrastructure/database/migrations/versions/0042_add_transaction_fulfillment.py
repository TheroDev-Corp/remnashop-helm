from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: Union[str, None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("fulfillment_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "transactions",
        sa.Column("fulfillment_next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("fulfillment_error", sa.String(), nullable=True),
    )
    # Historic paid rows were fulfilled by the old synchronous flow (or already handled by hand):
    # never let the reconciliation task grant them again.
    op.execute(
        "UPDATE transactions SET fulfilled_at = updated_at "
        "WHERE status IN ('COMPLETED', 'REFUNDED')"
    )


def downgrade() -> None:
    op.drop_column("transactions", "fulfillment_error")
    op.drop_column("transactions", "fulfillment_next_retry_at")
    op.drop_column("transactions", "fulfillment_attempts")
    op.drop_column("transactions", "fulfilled_at")
