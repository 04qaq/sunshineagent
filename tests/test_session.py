"""Tests for Session model and service."""

import json

import pytest

from src.models.database import Database
from src.session.service import SessionService


@pytest.fixture
async def db():
    d = Database(db_path=":memory:")
    await d.init()
    yield d
    await d.close()


@pytest.fixture
def session_service(db):
    return SessionService(db)


class TestSessionCRUD:
    async def test_create_session(self, session_service: SessionService):
        session = await session_service.create(agent="build")
        assert session.id.startswith("ses_")
        assert session.agent == "build"
        assert session.status == "idle"

    async def test_get_session(self, session_service: SessionService):
        session = await session_service.create(agent="test_agent")
        fetched = await session_service.get(session.id)
        assert fetched is not None
        assert fetched.id == session.id

    async def test_fork_session(self, session_service: SessionService):
        original = await session_service.create(agent="build", title="Original")
        await session_service.create_message(
            original.id, "user", [{"type": "text", "text": "Hello"}]
        )

        forked = await session_service.fork(original.id)
        assert forked.id != original.id
        assert forked.title == "Original (fork)"

        messages = await session_service.get_messages(forked.id)
        assert len(messages) == 1
        assert messages[0].role == "user"

    async def test_create_message(self, session_service: SessionService):
        session = await session_service.create()
        msg = await session_service.create_message(
            session.id, "user", [{"type": "text", "text": "test"}]
        )
        assert msg.role == "user"
        parts = json.loads(msg.parts)
        assert parts[0]["text"] == "test"

    async def test_get_messages(self, session_service: SessionService):
        session = await session_service.create()
        await session_service.create_message(session.id, "user", [{"type": "text", "text": "msg1"}])
        msg2 = [{"type": "text", "text": "msg2"}]
        await session_service.create_message(
            session.id, "assistant", msg2
        )

        messages = await session_service.get_messages(session.id)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"


class TestCoordinator:
    async def test_run_exclusive(self):
        from src.session.coordinator import RunCoordinator

        coordinator = RunCoordinator()
        results = []

        async def task(val):
            results.append(val)
            return val

        r = await coordinator.run_exclusive("s1", task(1))
        assert r == 1
        assert results == [1]
