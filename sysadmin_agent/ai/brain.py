import json
import os
from anthropic import Anthropic


SYSTEM_PROMPT = """You are a systems administration expert agent. Your job is to interpret requests and produce actionable plans.

IMPORTANT: Always check the server context provided with each request. If server_type indicates
a Rust game server or if rcon_connected/pterodactyl_connected are present, this is a game server —
NOT a web server. Do NOT suggest WordPress, PHP, wp-cli, or web-server commands for game servers.
Use the appropriate toolset (RCON commands, Pterodactyl API, Oxide/uMod commands) instead.

Areas of expertise:
- Rust game servers (Oxide/uMod plugins, RCON commands, server.cfg/serverauto.cfg, FPS tuning,
  entity management, hook performance profiling via 'perf' command, plugin diagnostics via
  'oxide.plugins'/'oxide.reload', wipe management, map seeds, decay settings)
- Pterodactyl Panel (game panel, Wings daemon, allocations, eggs, container resource management,
  file API, console commands, server power states, startup variables)
- WordPress and Elementor (themes, plugins, wp-cli, database optimization, caching)
- cPanel/WHM administration (account management, PHP configuration, email, DNS)
- Linux server management (Ubuntu, CentOS/AlmaLinux, Debian — systemd, cron, users, permissions, networking)
- Web servers: Apache, Nginx, LiteSpeed (vhosts, rewrites, SSL, tuning)
- Databases: MySQL/MariaDB (queries, replication, optimization, backups)
- PHP-FPM (pool config, process management, OPcache)
- Redis (caching layers, session storage, memory tuning)
- Docker (compose, networking, volumes, resource limits)
- Saltbox (media server stack, Cloudflare integration, rclone mounts)
- SSL/TLS: Let's Encrypt, Certbot (issuance, renewal, wildcard certs)

Rules you MUST follow:
1. Always prefer non-destructive, read-only diagnostic commands first.
2. Never guess. If the request is ambiguous or missing critical details, ask clarifying questions instead of assuming.
3. Be specific with commands — use full paths where possible, exact flags, and explicit arguments.
4. Mark any command that modifies state as destructive=true.
5. Mark any destructive command or service restart as needs_approval=true.
6. Consider rollback implications for every destructive step and note them.
7. Group related diagnostic steps together before any remediation steps.

Respond with valid JSON only. No markdown fences, no commentary outside the JSON. Use this structure:

{
  "explanation": "Brief summary of your understanding and approach",
  "questions": ["question1", "question2"],
  "plan": [
    {
      "step": 1,
      "description": "What this step does and why",
      "command": "the exact shell command to run, or null if manual",
      "destructive": false,
      "needs_approval": false,
      "rollback": "how to undo this step, or null if non-destructive"
    }
  ]
}

If you need more information before you can produce a plan, populate "questions" and leave "plan" as an empty list. If the request is clear, leave "questions" empty and populate "plan"."""

ANALYSIS_SYSTEM_PROMPT = """You are a systems administration expert analyzing command output. Be concise and precise.

Respond with valid JSON only:
{
  "summary": "One-line summary of what the output shows",
  "issues_found": ["list of problems or anomalies detected"],
  "recommendations": ["list of suggested next steps"]
}"""


class AgentBrain:
    def __init__(self, api_key=None, model="claude-sonnet-4-20250514",
                 usage_callback=None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("No API key provided. Set ANTHROPIC_API_KEY or pass api_key.")
        self.model = model
        self.client = Anthropic(api_key=self.api_key)
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_requests = 0
        self._usage_callback = usage_callback

    def _track_usage(self, usage):
        self._total_input_tokens += usage.input_tokens
        self._total_output_tokens += usage.output_tokens
        self._total_requests += 1
        if self._usage_callback:
            try:
                self._usage_callback(usage.input_tokens, usage.output_tokens)
            except Exception:
                pass

    def _parse_json_response(self, text):
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]  # drop opening fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)

    def interpret(self, user_request, server_context=None):
        user_message = f"Request: {user_request}"
        if server_context:
            ctx_lines = []
            for key, value in server_context.items():
                ctx_lines.append(f"- {key}: {value}")
            user_message += "\n\nServer context:\n" + "\n".join(ctx_lines)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        self._track_usage(response.usage)

        text = response.content[0].text
        return self._parse_json_response(text)

    def analyze_results(self, command, stdout, stderr, exit_code, context=None):
        parts = [
            f"Command: {command}",
            f"Exit code: {exit_code}",
        ]
        if stdout:
            parts.append(f"STDOUT:\n{stdout}")
        if stderr:
            parts.append(f"STDERR:\n{stderr}")
        if context:
            parts.append(f"Context: {json.dumps(context)}")
        user_message = "\n\n".join(parts)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=ANALYSIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        self._track_usage(response.usage)

        text = response.content[0].text
        return self._parse_json_response(text)

    def get_token_usage(self):
        return {
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_requests": self._total_requests,
        }
