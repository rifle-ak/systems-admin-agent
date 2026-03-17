import sys
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text


class ApprovalManager:
    def __init__(self, auto_approve=False, prompt_fn=None):
        self.auto_approve = auto_approve
        self._prompt_fn = prompt_fn
        self._history = []
        self._console = Console(stderr=True)

    def request_approval(self, action):
        command = action.get("command", "")
        description = action.get("description", "")
        destructive = action.get("destructive", False)
        snapshot_id = action.get("snapshot_id")

        if self.auto_approve:
            self._record(action, "auto_approved")
            return True

        if not self._prompt_fn and not sys.stdin.isatty():
            self._record(action, "denied_non_interactive")
            return False

        style = "bold red" if destructive else "bold yellow"
        label = "[DESTRUCTIVE] " if destructive else ""

        body = Text()
        body.append(f"Command:     {command}\n")
        body.append(f"Description: {description}\n")
        if snapshot_id:
            body.append(f"Snapshot:    {snapshot_id}\n")

        self._console.print(Panel(
            body,
            title=f"{label}Action Requires Approval",
            border_style=style,
        ))

        if self._prompt_fn:
            answer = self._prompt_fn(action)
        else:
            answer = Prompt.ask(
                "Do you approve this action? [y/N]",
                default="N",
                console=self._console,
            )

        approved = answer.strip().lower() in ("y", "yes")
        self._record(action, "approved" if approved else "denied")
        return approved

    def get_history(self):
        return list(self._history)

    def get_stats(self):
        total = len(self._history)
        approved = sum(
            1 for h in self._history if h["decision"] in ("approved", "auto_approved")
        )
        denied = sum(
            1 for h in self._history if h["decision"] in ("denied", "denied_non_interactive")
        )
        errors = sum(1 for h in self._history if h["decision"] == "error")
        return {
            "total": total,
            "approved": approved,
            "denied": denied,
            "errors": errors,
        }

    def _record(self, action, decision):
        self._history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "decision": decision,
        })
