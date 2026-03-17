"""Output formatting using Rich tables and panels."""

from rich.table import Table
from rich.text import Text


STATUS_STYLES = {
    "ok": "green",
    "info": "blue",
    "warning": "yellow",
    "critical": "red",
    "error": "red",
}


def format_os_info(os_info, console):
    """Print a Rich table showing OS details.

    Args:
        os_info: Dict with keys type, distribution, version, kernel,
                 architecture, hostname, uptime.
        console: Rich Console instance.
    """
    table = Table(title="Operating System", show_header=True, header_style="bold cyan")
    table.add_column("Property", style="bold")
    table.add_column("Value")

    fields = [
        ("Hostname", "hostname"),
        ("OS Type", "type"),
        ("Distribution", "distribution"),
        ("Version", "version"),
        ("Kernel", "kernel"),
        ("Architecture", "architecture"),
        ("Uptime", "uptime"),
    ]

    for label, key in fields:
        value = os_info.get(key, "N/A")
        table.add_row(label, str(value))

    console.print(table)


def format_app_discovery(apps, console):
    """Print multiple Rich tables for each discovered application category.

    Args:
        apps: Dict with keys services, web_servers, databases, control_panels,
              cms, languages, containers.
        console: Rich Console instance.
    """
    # Web Servers
    web_servers = apps.get("web_servers", [])
    if web_servers:
        table = Table(title="Web Servers", show_header=True, header_style="bold cyan")
        table.add_column("Name", style="bold")
        table.add_column("Version")
        for ws in web_servers:
            table.add_row(ws.get("name", "N/A"), ws.get("version", "N/A"))
        console.print(table)

    # Databases
    databases = apps.get("databases", [])
    if databases:
        table = Table(title="Databases", show_header=True, header_style="bold cyan")
        table.add_column("Name", style="bold")
        table.add_column("Version")
        table.add_column("Running")
        for db in databases:
            running = db.get("running")
            if running is True:
                running_text = Text("yes", style="green")
            elif running is False:
                running_text = Text("no", style="red")
            else:
                running_text = Text("N/A", style="dim")
            table.add_row(db.get("name", "N/A"), db.get("version", "N/A"), running_text)
        console.print(table)

    # Control Panels
    control_panels = apps.get("control_panels", [])
    if control_panels:
        table = Table(title="Control Panels", show_header=True, header_style="bold cyan")
        table.add_column("Name", style="bold")
        table.add_column("Version")
        for cp in control_panels:
            table.add_row(cp.get("name", "N/A"), cp.get("version", "N/A"))
        console.print(table)

    # CMS
    cms_list = apps.get("cms", [])
    if cms_list:
        table = Table(title="CMS / Applications", show_header=True, header_style="bold cyan")
        table.add_column("Name", style="bold")
        table.add_column("Version")
        table.add_column("Path")
        for cms in cms_list:
            table.add_row(
                cms.get("name", "N/A"),
                cms.get("version", "N/A"),
                cms.get("path", "N/A"),
            )
        console.print(table)

    # Languages
    languages = apps.get("languages", [])
    if languages:
        table = Table(title="Languages & Runtimes", show_header=True, header_style="bold cyan")
        table.add_column("Name", style="bold")
        table.add_column("Version")
        for lang in languages:
            table.add_row(lang.get("name", "N/A"), lang.get("version", "N/A"))
        console.print(table)

    # Containers
    containers = apps.get("containers", [])
    if containers:
        for runtime in containers:
            title = f"Containers ({runtime.get('name', 'unknown')} — {runtime.get('version', 'N/A')})"
            running_containers = runtime.get("containers", [])
            if running_containers:
                table = Table(title=title, show_header=True, header_style="bold cyan")
                table.add_column("ID")
                table.add_column("Name", style="bold")
                table.add_column("Image")
                table.add_column("Status")
                for c in running_containers:
                    table.add_row(
                        c.get("id", "N/A"),
                        c.get("name", "N/A"),
                        c.get("image", "N/A"),
                        c.get("status", "N/A"),
                    )
                console.print(table)
            else:
                console.print(f"[bold cyan]{title}[/]: no running containers")

    # Service count summary
    services = apps.get("services", [])
    table = Table(title="Service Summary", show_header=True, header_style="bold cyan")
    table.add_column("Category", style="bold")
    table.add_column("Count", justify="right")

    running = sum(1 for s in services if s.get("status") == "running")
    stopped = sum(1 for s in services if s.get("status") == "stopped")
    other = len(services) - running - stopped

    table.add_row("Total services", str(len(services)))
    table.add_row("Running", Text(str(running), style="green"))
    table.add_row("Stopped", Text(str(stopped), style="red"))
    if other:
        table.add_row("Other", str(other))
    table.add_row("Web servers", str(len(web_servers)))
    table.add_row("Databases", str(len(databases)))
    table.add_row("Control panels", str(len(control_panels)))
    table.add_row("CMS installs", str(len(cms_list)))
    table.add_row("Languages", str(len(languages)))
    table.add_row("Container runtimes", str(len(containers)))

    console.print(table)


def format_diagnostics(results, console):
    """Print a Rich table of diagnostic check results.

    Args:
        results: List of dicts with keys name, status, severity, details, fix.
        console: Rich Console instance.
    """
    table = Table(title="Diagnostic Results", show_header=True, header_style="bold cyan")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Severity")
    table.add_column("Fixable")
    table.add_column("Details", max_width=60)

    for check in results:
        name = check.get("name", "unknown").replace("check_", "").replace("_", " ").title()
        status_raw = check.get("status", "unknown")
        style = STATUS_STYLES.get(status_raw, "white")
        status_text = Text(status_raw.upper(), style=style)
        severity = check.get("severity", "low")
        fixable = "Yes" if check.get("fix") else "No"
        details = check.get("details", "")
        # Truncate long details for the table
        if len(details) > 60:
            details = details[:57] + "..."

        table.add_row(name, status_text, severity, fixable, details)

    console.print(table)


def format_snapshots(snapshots, console):
    """Print a Rich table of rollback snapshots.

    Args:
        snapshots: List of dicts with keys id, timestamp, command, status.
        console: Rich Console instance.
    """
    if not snapshots:
        console.print("[dim]No snapshots found.[/]")
        return

    table = Table(title="Rollback Snapshots", show_header=True, header_style="bold cyan")
    table.add_column("ID", style="bold")
    table.add_column("Time")
    table.add_column("Command")
    table.add_column("Status")

    for snap in snapshots:
        snap_id = str(snap.get("id", ""))[:8]
        timestamp = snap.get("timestamp", "N/A")
        command = str(snap.get("command", ""))
        if len(command) > 40:
            command = command[:37] + "..."
        status = snap.get("status", "N/A")

        table.add_row(snap_id, timestamp, command, status)

    console.print(table)
