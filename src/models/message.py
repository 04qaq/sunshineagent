"""Message data model (event-sourced style).

Ownership: Human module. This is a stub until the human implements it.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.database import Base

if TYPE_CHECKING:
    from src.models.session import Session


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    parent_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("messages.id"), nullable=True
    )

    parts: Mapped[str] = mapped_column(Text, default="[]")
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    usage: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    compacted: Mapped[bool] = mapped_column(Boolean, default=False)

    session: Mapped[Session] = relationship(back_populates="messages")
