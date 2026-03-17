"""CLI interface for the systems admin agent."""

import os
import sys
import json
import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from sysadmin_agent.connection import SSHManager
from sysadmin_agent.discovery import OSDetector, AppDiscovery
from sysadmin_agent.diagnostics import DiagnosticEngine
from sysadmin_agent.approval import ApprovalManager
from sysadmin_agent.rollback import RollbackManager
from sysadmin_agent.ai import AgentBrain
from sysadmin_agent.knowledge import DocFetcher
from sysadmin_agent.utils import format_os_info, format_app_discovery, format_diagnostics, format_snapshots

console = Console()


def build_ssh(ctx):
    opts = ctx.obj
    return SSHManager(
        host=opts["host"],
        port=opts["port"],
        username=opts["username"],
        password=opts.get("password"),
        private_key_path=opts.get("key"),
        passphrase=opts.get("passphrase"),
        timeout=opts.get("timeout", 15),
    )


def gather_server_context(ssh):
    """Run OS detection and app discovery, return context dict."""
    os_info = OSDetector(ssh).detect()
    apps = AppDiscovery(ssh).discover()
    context = {
        "os_type": os_info.get("type", "unknown"),
        "distribution": os_info.get("distribution", "unknown"),
        "version": os_info.get("version", "unknown"),
        "hostname": os_info.get("hostname", "unknown"),
        "web_servers": ", ".join(s["name"] for s in apps.get("web_servers", [])) or "none",
        "databases": ", ".join(s["name"] for s in apps.get("databases", [])) or "none",
        "control_panels": ", ".join(s["name"] for s in apps.get("control_panels", [])) or "none",
        "cms": ", ".join(f"{s['name']} at {s.get('path', '?')}" for s in apps.get("cms", [])) or "none",
        "containers": ", ".join(s.get("name", s.get("image", "?")) for s in apps.get("containers", [])) or "none",
        "languages": ", ".join(f"{s['name']} {s.get('version', '')}" for s in apps.get("languages", [])) or "none",
    }
    return os_info, apps, context


@click.group()
@click.option("--host", "-h", required=True, help="SSH host")
@click.option("--port", "-p", default=22, help="SSH port")
@click.option("--username", "-u", required=True, help="SSH username")
@click.option("--password", "-P", default=None, help="SSH password")
@click.option("--key", "-k", default=None, help="Path to SSH private key")
@click.option("--passphrase", default=None, help="Key passphrase")
@click.option("--timeout", default=15, help="Connection timeout in seconds")
@click.option("--api-key", envvar="ANTHROPIC_API_KEY", default=None, help="Anthropic API key")
@click.option("--auto-approve", is_flag=True, default=False, help="Auto-approve destructive actions")
@click.pass_context
def main(ctx, host, port, username, password, key, passphrase, timeout, api_key, auto_approve):
    """AI-powered systems administration agent."""
    ctx.ensure_object(dict)
    ctx.obj.update({
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "key": key,
        "passphrase": passphrase,
        "timeout": timeout,
        "api_key": api_key,
        "auto_approve": auto_approve,
    })


@main.command()
@click.pass_context
def scan(ctx):
    """Full scan: OS detection + app discovery + diagnostics."""
    ssh = build_ssh(ctx)
    try:
        ssh.connect()
        console.print("[bold]Scanning server...[/bold]\n")

        os_info = OSDetector(ssh).detect()
        format_os_info(os_info, console)

        apps = AppDiscovery(ssh).discover()
        format_app_discovery(apps, console)

        approval = ApprovalManager(auto_approve=ctx.obj["auto_approve"])
        rollback = RollbackManager(ssh)
        engine = DiagnosticEngine(ssh, approval, rollback)
        results = engine.run_all()
        format_diagnostics(results, console)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    finally:
        ssh.disconnect()


@main.command(name="os")
@click.pass_context
def detect_os(ctx):
    """Detect remote server OS."""
    ssh = build_ssh(ctx)
    try:
        ssh.connect()
        os_info = OSDetector(ssh).detect()
        format_os_info(os_info, console)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    finally:
        ssh.disconnect()


@main.command()
@click.pass_context
def apps(ctx):
    """Discover installed applications."""
    ssh = build_ssh(ctx)
    try:
        ssh.connect()
        result = AppDiscovery(ssh).discover()
        format_app_discovery(result, console)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    finally:
        ssh.disconnect()


@main.command()
@click.pass_context
def diagnose(ctx):
    """Run health checks."""
    ssh = build_ssh(ctx)
    try:
        ssh.connect()
        approval = ApprovalManager(auto_approve=ctx.obj["auto_approve"])
        rollback = RollbackManager(ssh)
        engine = DiagnosticEngine(ssh, approval, rollback)
        results = engine.run_all()
        format_diagnostics(results, console)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    finally:
        ssh.disconnect()


@main.command()
@click.pass_context
def fix(ctx):
    """Run diagnostics and apply fixes with approval."""
    ssh = build_ssh(ctx)
    try:
        ssh.connect()
        approval = ApprovalManager(auto_approve=ctx.obj["auto_approve"])
        rollback = RollbackManager(ssh)
        engine = DiagnosticEngine(ssh, approval, rollback)

        console.print("[bold]Running diagnostics...[/bold]\n")
        results = engine.run_all()
        format_diagnostics(results, console)

        fixable = [r for r in results if r.get("fix")]
        if not fixable:
            console.print("\n[green]No issues to fix.[/green]")
            return

        console.print(f"\n[yellow]Found {len(fixable)} fixable issue(s).[/yellow]\n")

        for result in fixable:
            console.print(Panel(
                f"[bold]{result['name']}[/bold]\n{result['details']}",
                title="Issue",
                border_style="yellow",
            ))
            for action in result["fix"]:
                outcome = engine.apply_fix(action)
                if outcome.get("applied"):
                    console.print(f"  [green]Applied:[/green] {action['description']}")
                    if outcome.get("result"):
                        console.print(f"  Output: {outcome['result'][:200]}")
                else:
                    console.print(f"  [dim]Skipped:[/dim] {outcome.get('reason', 'denied')}")

        stats = approval.get_stats()
        console.print(f"\n[bold]Approval stats:[/bold] {stats['approved']} approved, {stats['denied']} denied out of {stats['total']} total")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    finally:
        ssh.disconnect()


@main.command()
@click.pass_context
def rollback(ctx):
    """List and rollback from snapshots."""
    ssh = build_ssh(ctx)
    try:
        ssh.connect()
        mgr = RollbackManager(ssh)
        snapshots = mgr.list_snapshots()

        if not snapshots:
            console.print("[dim]No snapshots available.[/dim]")
            return

        format_snapshots(snapshots, console)

        snapshot_id = Prompt.ask("Enter snapshot ID to rollback (or 'cancel')")
        if snapshot_id.lower() == "cancel":
            return

        # Match partial IDs
        match = None
        for s in snapshots:
            if s["id"].startswith(snapshot_id):
                match = s
                break

        if not match:
            console.print(f"[red]Snapshot not found: {snapshot_id}[/red]")
            return

        confirm = Prompt.ask(f"Rollback snapshot {match['id'][:8]}?", choices=["y", "n"], default="n")
        if confirm != "y":
            return

        results = mgr.rollback(match["id"])
        console.print(f"[green]Rollback complete. {len(results)} item(s) restored.[/green]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    finally:
        ssh.disconnect()


@main.command(name="exec")
@click.argument("command")
@click.pass_context
def exec_cmd(ctx, command):
    """Execute a remote command."""
    ssh = build_ssh(ctx)
    try:
        ssh.connect()
        result = ssh.execute(command)
        if result["stdout"]:
            console.print(result["stdout"])
        if result["stderr"]:
            console.print(f"[red]{result['stderr']}[/red]")
        if result["exit_code"] != 0:
            console.print(f"[dim]Exit code: {result['exit_code']}[/dim]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    finally:
        ssh.disconnect()


@main.command()
@click.argument("request", nargs=-1, required=True)
@click.pass_context
def ask(ctx, request):
    """Ask the AI agent to do something in plain English."""
    api_key = ctx.obj.get("api_key")
    if not api_key:
        console.print("[red]API key required. Set ANTHROPIC_API_KEY or use --api-key.[/red]")
        sys.exit(1)

    user_request = " ".join(request)
    ssh = build_ssh(ctx)
    try:
        ssh.connect()
        console.print("[bold]Gathering server context...[/bold]")
        os_info, apps, server_context = gather_server_context(ssh)

        # Add doc context for discovered software
        fetcher = DocFetcher()
        doc_context = {}
        for ws in apps.get("web_servers", []):
            info = fetcher.get_context(ws["name"])
            if info:
                doc_context[ws["name"]] = info.get("useful_commands", [])
        for cp in apps.get("control_panels", []):
            info = fetcher.get_context(cp["name"])
            if info:
                doc_context[cp["name"]] = info.get("useful_commands", [])
        for cms in apps.get("cms", []):
            info = fetcher.get_context(cms["name"])
            if info:
                doc_context[cms["name"]] = info.get("useful_commands", [])
        if doc_context:
            server_context["available_tools"] = json.dumps(doc_context)

        console.print("[bold]Consulting AI...[/bold]\n")
        brain = AgentBrain(api_key=api_key)
        response = brain.interpret(user_request, server_context)

        # Handle questions
        if response.get("questions"):
            console.print(Panel(
                "\n".join(f"  {i+1}. {q}" for i, q in enumerate(response["questions"])),
                title="Questions before proceeding",
                border_style="yellow",
            ))
            if not response.get("plan"):
                usage = brain.get_token_usage()
                console.print(f"\n[dim]Tokens used: {usage['total_input_tokens']} in / {usage['total_output_tokens']} out[/dim]")
                return

        # Show explanation
        if response.get("explanation"):
            console.print(Panel(response["explanation"], title="Plan", border_style="blue"))

        # Execute plan
        if response.get("plan"):
            approval = ApprovalManager(auto_approve=ctx.obj["auto_approve"])
            rollback_mgr = RollbackManager(ssh)

            for step in response["plan"]:
                console.print(f"\n[bold]Step {step.get('step', '?')}:[/bold] {step['description']}")

                command = step.get("command")
                if not command:
                    console.print("  [dim](manual step — no command)[/dim]")
                    continue

                console.print(f"  [cyan]$ {command}[/cyan]")

                if step.get("needs_approval", False):
                    snapshot_id = rollback_mgr.create_snapshot(command, step["description"])
                    approved = approval.request_approval({
                        "command": command,
                        "description": step["description"],
                        "destructive": step.get("destructive", True),
                        "snapshot_id": snapshot_id,
                    })
                    if not approved:
                        console.print("  [yellow]Skipped (not approved)[/yellow]")
                        continue

                if step.get("destructive", False):
                    result = ssh.execute_sudo(command)
                else:
                    result = ssh.execute(command)

                if result["stdout"]:
                    # Truncate long output
                    output = result["stdout"]
                    if len(output) > 2000:
                        output = output[:2000] + "\n... (truncated)"
                    console.print(f"  {output}")
                if result["stderr"]:
                    console.print(f"  [red]{result['stderr'][:500]}[/red]")
                if result["exit_code"] != 0:
                    console.print(f"  [red]Command failed (exit {result['exit_code']})[/red]")

        usage = brain.get_token_usage()
        console.print(f"\n[dim]Tokens used: {usage['total_input_tokens']} in / {usage['total_output_tokens']} out ({usage['total_requests']} request(s))[/dim]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    finally:
        ssh.disconnect()


@main.command()
@click.pass_context
def interactive(ctx):
    """Interactive REPL mode — connect once, ask multiple questions."""
    api_key = ctx.obj.get("api_key")
    if not api_key:
        console.print("[red]API key required. Set ANTHROPIC_API_KEY or use --api-key.[/red]")
        sys.exit(1)

    ssh = build_ssh(ctx)
    try:
        ssh.connect()
        console.print("[bold green]Connected.[/bold green] Gathering server info...\n")
        os_info, apps, server_context = gather_server_context(ssh)
        format_os_info(os_info, console)

        brain = AgentBrain(api_key=api_key)
        approval = ApprovalManager(auto_approve=ctx.obj["auto_approve"])
        rollback_mgr = RollbackManager(ssh)
        fetcher = DocFetcher()

        console.print("\n[bold]Ready.[/bold] Type your request in plain English. Type 'exit' to quit.\n")

        while True:
            try:
                user_input = Prompt.ask("[bold cyan]>[/bold cyan]")
            except (EOFError, KeyboardInterrupt):
                break

            user_input = user_input.strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                break

            # Direct command shortcut: starts with !
            if user_input.startswith("!"):
                cmd = user_input[1:].strip()
                result = ssh.execute(cmd)
                if result["stdout"]:
                    console.print(result["stdout"])
                if result["stderr"]:
                    console.print(f"[red]{result['stderr']}[/red]")
                continue

            try:
                response = brain.interpret(user_input, server_context)

                if response.get("questions"):
                    for i, q in enumerate(response["questions"]):
                        console.print(f"  [yellow]{i+1}.[/yellow] {q}")
                    if not response.get("plan"):
                        continue

                if response.get("explanation"):
                    console.print(f"\n[blue]{response['explanation']}[/blue]\n")

                for step in response.get("plan", []):
                    console.print(f"[bold]Step {step.get('step', '?')}:[/bold] {step['description']}")
                    command = step.get("command")
                    if not command:
                        continue

                    console.print(f"  [cyan]$ {command}[/cyan]")

                    if step.get("needs_approval", False):
                        snapshot_id = rollback_mgr.create_snapshot(command, step["description"])
                        approved = approval.request_approval({
                            "command": command,
                            "description": step["description"],
                            "destructive": step.get("destructive", True),
                            "snapshot_id": snapshot_id,
                        })
                        if not approved:
                            console.print("  [yellow]Skipped[/yellow]")
                            continue

                    if step.get("destructive", False):
                        result = ssh.execute_sudo(command)
                    else:
                        result = ssh.execute(command)

                    if result["stdout"]:
                        output = result["stdout"]
                        if len(output) > 2000:
                            output = output[:2000] + "\n... (truncated)"
                        console.print(f"  {output}")
                    if result["stderr"]:
                        console.print(f"  [red]{result['stderr'][:500]}[/red]")

            except json.JSONDecodeError:
                console.print("[red]AI returned invalid response. Try rephrasing.[/red]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        usage = brain.get_token_usage()
        console.print(f"\n[dim]Session tokens: {usage['total_input_tokens']} in / {usage['total_output_tokens']} out ({usage['total_requests']} request(s))[/dim]")

    except Exception as e:
        console.print(f"[red]Connection error: {e}[/red]")
        sys.exit(1)
    finally:
        ssh.disconnect()
        console.print("[dim]Disconnected.[/dim]")


if __name__ == "__main__":
    main()
