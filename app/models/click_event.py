import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ClickEvent(Base):
    __tablename__ = "click_events"
    __table_args__ = (Index("ix_click_events_url_clicked_at", "short_url_id", "clicked_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    short_url_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("short_urls.id", ondelete="CASCADE"), nullable=False
    )
    clicked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    referrer: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    short_url: Mapped["ShortURL"] = relationship(back_populates="click_events")  # noqa: F821
