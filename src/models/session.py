"""Session 数据模型 —— 一个会话 = 一次用户对话。

OWNER: Human
SKILL: SQLAlchemy 2.0 async, dataclass, JSON column
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ulid import ULID

from src.models.database import Base

if TYPE_CHECKING:
    from src.models.message import Message


class Session(Base):
    """会话表。支持自引用父子关系，实现 fork 功能。"""

    __tablename__ = "sessions"

    # 主键：ses_ 前缀的 ULID，32 字符定长
    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: f"ses_{ULID()}"
    )

    # 父会话 ID：自引用外键，fork 时使用；首条记录为 NULL
    parent_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("sessions.id"), nullable=True, index=True
    )

    # 使用的 Agent 名称，默认 "build"
    agent: Mapped[str] = mapped_column(String(64), default="build")

    # Provider 和 Model：NULL 表示使用默认值
    provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 会话标题，可由 compaction agent 自动生成
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # 状态：idle / busy / compact
    status: Mapped[str] = mapped_column(String(16), default="idle")

    # 时间戳：lambda 包装确保每次 INSERT 时重新求值
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    # === 关系 ===

    # 一对多：一个 Session 包含多条 Message，级联删除
    messages: Mapped[list[Message]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    # 自引用一对多：父 Session 的子 Session（fork 树）
    children: Mapped[list[Session]] = relationship(
        back_populates="parent",
        remote_side=[id],  # 关键：指定 id 列在"多"侧
        cascade="all, delete",
    )

    # 自引用多对一：当前 Session 的父 Session
    parent: Mapped[Session | None] = relationship(
        back_populates="children", remote_side=[parent_id]
    )
