from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.core.enums import OAuthProvider

from .base import BaseSql
from .timestamp import TimestampMixin


class UserOAuthProvider(BaseSql, TimestampMixin):
    __tablename__ = "user_oauth_providers"

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_oauth_providers_user_provider"),
        UniqueConstraint("provider", "provider_id", name="uq_user_oauth_providers_provider_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    provider: Mapped[OAuthProvider] = mapped_column(String(32))
    provider_id: Mapped[str] = mapped_column(String(255))
