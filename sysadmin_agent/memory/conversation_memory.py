import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


class ConversationMemory:
    def __init__(self, storage_dir=".sysadmin-sessions"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def new_session(self, server_host):
        session_id = str(uuid.uuid4())
        session = {
            "session_id": session_id,
            "server_host": server_host,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "messages": [],
        }
        self._save_session(session_id, session)
        return session_id

    def add_message(self, session_id, role, content, metadata=None):
        session = self._load_session(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")

        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            message["metadata"] = metadata

        session["messages"].append(message)
        self._save_session(session_id, session)

    def get_history(self, session_id, last_n=None):
        session = self._load_session(session_id)
        if session is None:
            return []
        messages = session["messages"]
        if last_n is not None:
            return messages[-last_n:]
        return messages

    def get_context_summary(self, session_id, max_tokens=2000):
        session = self._load_session(session_id)
        if session is None:
            return ""

        max_chars = max_tokens * 4
        parts = [f"Server: {session['server_host']}"]

        commands_run = []
        key_findings = []
        fixes_applied = []

        for msg in session["messages"]:
            role = msg["role"]
            content = msg["content"]
            meta = msg.get("metadata", {})

            if role == "command_result":
                cmd = meta.get("command", "unknown")
                exit_code = meta.get("exit_code", None)
                status = "OK" if exit_code == 0 else f"FAIL({exit_code})"
                commands_run.append(f"  {cmd} [{status}]")
            elif role == "agent":
                if len(content) > 200:
                    key_findings.append(f"  {content[:200]}...")
                else:
                    key_findings.append(f"  {content}")
            elif role == "system":
                fixes_applied.append(f"  {content[:150]}")

        if commands_run:
            parts.append("Commands executed:")
            parts.extend(commands_run[-15:])

        if key_findings:
            parts.append("Key findings:")
            parts.extend(key_findings[-10:])

        if fixes_applied:
            parts.append("Actions taken:")
            parts.extend(fixes_applied[-10:])

        summary = "\n".join(parts)

        if len(summary) > max_chars:
            summary = summary[:max_chars - 3] + "..."

        return summary

    def list_sessions(self, server_host=None):
        sessions = []
        for path in self.storage_dir.glob("*.json"):
            try:
                session = json.loads(path.read_text())
                if server_host and session.get("server_host") != server_host:
                    continue
                sessions.append({
                    "session_id": session["session_id"],
                    "server_host": session["server_host"],
                    "created_at": session["created_at"],
                    "message_count": len(session["messages"]),
                })
            except (json.JSONDecodeError, KeyError):
                continue
        return sorted(sessions, key=lambda s: s["created_at"], reverse=True)

    def delete_session(self, session_id):
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def _session_path(self, session_id):
        return self.storage_dir / f"{session_id}.json"

    def _load_session(self, session_id):
        path = self._session_path(session_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return None

    def _save_session(self, session_id, session):
        path = self._session_path(session_id)
        path.write_text(json.dumps(session, indent=2) + "\n")
