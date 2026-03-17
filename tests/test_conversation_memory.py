"""Tests for the ConversationMemory module."""

import uuid
import pytest

from sysadmin_agent.memory.conversation_memory import ConversationMemory


class TestNewSession:
    def test_new_session_returns_uuid(self, tmp_path):
        mem = ConversationMemory(storage_dir=str(tmp_path / "sessions"))

        session_id = mem.new_session("web1.example.com")

        # Should be a valid UUID string
        parsed = uuid.UUID(session_id)
        assert str(parsed) == session_id


class TestAddAndGetMessages:
    def test_add_and_get_messages(self, tmp_path):
        mem = ConversationMemory(storage_dir=str(tmp_path / "sessions"))
        sid = mem.new_session("web1.example.com")

        mem.add_message(sid, "user", "Check disk space")
        mem.add_message(sid, "agent", "Disk usage is at 75%")
        mem.add_message(
            sid,
            "command_result",
            "df -h output",
            metadata={"command": "df -h", "exit_code": 0},
        )

        history = mem.get_history(sid)
        assert len(history) == 3
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Check disk space"
        assert history[1]["role"] == "agent"
        assert history[2]["role"] == "command_result"
        assert history[2]["metadata"]["command"] == "df -h"


class TestGetHistoryLastN:
    def test_get_history_last_n(self, tmp_path):
        mem = ConversationMemory(storage_dir=str(tmp_path / "sessions"))
        sid = mem.new_session("db1.example.com")

        for i in range(10):
            mem.add_message(sid, "user", f"Message {i}")

        last_3 = mem.get_history(sid, last_n=3)
        assert len(last_3) == 3
        assert last_3[0]["content"] == "Message 7"
        assert last_3[1]["content"] == "Message 8"
        assert last_3[2]["content"] == "Message 9"


class TestContextSummary:
    def test_context_summary_truncates(self, tmp_path):
        mem = ConversationMemory(storage_dir=str(tmp_path / "sessions"))
        sid = mem.new_session("big-server.example.com")

        # Add many messages to exceed the token limit
        for i in range(200):
            mem.add_message(
                sid,
                "command_result",
                f"Output of command {i}" * 20,
                metadata={"command": f"long-command-{i}", "exit_code": 0},
            )

        summary = mem.get_context_summary(sid, max_tokens=100)
        # max_tokens=100 -> max_chars=400
        assert len(summary) <= 400 + 3  # allow for "..."
        assert summary.endswith("...")


class TestListSessions:
    def test_list_sessions(self, tmp_path):
        mem = ConversationMemory(storage_dir=str(tmp_path / "sessions"))

        sid1 = mem.new_session("web1.example.com")
        sid2 = mem.new_session("db1.example.com")
        sid3 = mem.new_session("web1.example.com")

        sessions = mem.list_sessions()
        assert len(sessions) == 3
        session_ids = {s["session_id"] for s in sessions}
        assert {sid1, sid2, sid3} == session_ids

        # Each session info should have expected keys
        for s in sessions:
            assert "session_id" in s
            assert "server_host" in s
            assert "created_at" in s
            assert "message_count" in s

    def test_list_sessions_filtered_by_host(self, tmp_path):
        mem = ConversationMemory(storage_dir=str(tmp_path / "sessions"))

        mem.new_session("web1.example.com")
        mem.new_session("db1.example.com")
        mem.new_session("web1.example.com")

        web_sessions = mem.list_sessions(server_host="web1.example.com")
        assert len(web_sessions) == 2
        for s in web_sessions:
            assert s["server_host"] == "web1.example.com"

        db_sessions = mem.list_sessions(server_host="db1.example.com")
        assert len(db_sessions) == 1


class TestDeleteSession:
    def test_delete_session(self, tmp_path):
        mem = ConversationMemory(storage_dir=str(tmp_path / "sessions"))
        sid = mem.new_session("web1.example.com")

        mem.add_message(sid, "user", "hello")
        assert len(mem.get_history(sid)) == 1

        result = mem.delete_session(sid)
        assert result is True
        assert mem.get_history(sid) == []


class TestPersistence:
    def test_persistence(self, tmp_path):
        storage = str(tmp_path / "sessions")

        mem1 = ConversationMemory(storage_dir=storage)
        sid = mem1.new_session("persistent.example.com")
        mem1.add_message(sid, "user", "Remember this")
        mem1.add_message(sid, "agent", "I will remember")

        # Create a completely new instance pointing at the same directory
        mem2 = ConversationMemory(storage_dir=storage)

        history = mem2.get_history(sid)
        assert len(history) == 2
        assert history[0]["content"] == "Remember this"
        assert history[1]["content"] == "I will remember"

        sessions = mem2.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["server_host"] == "persistent.example.com"
