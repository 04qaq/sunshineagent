"""SessionService: CRUD + lifecycle management for sessions and messages."""

import json

from sqlalchemy import delete, select
from ulid import ULID

from src.models.database import Database
from src.models.message import Message
from src.models.session import Session


class SessionService:
    def __init__(self, db: Database):
        self._db = db

    async def create(
        self,
        *,
        parent_id: str | None = None,
        agent: str = "build",
        provider_id: str | None = None,
        model_id: str | None = None,
        title: str | None = None,
    ) -> Session:
        session = Session(
            id=f"ses_{ULID()}",
            parent_id=parent_id,
            agent=agent,
            provider_id=provider_id,
            model_id=model_id,
            title=title,
        )
        async with self._db.session() as db:
            db.add(session)
            await db.commit()
            await db.refresh(session)
        return session

    async def get(self, session_id: str) -> Session | None:
        async with self._db.session() as db:
            return await db.get(Session, session_id)

    async def fork(self, session_id: str) -> Session:
        async with self._db.session() as db:
            original = await db.get(Session, session_id)
            if original is None:
                raise ValueError(f"Session not found: {session_id}")

            new_session = Session(
                id=f"ses_{ULID()}",
                parent_id=original.parent_id,
                agent=original.agent,
                provider_id=original.provider_id,
                model_id=original.model_id,
                title=f"{original.title or ''} (fork)".strip(),
            )
            db.add(new_session)
            await db.flush()

            result = await db.execute(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at)
            )
            for msg in result.scalars():
                new_msg = Message(
                    id=f"msg_{ULID()}",
                    session_id=new_session.id,
                    role=msg.role,
                    parts=msg.parts,
                )
                db.add(new_msg)

            await db.commit()
            await db.refresh(new_session)
        return new_session

    async def remove(self, session_id: str):
        async with self._db.session() as db:
            result = await db.execute(
                select(Session).where(Session.parent_id == session_id)
            )
            for child in result.scalars():
                await self.remove(child.id)

            await db.execute(delete(Message).where(Message.session_id == session_id))
            await db.execute(delete(Session).where(Session.id == session_id))
            await db.commit()

    async def set_status(self, session_id: str, status: str):
        async with self._db.session() as db:
            session = await db.get(Session, session_id)
            if session:
                session.status = status
                await db.commit()

    async def create_message(
        self,
        session_id: str,
        role: str,
        parts: list[dict],
        parent_id: str | None = None,
    ) -> Message:
        msg = Message(
            id=f"msg_{ULID()}",
            session_id=session_id,
            role=role,
            parts=json.dumps(parts),
            parent_id=parent_id,
        )
        async with self._db.session() as db:
            db.add(msg)
            await db.commit()
            await db.refresh(msg)
        return msg

    async def append_part(self, message_id: str, part: dict):
        async with self._db.session() as db:
            msg = await db.get(Message, message_id)
            if msg:
                parts = json.loads(msg.parts)
                parts.append(part)
                msg.parts = json.dumps(parts)
                await db.commit()

    async def update_message(self, message_id: str, **kwargs):
        async with self._db.session() as db:
            msg = await db.get(Message, message_id)
            if msg:
                for key, value in kwargs.items():
                    setattr(msg, key, value)
                await db.commit()

    async def get_messages(
        self, session_id: str, *, include_compacted: bool = False
    ) -> list[Message]:
        async with self._db.session() as db:
            q = (
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at)
            )
            if not include_compacted:
                q = q.where(Message.compacted.is_(False))
            result = await db.execute(q)
            return list(result.scalars())
