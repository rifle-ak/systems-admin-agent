"""CLI layer for the systems administration agent using Click and Rich."""

import json
import os
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from sysadmin_agent.connection import SSHManager
from sysadmin_agent.discovery import OSDetector, AppDiscovery
from sysadmin_agent.diagnostics import DiagnosticEngine
from sysadmin_agent.approval import ApprovalManager
from sysadmin_agent.rollback import RollbackManager
from sysadmin_agent.ai import AgentBrain
from sysadmin_agent.knowledge import DocFetcher
from sysadmin_agent.utils import (
    format_os_info,
    format_app_discovery,
    format_diagnostics,
    format_snapshots,
)

console = Console()
error_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _friendly_ssh_error(exc):
    """Convert paramiko / connection exceptions into human-readable messages."""
    import paramiko

    if isinstance(exc, paramiko.AuthenticationException):
        return "Authentication failed. Check your username, password, or SSH key."
    if isinstance(exc, paramiko.SSHException):
        return f"SSH error: {exc}"
    if isinstance(exc, TimeoutError):
        return "Connection timed out. Verify the host address and port are correct."
    if isinstance(exc, OSError):
        return f"Connection error: {exc}"
    return str(exc)


def _friendly_api_error(exc):
    """Convert Anthropic SDK exceptions into human-readable messages."""
    try:
        import anthropic

        if isinstance(exc, anthropic.AuthenticationError):
            return "API authentication failed. Check your ANTHROPIC_API_KEY."
        if isinstance(exc, anthropic.RateLimitError):
            return "API rate limit exceeded. Please wait a moment and try again."
        if isinstance(exc, anthropic.APIConnectionError):
            return "Could not reach the Anthropic API. Check your network connection."
        if isinstance(exc, anthropic.APIStatusError):
            return f"API error (HTTP {exc.status_code}): {exc.message}"
    except ImportError:
        pass
    return f"API error: {exc}"


def _is_api_error(exc):
    """Return True if *exc* is an Anthropic SDK error."""
    try:
        import anthropic
        return isinstance(exc, (anthropic.APIError, anthropic.APIConnectionError))
    except ImportError:
        return False


def _build_ssh(ctx):
    """Construct an SSHManager from the CLI context."""
    return SSHManager(
        host=ctx.obj["host"],
        port=ctx.obj["port"],
        username=ctx.obj["username"],
        password=ctx.obj.get("password"),
        private_key_path=ctx.obj.get("key"),
        passphrase=ctx.obj.get("passphrase"),
    )


def _require_ssh_opts(ctx):
    """Abort early when mandatory SSH options are missing."""
    if not ctx.obj.get("host"):
        raise click.UsageError("--host is required for this command.")
    if not ctx.obj.get("username"):
        raise click.UsageError("--username is required for this command.")


def _connect(ssh):
    """Connect with a friendly error on failure."""
    try:
        console.print("[bold]Connecting...[/]")
        ssh.connect()
    except Exception as exc:
        error_console.print(f"[bold red]Connection failed:[/] {_friendly_ssh_error(exc)}")
        sys.exit(1)


def _show_token_usage(usage):
    """Print a small Rich table summarising token consumption."""
    table = Table(title="Token Usage", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Input tokens", str(usage["total_input_tokens"]))
    table.add_row("Output tokens", str(usage["total_output_tokens"]))
    table.add_row("API requests", str(usage["total_requests"]))
    console.print(table)


def _build_server_context(os_info, apps):
    """Collapse OS + app dicts into a flat context dict for the AI brain."""
    ctx = {}
    if os_info:
        ctx["os"] = (
            f"{os_info.get('distribution', 'unknown')} "
            f"{os_info.get('version', '')}".strip()
        )
        ctx["kernel"] = os_info.get("kernel", "unknown")
        ctx["architecture"] = os_info.get("architecture", "unknown")
        ctx["hostname"] = os_info.get("hostname", "unknown")

    if apps:
        web = [w.get("name", "") for w in apps.get("web_servers", [])]
        if web:
            ctx["web_servers"] = ", ".join(web)

        dbs = [d.get("name", "") for d in apps.get("databases", [])]
        if dbs:
            ctx["databases"] = ", ".join(dbs)

        panels = [p.get("name", "") for p in apps.get("control_panels", [])]
        if panels:
            ctx["control_panels"] = ", ".join(panels)

        cms = [c.get("name", "") for c in apps.get("cms", [])]
        if cms:
            ctx["cms"] = ", ".join(cms)

        langs = [l.get("name", "") for l in apps.get("languages", [])]
        if langs:
            ctx["languages"] = ", ".join(langs)

        containers = [c.get("name", "") for c in apps.get("containers", [])]
        if containers:
            ctx["container_runtimes"] = ", ".join(containers)

        services = apps.get("services", [])
        running = sum(1 for s in services if s.get("status") == "running")
        ctx["services"] = f"{running} running out of {len(services)} total"

    return ctx


def _get_discovered_software(os_info, apps):
    """Return a set of lowercase software names found during discovery."""
    names = set()
    if os_info:
        dist = os_info.get("distribution", "")
        if dist:
            names.add(dist.lower())

    if apps:
        for category in (
            "web_servers", "databases", "control_panels",
            "cms", "languages", "containers",
        ):
            for item in apps.get(category, []):
                name = item.get("name", "")
                if name:
                    names.add(name.lower())
    return names


def _fetch_doc_context(software_names, server_context):
    """Populate *server_context* with documentation snippets."""
    fetcher = DocFetcher()
    for name in software_names:
        doc = fetcher.get_context(name)
        if doc:
            server_context[f"docs_{name}"] = (
                json.dumps(doc) if not isinstance(doc, str) else doc
            )


def _execute_plan(plan, ssh, ctx, approval_mgr=None, rollback_mgr=None):
    """Walk through a plan list, executing each step with approval / output."""
    auto_approve = ctx.obj.get("auto_approve", False)

    for step in plan:
        step_num = step.get("step", "?")
        desc = step.get("description", "")
        cmd = step.get("command")
        destructive = step.get("destructive", False)
        needs_approval = step.get("needs_approval", False)

        tag = " [bold red][DESTRUCTIVE][/]" if destructive else ""
        console.print(f"\n[bold]Step {step_num}:{tag}[/] {desc}")

        if not cmd:
            console.print("  [dim](manual step — no command)[/]")
            continue

        console.print(f"  [dim]$ {cmd}[/]")

        # --- Approval gate ---------------------------------------------------
        if needs_approval and not auto_approve:
            if approval_mgr:
                action = {
                    "command": cmd,
                    "description": desc,
                    "destructive": destructive,
                }
                if rollback_mgr:
                    try:
                        snap_id = rollback_mgr.create_snapshot(cmd, desc)
                        action["snapshot_id"] = snap_id
                        console.print(f"  [dim]Snapshot: {snap_id[:8]}[/]")
                    except Exception:
                        pass
                approved = approval_mgr.request_approval(action)
            else:
                answer = Prompt.ask(
                    "  Approve this step? [y/N/skip]",
                    default="N",
                    console=console,
                )
                if answer.strip().lower() == "skip":
                    console.print("  [yellow]Skipped.[/]")
                    continue
                approved = answer.strip().lower() in ("y", "yes")

            if not approved:
                console.print("  [yellow]Denied. Stopping plan execution.[/]")
                break
        elif needs_approval and auto_approve:
            console.print("  [dim]Auto-approved.[/]")
            if rollback_mgr:
                try:
                    snap_id = rollback_mgr.create_snapshot(cmd, desc)
                    console.print(f"  [dim]Snapshot: {snap_id[:8]}[/]")
                except Exception:
                    pass

        # --- Execute ---------------------------------------------------------
        if destructive and ssh.password:
            result = ssh.execute_sudo(cmd)
        else:
            result = ssh.execute(cmd)

        if result["stdout"]:
            output = result["stdout"].rstrip()
            if len(output) > 2000:
                output = output[:2000] + "\n... (truncated)"
            console.print(Panel(
                output,
                title="Output",
                border_style="green" if result["exit_code"] == 0 else "red",
            ))
        if result["stderr"]:
            error_console.print(f"  [dim red]{result['stderr'].rstrip()[:500]}[/]")

        if result["exit_code"] != 0:
            console.print(
                f"  [bold red]Command failed (exit code {result['exit_code']})[/]"
            )
            rollback_hint = step.get("rollback")
            if rollback_hint:
                console.print(f"  [yellow]Rollback hint:[/] {rollback_hint}")

            if not auto_approve:
                answer = Prompt.ask(
                    "  Continue with remaining steps? [y/N]",
                    default="N",
                    console=console,
                )
                if answer.strip().lower() not in ("y", "yes"):
                    console.print("  [yellow]Execution stopped by user.[/]")
                    break
        else:
            console.print("  [green]OK[/]")


# ---------------------------------------------------------------------------
# Click CLI
# ---------------------------------------------------------------------------

@click.group()
@click.option("--host", "-h", envvar="SYSADMIN_HOST", default=None, help="Remote host to connect to.")
@click.option("--port", "-p", default=22, type=int, help="SSH port (default 22).")
@click.option("--username", "-u", envvar="SYSADMIN_USER", default=None, help="SSH username.")
@click.option("--password", envvar="SYSADMIN_PASSWORD", default=None, help="SSH password.")
@click.option("--key", "-k", type=click.Path(), default=None, help="Path to SSH private key.")
@click.option("--passphrase", default=None, help="Passphrase for SSH private key.")
@click.option("--api-key", envvar="ANTHROPIC_API_KEY", default=None, help="Anthropic API key for AI commands.")
@click.option("--auto-approve", is_flag=True, default=False, help="Auto-approve all actions (for scripted use).")
@click.pass_context
def main(ctx, host, port, username, password, key, passphrase, api_key, auto_approve):
    """AI-powered systems administration agent."""
    ctx.ensure_object(dict)
    ctx.obj.update({
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "key": key,
        "passphrase": passphrase,
        "api_key": api_key,
        "auto_approve": auto_approve,
    })


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

@main.command()
@click.pass_context
def scan(ctx):
    """Full scan: OS detection + app discovery + diagnostics."""
    _require_ssh_opts(ctx)
    ssh = _build_ssh(ctx)
    _connect(ssh)

    try:
        console.print("\n[bold cyan]--- OS Detection ---[/]")
        os_info = OSDetector(ssh).detect()
        format_os_info(os_info, console)

        console.print("\n[bold cyan]--- Application Discovery ---[/]")
        apps = AppDiscovery(ssh).discover()
        format_app_discovery(apps, console)

        console.print("\n[bold cyan]--- Diagnostics ---[/]")
        approval_mgr = ApprovalManager(auto_approve=ctx.obj["auto_approve"])
        rollback_mgr = RollbackManager(ssh)
        engine = DiagnosticEngine(ssh, approval_mgr, rollback_mgr)
        results = engine.run_all()
        format_diagnostics(results, console)
    except Exception as exc:
        error_console.print(f"[bold red]Error during scan:[/] {exc}")
        sys.exit(1)
    finally:
        ssh.disconnect()


# ---------------------------------------------------------------------------
# os
# ---------------------------------------------------------------------------

@main.command(name="os")
@click.pass_context
def os_cmd(ctx):
    """Detect and display operating system information."""
    _require_ssh_opts(ctx)
    ssh = _build_ssh(ctx)
    _connect(ssh)

    try:
        os_info = OSDetector(ssh).detect()
        format_os_info(os_info, console)
    except Exception as exc:
        error_console.print(f"[bold red]Error:[/] {exc}")
        sys.exit(1)
    finally:
        ssh.disconnect()


# ---------------------------------------------------------------------------
# apps
# ---------------------------------------------------------------------------

@main.command()
@click.pass_context
def apps(ctx):
    """Discover installed applications and services."""
    _require_ssh_opts(ctx)
    ssh = _build_ssh(ctx)
    _connect(ssh)

    try:
        app_info = AppDiscovery(ssh).discover()
        format_app_discovery(app_info, console)
    except Exception as exc:
        error_console.print(f"[bold red]Error:[/] {exc}")
        sys.exit(1)
    finally:
        ssh.disconnect()


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------

@main.command()
@click.pass_context
def diagnose(ctx):
    """Run health checks on the remote server."""
    _require_ssh_opts(ctx)
    ssh = _build_ssh(ctx)
    _connect(ssh)

    try:
        approval_mgr = ApprovalManager(auto_approve=ctx.obj["auto_approve"])
        rollback_mgr = RollbackManager(ssh)
        engine = DiagnosticEngine(ssh, approval_mgr, rollback_mgr)
        results = engine.run_all()
        format_diagnostics(results, console)
    except Exception as exc:
        error_console.print(f"[bold red]Error:[/] {exc}")
        sys.exit(1)
    finally:
        ssh.disconnect()


# ---------------------------------------------------------------------------
# fix
# ---------------------------------------------------------------------------

@main.command()
@click.pass_context
def fix(ctx):
    """Run diagnostics, show fixable issues, and apply fixes with approval."""
    _require_ssh_opts(ctx)
    ssh = _build_ssh(ctx)
    _connect(ssh)

    try:
        approval_mgr = ApprovalManager(auto_approve=ctx.obj["auto_approve"])
        rollback_mgr = RollbackManager(ssh)
        engine = DiagnosticEngine(ssh, approval_mgr, rollback_mgr)

        console.print("[bold]Running diagnostics...[/]\n")
        results = engine.run_all()
        format_diagnostics(results, console)

        fixable = [r for r in results if r.get("fix")]
        if not fixable:
            console.print("\n[green]No fixable issues found.[/]")
            return

        console.print(f"\n[bold yellow]Found {len(fixable)} fixable issue(s).[/]\n")

        for check in fixable:
            name = (
                check.get("name", "unknown")
                .replace("check_", "")
                .replace("_", " ")
                .title()
            )
            fixes = check.get("fix", [])
            if not isinstance(fixes, list):
                fixes = [fixes]

            console.print(f"[bold]{name}:[/] {check.get('details', '')}")

            for action in fixes:
                desc = action.get("description", action.get("command", ""))
                console.print(f"  [dim]Fix:[/] {desc}")
                outcome = engine.apply_fix(action)
                if outcome.get("applied"):
                    console.print("  [green]Applied successfully.[/]")
                    if outcome.get("result"):
                        console.print(f"  [dim]{outcome['result'][:200]}[/]")
                else:
                    console.print(
                        f"  [yellow]Not applied:[/] {outcome.get('reason', 'unknown')}"
                    )
            console.print()

        # Approval summary
        stats = approval_mgr.get_stats()
        console.print(Panel(
            f"Total: {stats['total']}  |  "
            f"Approved: {stats['approved']}  |  "
            f"Denied: {stats['denied']}  |  "
            f"Errors: {stats['errors']}",
            title="Approval Summary",
            border_style="cyan",
        ))
    except Exception as exc:
        error_console.print(f"[bold red]Error:[/] {exc}")
        sys.exit(1)
    finally:
        ssh.disconnect()


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------

@main.command()
@click.pass_context
def rollback(ctx):
    """List snapshots and rollback to a previous state."""
    _require_ssh_opts(ctx)
    ssh = _build_ssh(ctx)
    _connect(ssh)

    try:
        rollback_mgr = RollbackManager(ssh)
        snapshots = rollback_mgr.list_snapshots()
        format_snapshots(snapshots, console)

        if not snapshots:
            return

        snapshot_ids = [s["id"] for s in snapshots]
        short_map = {s["id"][:8]: s["id"] for s in snapshots}

        chosen = Prompt.ask(
            "\nEnter snapshot ID to rollback (or 'cancel' to abort)",
            console=console,
        )

        if chosen.strip().lower() == "cancel":
            console.print("[dim]Rollback cancelled.[/]")
            return

        # Match by full ID, truncated 8-char ID, or prefix
        chosen = chosen.strip()
        full_id = short_map.get(chosen)
        if not full_id and chosen in snapshot_ids:
            full_id = chosen
        if not full_id:
            # Try prefix match
            for sid in snapshot_ids:
                if sid.startswith(chosen):
                    full_id = sid
                    break

        if not full_id:
            error_console.print(f"[bold red]Snapshot not found:[/] {chosen}")
            sys.exit(1)

        confirm = Prompt.ask(
            f"Confirm rollback to snapshot [bold]{full_id[:8]}[/]? [y/N]",
            default="N",
            console=console,
        )
        if confirm.strip().lower() not in ("y", "yes"):
            console.print("[dim]Rollback cancelled.[/]")
            return

        console.print(f"[bold yellow]Rolling back to snapshot {full_id[:8]}...[/]")
        results = rollback_mgr.rollback(full_id)

        for r in results:
            target = r.get("file") or r.get("service", "unknown")
            status = r.get("status", "unknown")
            if status in ("restored", "started", "stopped"):
                console.print(f"  [green]{target}:[/] {status}")
            else:
                err = r.get("error", "")
                console.print(f"  [red]{target}:[/] {status} {err}")

        console.print("[bold green]Rollback complete.[/]")
    except Exception as exc:
        error_console.print(f"[bold red]Error:[/] {exc}")
        sys.exit(1)
    finally:
        ssh.disconnect()


# ---------------------------------------------------------------------------
# exec
# ---------------------------------------------------------------------------

@main.command(name="exec")
@click.argument("command")
@click.pass_context
def exec_cmd(ctx, command):
    """Execute an arbitrary command on the remote server."""
    _require_ssh_opts(ctx)
    ssh = _build_ssh(ctx)
    _connect(ssh)

    try:
        result = ssh.execute(command)
        if result["stdout"]:
            console.print(result["stdout"], highlight=False)
        if result["stderr"]:
            error_console.print(f"[dim red]{result['stderr']}[/]")
        if result["exit_code"] != 0:
            error_console.print(f"[bold red]Exit code:[/] {result['exit_code']}")
            sys.exit(result["exit_code"])
    except Exception as exc:
        error_console.print(f"[bold red]Error:[/] {exc}")
        sys.exit(1)
    finally:
        ssh.disconnect()


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------

@main.command()
@click.argument("request", nargs=-1, required=True)
@click.pass_context
def ask(ctx, request):
    """Plain English request mode.

    Analyses the server, builds a plan with the AI agent, and executes it
    step by step with approval for destructive actions.
    """
    _require_ssh_opts(ctx)
    user_request = " ".join(request)
    api_key = ctx.obj.get("api_key")

    ssh = _build_ssh(ctx)
    _connect(ssh)

    try:
        brain = AgentBrain(api_key=api_key)
    except ValueError as exc:
        ssh.disconnect()
        error_console.print(f"[bold red]{exc}[/]")
        sys.exit(1)

    try:
        # 1. Gather server context
        console.print("[bold]Detecting OS...[/]")
        os_info = OSDetector(ssh).detect()
        format_os_info(os_info, console)

        console.print("\n[bold]Discovering applications...[/]")
        app_info = AppDiscovery(ssh).discover()
        format_app_discovery(app_info, console)

        server_context = _build_server_context(os_info, app_info)

        # 2. Fetch relevant documentation
        software_names = _get_discovered_software(os_info, app_info)
        _fetch_doc_context(software_names, server_context)

        # 3. Send to AI brain
        console.print("\n[bold]Analysing request...[/]")
        response = brain.interpret(user_request, server_context)

        # Show explanation
        explanation = response.get("explanation", "")
        if explanation:
            console.print(Panel(explanation, title="Analysis", border_style="cyan"))

        # 4. Handle questions — display and exit
        questions = response.get("questions", [])
        if questions:
            console.print("\n[bold yellow]The agent needs more information:[/]")
            for i, q in enumerate(questions, 1):
                console.print(f"  {i}. {q}")
            console.print(
                "\n[dim]Please re-run with more details in your request.[/]"
            )
            if not response.get("plan"):
                console.print()
                _show_token_usage(brain.get_token_usage())
                return

        # 5. Display plan
        plan = response.get("plan", [])
        if not plan:
            console.print("[dim]No actions needed.[/]")
            console.print()
            _show_token_usage(brain.get_token_usage())
            return

        console.print(f"\n[bold]Execution Plan ({len(plan)} steps):[/]")
        for step in plan:
            tag = " [DESTRUCTIVE]" if step.get("destructive") else ""
            console.print(
                f"  {step.get('step', '?')}. {step.get('description', '')}{tag}"
            )

        console.print()

        # 6. Execute plan step by step
        approval_mgr = ApprovalManager(auto_approve=ctx.obj["auto_approve"])
        rollback_mgr = RollbackManager(ssh)
        _execute_plan(plan, ssh, ctx, approval_mgr, rollback_mgr)

        # 7. Token usage
        console.print()
        _show_token_usage(brain.get_token_usage())

    except Exception as exc:
        if _is_api_error(exc):
            error_console.print(f"[bold red]{_friendly_api_error(exc)}[/]")
        else:
            error_console.print(f"[bold red]Error:[/] {exc}")
        sys.exit(1)
    finally:
        ssh.disconnect()


# ---------------------------------------------------------------------------
# interactive
# ---------------------------------------------------------------------------

@main.command()
@click.pass_context
def interactive(ctx):
    """Interactive REPL mode.

    Connect once, then issue commands and plain-English questions.
    Type 'exit' to quit.  Prefix a raw shell command with '!' to execute
    it directly (e.g. !uptime).
    """
    _require_ssh_opts(ctx)
    api_key = ctx.obj.get("api_key")

    ssh = _build_ssh(ctx)
    _connect(ssh)

    try:
        brain = AgentBrain(api_key=api_key)
    except ValueError as exc:
        ssh.disconnect()
        error_console.print(f"[bold red]{exc}[/]")
        sys.exit(1)

    approval_mgr = ApprovalManager(auto_approve=ctx.obj["auto_approve"])
    rollback_mgr = RollbackManager(ssh)

    try:
        # Initial discovery
        console.print("[bold]Gathering server context...[/]\n")
        os_info = OSDetector(ssh).detect()
        format_os_info(os_info, console)

        app_info = AppDiscovery(ssh).discover()
        server_context = _build_server_context(os_info, app_info)

        software_names = _get_discovered_software(os_info, app_info)
        _fetch_doc_context(software_names, server_context)

        console.print(
            "\n[bold green]Ready.[/] Type a request in plain English. "
            "Type [bold]exit[/] to quit.\n"
        )

        while True:
            try:
                user_input = Prompt.ask("[bold cyan]sysadmin[/]", console=console)
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Goodbye.[/]")
                break

            user_input = user_input.strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                console.print("[dim]Goodbye.[/]")
                break

            # Direct shell command shortcut: !<command>
            if user_input.startswith("!"):
                raw_cmd = user_input[1:].strip()
                if not raw_cmd:
                    continue
                try:
                    result = ssh.execute(raw_cmd)
                    if result["stdout"]:
                        console.print(result["stdout"], highlight=False)
                    if result["stderr"]:
                        error_console.print(f"[dim red]{result['stderr']}[/]")
                    if result["exit_code"] != 0:
                        error_console.print(
                            f"[bold red]Exit code:[/] {result['exit_code']}"
                        )
                except Exception as exc:
                    error_console.print(f"[bold red]Error:[/] {exc}")
                continue

            # Send to AI brain
            try:
                response = brain.interpret(user_input, server_context)
            except Exception as exc:
                if _is_api_error(exc):
                    error_console.print(f"[bold red]{_friendly_api_error(exc)}[/]")
                else:
                    error_console.print(f"[bold red]Error:[/] {exc}")
                continue

            # Explanation
            explanation = response.get("explanation", "")
            if explanation:
                console.print(Panel(explanation, title="Analysis", border_style="cyan"))

            # Questions
            questions = response.get("questions", [])
            if questions:
                console.print("[bold yellow]Need more information:[/]")
                for i, q in enumerate(questions, 1):
                    console.print(f"  {i}. {q}")
                if not response.get("plan"):
                    continue

            # Execute plan
            plan = response.get("plan", [])
            if not plan:
                console.print("[dim]No actions to take.[/]")
                continue

            for step in plan:
                step_num = step.get("step", "?")
                desc = step.get("description", "")
                cmd = step.get("command")
                destructive = step.get("destructive", False)
                needs_approval = step.get("needs_approval", False)

                tag = " [bold red][DESTRUCTIVE][/]" if destructive else ""
                console.print(f"\n[bold]Step {step_num}:{tag}[/] {desc}")

                if not cmd:
                    console.print("  [dim](manual step)[/]")
                    continue

                console.print(f"  [dim]$ {cmd}[/]")

                # Approval
                if needs_approval:
                    action = {
                        "command": cmd,
                        "description": desc,
                        "destructive": destructive,
                    }
                    if destructive:
                        try:
                            snap_id = rollback_mgr.create_snapshot(cmd, desc)
                            action["snapshot_id"] = snap_id
                            console.print(f"  [dim]Snapshot: {snap_id[:8]}[/]")
                        except Exception:
                            pass
                    approved = approval_mgr.request_approval(action)
                    if not approved:
                        console.print("  [yellow]Denied. Stopping plan execution.[/]")
                        break

                # Execute
                try:
                    if destructive and ssh.password:
                        result = ssh.execute_sudo(cmd)
                    else:
                        result = ssh.execute(cmd)

                    if result["stdout"]:
                        output = result["stdout"].rstrip()
                        if len(output) > 2000:
                            output = output[:2000] + "\n... (truncated)"
                        console.print(Panel(
                            output,
                            title="Output",
                            border_style=(
                                "green" if result["exit_code"] == 0 else "red"
                            ),
                        ))
                    if result["stderr"]:
                        error_console.print(
                            f"  [dim red]{result['stderr'].rstrip()[:500]}[/]"
                        )

                    if result["exit_code"] != 0:
                        console.print(
                            f"  [bold red]Failed (exit {result['exit_code']})[/]"
                        )
                        rollback_hint = step.get("rollback")
                        if rollback_hint:
                            console.print(f"  [yellow]Rollback:[/] {rollback_hint}")
                        answer = Prompt.ask(
                            "  Continue? [y/N]",
                            default="N",
                            console=console,
                        )
                        if answer.strip().lower() not in ("y", "yes"):
                            break
                    else:
                        console.print("  [green]OK[/]")

                    # Analyse result for extra insight (best-effort)
                    try:
                        analysis = brain.analyze_results(
                            cmd,
                            result["stdout"],
                            result["stderr"],
                            result["exit_code"],
                        )
                        issues = analysis.get("issues_found", [])
                        if issues:
                            console.print("  [yellow]Issues detected:[/]")
                            for issue in issues:
                                console.print(f"    - {issue}")
                        recs = analysis.get("recommendations", [])
                        if recs:
                            console.print("  [cyan]Recommendations:[/]")
                            for rec in recs:
                                console.print(f"    - {rec}")
                    except Exception:
                        pass

                except Exception as exc:
                    error_console.print(f"  [bold red]Error:[/] {exc}")
                    break

            console.print()

        # Show session token usage on exit
        console.print()
        _show_token_usage(brain.get_token_usage())

    except Exception as exc:
        error_console.print(f"[bold red]Error:[/] {exc}")
        sys.exit(1)
    finally:
        ssh.disconnect()
        console.print("[dim]Disconnected.[/]")


@main.command()
@click.option("--web-host", default="0.0.0.0", help="Web server bind address")
@click.option("--web-port", default=5000, help="Web server port")
@click.option("--debug", is_flag=True, default=False, help="Enable Flask debug mode")
@click.pass_context
def web(ctx, web_host, web_port, debug):
    """Launch the web UI."""
    try:
        from sysadmin_agent.web.app import create_app
    except ImportError as e:
        error_console.print(f"[bold red]Missing web dependencies:[/] {e}")
        error_console.print("Install with: pip install flask flask-socketio simple-websocket python-dotenv")
        sys.exit(1)

    app, socketio = create_app()
    console.print(f"[bold green]Starting web UI at http://{web_host}:{web_port}[/]")
    socketio.run(app, host=web_host, port=web_port, debug=debug, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
