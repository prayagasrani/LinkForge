import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class ShortURL(TimestampMixin, Base):
    __tablename__ = "short_urls"
    __table_args__ = (Index("ix_short_urls_owner_created_at", "owner_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    short_code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    click_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    owner: Mapped["User"] = relationship(back_populates="short_urls")  # noqa: F821
    click_events: Mapped[list["ClickEvent"]] = relationship(  # noqa: F821
        back_populates="short_url", cascade="all, delete-orphan", passive_deletes=True
    )
