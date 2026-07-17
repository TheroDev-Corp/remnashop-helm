from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Refreshes the database collation version after a glibc upgrade caused a
    # collation version mismatch (Postgres warns and may reject index operations).
    # Safe to execute without superuser privileges on PG 15+.
    bind = op.get_bind()
    db_name = bind.execute(sa.text("SELECT current_database()")).scalar()
    bind.execute(sa.text(f'ALTER DATABASE "{db_name}" REFRESH COLLATION VERSION'))


def downgrade() -> None:
    pass
