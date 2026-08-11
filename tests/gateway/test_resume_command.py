"""Tests for /resume gateway slash command.

Tests the _handle_resume_command handler (switch to a previously-named session)
across gateway messenger platforms.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource, build_session_key


def _make_event(text="/resume", platform=Platform.TELEGRAM,
                user_id="12345", chat_id="67890"):
    """Build a MessageEvent for testing."""
    source = SessionSource(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        user_name="testuser",
    )
    return MessageEvent(text=text, source=source)


def _session_key_for_event(event):
    """Get the session key that build_session_key produces for an event."""
    return build_session_key(event.source)


def _make_runner(session_db=None, current_session_id="current_session_001",
                 event=None):
    """Create a bare GatewayRunner with a mock session_store and optional session_db."""
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner.config = SimpleNamespace(platforms={})
    runner._voice_mode = {}
    # Gateway holds the async facade; the slash handlers await it.
    if session_db is not None:
        from hermes_state import AsyncSessionDB
        session_db = AsyncSessionDB(session_db)
    runner._session_db = session_db
    runner._running_agents = {}
    runner._is_user_authorized = lambda _source: True

    # Compute the real session key if an event is provided
    session_key = build_session_key(event.source) if event else "agent:main:telegram:dm"

    # Mock session_store that returns a session entry with a known session_id
    mock_session_entry = MagicMock()
    mock_session_entry.session_id = current_session_id
    mock_session_entry.session_key = session_key
    mock_store = MagicMock()
    mock_store.get_or_create_session.return_value = mock_session_entry
    mock_store.load_transcript.return_value = []
    mock_store.switch_session.return_value = mock_session_entry
    runner.session_store = mock_store

    return runner


# ---------------------------------------------------------------------------
# _handle_resume_command
# ---------------------------------------------------------------------------


class TestHandleResumeCommand:
    """Tests for GatewayRunner._handle_resume_command."""

    @pytest.mark.asyncio
    async def test_no_session_db(self):
        """Returns error when session database is unavailable."""
        runner = _make_runner(session_db=None)
        event = _make_event(text="/resume My Project")
        result = await runner._handle_resume_command(event)
        assert "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_list_named_sessions_when_no_arg(self, tmp_path):
        """With no argument, lists recently titled sessions."""
        from hermes_state import SessionDB
        db = SessionDB(db_path=tmp_path / "state.db")
        event = _make_event(text="/resume")
        lane_key = _session_key_for_event(event)
        db.create_session(
            "sess_001", "telegram", session_key=lane_key,
            user_id="12345", chat_id="67890",
        )
        db.create_session(
            "sess_002", "telegram", session_key=lane_key,
            user_id="12345", chat_id="67890",
        )
        db.set_session_title("sess_001", "Research")
        db.set_session_title("sess_002", "Coding")

        runner = _make_runner(session_db=db, event=event)
        result = await runner._handle_resume_command(event)
        assert "Research" in result
        assert "Coding" in result
        assert "Named Sessions" in result
        assert "1." in result
        assert "2." in result
        assert "/resume 1" in result
        db.close()


    @pytest.mark.asyncio
    async def test_resume_clears_session_model_overrides(self, tmp_path):
        """Resume must not carry a previous session's /model override into the
        restored conversation, while leaving other chats' overrides intact (#10702)."""
        from hermes_state import SessionDB
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("old_session_abc", "telegram", user_id="12345", chat_id="67890")
        db.set_session_title("old_session_abc", "My Project")
        db.create_session("current_session_001", "telegram", user_id="12345", chat_id="67890")

        event = _make_event(text="/resume My Project")
        runner = _make_runner(session_db=db, current_session_id="current_session_001",
                              event=event)
        key = _session_key_for_event(event)
        runner._session_model_overrides = {
            key: {"model": "gpt-5", "provider": "openai"},
            "agent:main:telegram:dm:other": {"model": "keep-me"},
        }
        runner._pending_model_notes = {
            key: "[Note: switched to gpt-5]",
            "agent:main:telegram:dm:other": "[Note: keep-me]",
        }

        result = await runner._handle_resume_command(event)

        assert "Resumed" in result
        # The resumed chat's override + pending note are cleared...
        assert key not in runner._session_model_overrides
        assert key not in runner._pending_model_notes
        # ...but an unrelated chat's state is untouched.
        assert runner._session_model_overrides["agent:main:telegram:dm:other"] == {"model": "keep-me"}
        assert runner._pending_model_notes["agent:main:telegram:dm:other"] == "[Note: keep-me]"
        db.close()

    @pytest.mark.asyncio
    async def test_resume_clears_last_resolved_model(self, tmp_path):
        """Resume must also clear the resumed chat's cached last-resolved
        model, so the restored conversation re-resolves from current config
        instead of a value cached before the switch (mirrors /new and the
        compression-exhausted auto-reset, #58403), while leaving other
        chats' cache entries intact."""
        from hermes_state import SessionDB
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("old_session_abc", "telegram", user_id="12345", chat_id="67890")
        db.set_session_title("old_session_abc", "My Project")
        db.create_session("current_session_001", "telegram", user_id="12345", chat_id="67890")

        event = _make_event(text="/resume My Project")
        runner = _make_runner(session_db=db, current_session_id="current_session_001",
                              event=event)
        key = _session_key_for_event(event)
        runner._last_resolved_model = {
            key: "gpt-5",
            "agent:main:telegram:dm:other": "keep-me",
        }

        result = await runner._handle_resume_command(event)

        assert "Resumed" in result
        assert key not in runner._last_resolved_model
        assert runner._last_resolved_model["agent:main:telegram:dm:other"] == "keep-me"
        db.close()


    @pytest.mark.asyncio
    async def test_resume_follows_compression_continuation(self, tmp_path):
        """Gateway /resume should reopen the live descendant after compression."""
        from hermes_state import SessionDB

        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("compressed_root", "telegram", user_id="12345", chat_id="67890")
        db.set_session_title("compressed_root", "Compressed Work")
        db.end_session("compressed_root", "compression")
        db.create_session("compressed_child", "telegram", user_id="12345", chat_id="67890", parent_session_id="compressed_root")
        db.append_message("compressed_child", "user", "hello from continuation")
        db.create_session("current_session_001", "telegram", user_id="12345", chat_id="67890")

        event = _make_event(text="/resume Compressed Work")
        runner = _make_runner(
            session_db=db,
            current_session_id="current_session_001",
            event=event,
        )
        runner.session_store.load_transcript.side_effect = (
            lambda session_id: [{"role": "user", "content": "hello from continuation"}]
            if session_id == "compressed_child"
            else []
        )

        result = await runner._handle_resume_command(event)

        assert "Resumed session" in result
        assert "(1 message)" in result
        call_args = runner.session_store.switch_session.call_args
        assert call_args[0][1] == "compressed_child"
        runner.session_store.load_transcript.assert_called_with("compressed_child")
        db.close()


    @pytest.mark.asyncio
    async def test_resume_evicts_cached_agent(self, tmp_path):
        """Gateway /resume evicts the cached AIAgent so the next message
        rebuilds with the correct session_id end-to-end — mirrors /branch
        and /reset. Without this, the cached agent's memory provider keeps
        writing into the wrong session. See #6672.
        """
        import threading
        from hermes_state import SessionDB
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("old_session", "telegram", user_id="12345", chat_id="67890")
        db.set_session_title("old_session", "Old Work")
        db.create_session("current_session_001", "telegram", user_id="12345", chat_id="67890")

        event = _make_event(text="/resume Old Work")
        runner = _make_runner(session_db=db, current_session_id="current_session_001",
                              event=event)
        # Seed the cache with a fake agent
        real_key = _session_key_for_event(event)
        runner._agent_cache = {real_key: (MagicMock(), object())}
        runner._agent_cache_lock = threading.RLock()

        await runner._handle_resume_command(event)

        assert real_key not in runner._agent_cache
        db.close()







class TestHandleSessionsCommand:
    """Tests for GatewayRunner._handle_sessions_command."""


    @pytest.mark.asyncio
    async def test_sessions_admin_all_preserves_cross_origin_widening(self, tmp_path):
        from hermes_state import SessionDB

        db = SessionDB(db_path=tmp_path / "state.db")
        event = _make_event(text="/sessions all")
        lane_key = _session_key_for_event(event)
        db.create_session(
            "tg_named", "telegram", session_key=lane_key,
            user_id="12345", chat_id="67890",
        )
        db.set_session_title("tg_named", "Telegram Work")
        db.create_session(
            "discord_named", "discord",
            session_key="agent:main:discord:dm:other",
            user_id="other-user", chat_id="other",
        )
        db.set_session_title("discord_named", "Discord Work")

        runner = _make_runner(session_db=db, event=event)
        runner._resume_caller_is_admin = lambda _source: True
        result = await runner._handle_sessions_command(event)

        assert "Telegram Work" in result
        assert "Discord Work" in result
        db.close()





    @pytest.mark.asyncio
    async def test_sessions_search_finds_older_titled_session(self, tmp_path):
        """`/sessions search <query>` matches titles beyond the recent-10 list
        and orders by activity, keeping the caller's own scope."""
        from hermes_state import SessionDB
        db = SessionDB(db_path=tmp_path / "state.db")
        event = _make_event(text="/sessions search an94")
        lane_key = _session_key_for_event(event)
        # Bury the target under newer sessions so a plain listing misses it.
        db.create_session(
            "target_an94", "telegram", session_key=lane_key,
            user_id="12345", chat_id="67890",
        )
        db.set_session_title("target_an94", "AN-94 Prestige Barrel Build #2")
        for i in range(12):
            sid = f"filler_{i}"
            db.create_session(
                sid, "telegram", session_key=lane_key,
                user_id="12345", chat_id="67890",
            )
            db.set_session_title(sid, f"Filler {i}")

        runner = _make_runner(session_db=db, event=event)
        result = await runner._handle_sessions_command(event)

        assert "AN-94 Prestige Barrel Build #2" in result
        assert "target_an94" in result
        assert "Filler" not in result
        db.close()





    @pytest.mark.asyncio
    async def test_gateway_dispatches_sessions_command(self, tmp_path):
        from hermes_state import SessionDB
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("tg_session", "telegram", user_id="12345", chat_id="67890")
        db.set_session_title("tg_session", "Telegram Work")

        event = _make_event(text="/sessions")
        runner = _make_runner(session_db=db, event=event)
        runner._handle_sessions_command = AsyncMock(return_value="sessions output")

        result = await runner._handle_message(event)

        assert result == "sessions output"
        runner._handle_sessions_command.assert_awaited_once_with(event)
        db.close()






class TestSameMatrixRoomThreadScoping:
    """Matrix `/resume` (direct and listing) scopes by room AND thread: a live
    session in another thread of the same room is a different session
    (build_session_key appends thread_id), so a caller in thread A must not
    resume/enumerate a target whose origin is in thread B. Non-threaded rooms
    keep room-level sharing unchanged."""

    @staticmethod
    def _msrc(chat_id="!room-a:hs", user_id="@alice:hs", thread_id=None):
        return SessionSource(platform=Platform.MATRIX, chat_id=chat_id,
                             chat_type="group", user_id=user_id, thread_id=thread_id)

    def test_same_room_no_thread_still_shared(self):
        runner = _make_runner()
        a = self._msrc(user_id="@alice:hs")
        b = self._msrc(user_id="@bob:hs")
        assert runner._same_matrix_room(a, b) is True


    def test_cross_thread_same_room_blocked(self):
        """The reviewer's probe: caller in thread-a, target origin in thread-b
        of the same room → must not match."""
        runner = _make_runner()
        caller = self._msrc(thread_id="thread-a")
        victim_origin = self._msrc(thread_id="thread-b")
        assert runner._same_matrix_room(caller, victim_origin) is False


