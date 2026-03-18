"""Comprehensive Rust game server diagnostics.

Performs deep analysis via RCON commands, Pterodactyl API, and SSH-level
system inspection to identify performance issues, lag sources, plugin
problems, and configuration mistakes.

This is the Rust equivalent of wordpress_checks.py — a specialized
diagnostic engine that knows how Rust servers actually work.
"""

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


class RustServerDiagnostics:
    """Deep diagnostics for a Rust game server.

    Requires at least one of: RCON client, Pterodactyl API, SSH connection.
    The more you provide, the deeper the analysis.

    Usage::

        diag = RustServerDiagnostics(
            rcon=rcon_client,        # For live server queries
            ptero=ptero_api,         # For file/config management
            server_id="abc123",      # Pterodactyl server identifier
            ssh=ssh_manager,         # For OS-level inspection
        )
        results = diag.run_all()
    """

    # All known Oxide directory casing variations
    _OXIDE_LOG_DIRS = [
        "/server/rust/oxide/logs",
        "/server/rust/Oxide/Logs",
        "/server/rust/Oxide/logs",
        "/server/rust/oxide/Logs",
    ]

    def __init__(self, rcon=None, ptero=None, server_id=None, ssh=None,
                 on_progress=None, server_limits=None):
        self.rcon = rcon
        self.ptero = ptero
        self.server_id = server_id
        self.ssh = ssh
        self._on_progress = on_progress  # callback(message_str)
        # Server limits from Pterodactyl (cpu: 0=unlimited, memory in MB, etc.)
        self._server_limits = server_limits or {}
        # Discovered Oxide log directory (cached after first successful read)
        self._oxide_log_dir = None

        self._checks = [
            self.check_server_fps,
            self.check_entity_count,
            self.check_player_count,
            self.check_server_resources,
            self.check_network_quality,
            self.check_oxide_health,
            self.check_plugin_errors,
            self.check_server_config,
            self.check_world_size,
            self.check_save_performance,
            self.check_garbage_collection,
            self.check_process_health,
            self.check_disk_space_game,
            self.check_rust_update_status,
            self.check_oxide_update_status,
            self.check_recent_crashes,
            self.check_connection_quality,
            self.check_memory_leak_indicators,
            self.check_hook_performance,
            self.check_server_logs,
        ]

    def _progress(self, message):
        """Emit a progress update if a callback is registered."""
        if self._on_progress:
            try:
                self._on_progress(message)
            except Exception:
                pass

    def run_all(self) -> list:
        """Run all applicable diagnostic checks."""
        results = []
        total = len(self._checks)
        completed = 0
        self._progress(f"Starting diagnostics — {total} checks queued...")
        # Use fewer workers since RCON is single-threaded
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(check): check.__name__
                for check in self._checks
            }
            for future in as_completed(futures):
                name = futures[future]
                completed += 1
                friendly = name.replace("check_", "").replace("_", " ").title()
                self._progress(f"[{completed}/{total}] {friendly}...")
                try:
                    result = future.result()
                    if result:  # Skip None results from inapplicable checks
                        results.append(result)
                except Exception as e:
                    logger.error("Rust check %s failed: %s", name, e)
                    results.append({
                        "name": name,
                        "status": "error",
                        "severity": "low",
                        "details": f"Check failed: {type(e).__name__}: {e}",
                        "fix": None,
                        "category": "error",
                    })
        return results

    def run_lag_diagnosis(self) -> dict:
        """Deep lag / rubber-banding diagnosis.

        Runs 11 checks, cross-correlates findings, and returns a structured
        analysis with root cause identification and actionable fixes.
        """
        findings = []
        raw = {}  # collected raw data for cross-correlation

        total_steps = 11

        # 1. Tick rate — the single most direct indicator
        self._progress(f"[1/{total_steps}] Checking server tick rate (FPS)...")
        fps_result = self._safe_rcon("fps")
        fps_val = self._parse_fps(fps_result) if fps_result else None
        raw["fps"] = fps_val
        if fps_val is not None:
            if fps_val < 10:
                findings.append({
                    "cause": "Critically low server tick rate",
                    "severity": "critical",
                    "details": f"Server running at {fps_val:.1f} FPS (target: 30). "
                               "At this rate, every player experiences constant rubber-banding. "
                               "The server physically cannot process movement fast enough.",
                    "likely_reason": "Entity overload, plugin lag, or insufficient CPU — see other findings below",
                    "fix": "This is a symptom, not the root cause. Check the findings below to see what's dragging FPS down.",
                })
            elif fps_val < 20:
                findings.append({
                    "cause": "Low server tick rate",
                    "severity": "high",
                    "details": f"Server at {fps_val:.1f} FPS (target: 30). "
                               "Players will experience intermittent rubber-banding, especially during combat or vehicles.",
                    "likely_reason": "Growing entity count, heavy plugin load, or CPU contention",
                    "fix": "Check entity count and plugin performance findings below for the root cause.",
                })
            elif fps_val < 28:
                findings.append({
                    "cause": "Server tick rate slightly below target",
                    "severity": "medium",
                    "details": f"Server at {fps_val:.1f} FPS (target: 30). "
                               "May cause subtle movement jitter, especially noticeable on horses or boats.",
                    "likely_reason": "Normal for a busy server, but worth monitoring",
                    "fix": "Not urgent, but addressing entity count or plugin load may help.",
                })

        # 2. Entity count — #1 root cause of low FPS
        self._progress(f"[2/{total_steps}] Checking entity count...")
        entity_result = self._safe_rcon("entity.count")
        entity_count = self._parse_entity_count(entity_result) if entity_result else None
        raw["entities"] = entity_count
        if entity_count is not None:
            if entity_count > 300000:
                findings.append({
                    "cause": "Extreme entity count",
                    "severity": "critical",
                    "details": f"{entity_count:,} entities on the map (healthy: <150k, danger: >300k). "
                               "This is almost certainly the primary cause of lag. Every server tick must "
                               "process all entities — at this count, each tick takes far too long.",
                    "likely_reason": "Overdue wipe, decay disabled or too low, massive bases, or loot accumulation",
                    "fix": "Map wipe is the most effective fix. If not possible: check decay.scale in server.cfg, "
                           "use an entity cleanup plugin, or manually remove abandoned bases.",
                })
            elif entity_count > 200000:
                findings.append({
                    "cause": "High entity count",
                    "severity": "high",
                    "details": f"{entity_count:,} entities (healthy: <150k). Performance is degrading. "
                               "You'll see FPS dropping and save times increasing.",
                    "likely_reason": "Long wipe cycle, large player base, or decay disabled",
                    "fix": "Schedule a wipe soon. In the meantime, check if decay.scale is > 0 in server.cfg, "
                           "and consider an entity cleanup plugin like RustCleaner.",
                })
            elif entity_count > 150000:
                findings.append({
                    "cause": "Entity count getting high",
                    "severity": "medium",
                    "details": f"{entity_count:,} entities. Not critical yet, but approaching the danger zone.",
                    "likely_reason": "Normal for mid-wipe on a busy server",
                    "fix": "Monitor this — if it keeps climbing above 200k, plan a wipe.",
                })

        # 3. Server resources (CPU, memory, disk, uptime)
        self._progress(f"[3/{total_steps}] Checking server resources (CPU/RAM/disk)...")
        cpu = mem_pct = mem_mb = mem_limit_mb = uptime_hrs = 0
        if self.ptero and self.server_id:
            try:
                resources = self.ptero.get_resources(self.server_id)
                res = resources.get("resources", {})
                cpu = res.get("cpu_absolute", 0)
                mem_bytes = res.get("memory_bytes", 0)
                mem_limit = res.get("memory_limit_bytes", 0)
                uptime_ms = res.get("uptime", 0)
                uptime_hrs = uptime_ms / (1000 * 3600) if uptime_ms else 0
                mem_mb = mem_bytes // (1024 * 1024)
                mem_limit_mb = mem_limit // (1024 * 1024) if mem_limit else 0
                mem_pct = (mem_bytes / mem_limit * 100) if mem_limit else 0

                raw["cpu"] = cpu
                raw["mem_pct"] = mem_pct
                raw["uptime_hrs"] = uptime_hrs

                # Evaluate CPU usage against the allocated limit
                # cpu_absolute is % of a single core (200% = 2 full cores)
                # CPU limit: 0 = unlimited, otherwise the cap in same units
                cpu_limit = self._server_limits.get("cpu", 0)
                raw["cpu_limit"] = cpu_limit

                if cpu_limit and cpu_limit > 0:
                    # Server has a CPU cap — check usage as % of that cap
                    cpu_pct_of_limit = (cpu / cpu_limit) * 100
                    if cpu_pct_of_limit > 95:
                        findings.append({
                            "cause": "CPU at allocation limit",
                            "severity": "critical",
                            "details": f"CPU at {cpu:.1f}% of {cpu_limit}% limit "
                                       f"({cpu_pct_of_limit:.0f}% of allocation used). "
                                       "The server is hitting its CPU cap — Pterodactyl will throttle it, "
                                       "which directly causes rubber-banding.",
                            "likely_reason": "CPU allocation too low for current server load, "
                                             "or plugins/entities consuming too much CPU",
                            "fix": "Increase CPU limit in Pterodactyl, reduce entity count, "
                                   "or identify CPU-hungry plugins.",
                        })
                    elif cpu_pct_of_limit > 80:
                        findings.append({
                            "cause": "CPU nearing allocation limit",
                            "severity": "high",
                            "details": f"CPU at {cpu:.1f}% of {cpu_limit}% limit "
                                       f"({cpu_pct_of_limit:.0f}% of allocation used). "
                                       "Approaching the cap — throttling may start soon.",
                            "likely_reason": "Growing server load approaching the CPU allocation",
                            "fix": "Monitor closely. Consider increasing CPU limit or reducing load.",
                        })
                else:
                    # CPU limit is 0 (unlimited) — >100% just means using multiple cores
                    # This is normal for Rust. Only flag if FPS is also low.
                    cpu_info = f"CPU at {cpu:.1f}% (no limit set — using ~{cpu/100:.1f} cores)"
                    raw["cpu_info"] = cpu_info
                    # We don't flag unlimited CPU as a problem on its own.
                    # The cross-correlation engine will catch it if FPS is also low.

                if mem_pct > 90:
                    findings.append({
                        "cause": "Memory nearly full",
                        "severity": "critical",
                        "details": f"Memory at {mem_pct:.1f}% ({mem_mb}MB / {mem_limit_mb}MB). "
                                   "When memory runs out, the server will either crash (OOM kill) or "
                                   "start swapping to disk, which causes massive lag spikes.",
                        "likely_reason": "Large map + many plugins + long uptime = memory leak accumulation",
                        "fix": "Restart the server to free leaked memory. If it fills up again quickly, "
                               "identify and remove memory-leaking plugins or increase the memory limit.",
                    })
                elif mem_pct > 80:
                    findings.append({
                        "cause": "High memory usage",
                        "severity": "high",
                        "details": f"Memory at {mem_pct:.1f}% ({mem_mb}MB / {mem_limit_mb}MB). "
                                   "Risk of OOM if it keeps growing.",
                        "likely_reason": "Memory growth over time — possible leak in a plugin",
                        "fix": "Plan a restart soon. Check if memory keeps growing after restart to identify leaks.",
                    })

                if uptime_hrs > 72 and mem_pct > 70:
                    findings.append({
                        "cause": "Server running without restart for a long time",
                        "severity": "medium",
                        "details": f"Server has been up for {uptime_hrs:.0f} hours ({uptime_hrs/24:.1f} days) "
                                   f"and memory is at {mem_pct:.1f}%. Rust servers accumulate memory leaks "
                                   "over time — periodic restarts help.",
                        "likely_reason": "Normal memory growth + plugin leaks over extended uptime",
                        "fix": "Schedule daily or twice-daily restarts during low-population hours.",
                    })
            except Exception as e:
                findings.append({
                    "cause": "Could not check server resources",
                    "severity": "medium",
                    "details": f"Pterodactyl resource check failed: {e}. "
                               "Without resource data, we can't tell if CPU or memory is the problem.",
                    "likely_reason": "API key permissions or connection issue",
                    "fix": "Check Pterodactyl connection and API key permissions.",
                })

        # 4. Plugin performance (perf hooks)
        # "perf" alone just shows/toggles the perf level setting.
        # We need to enable profiling (perf 2), wait for data to accumulate,
        # then read the results. If perf is already enabled, just read.
        self._progress(f"[4/{total_steps}] Profiling plugin hook performance...")
        # Enable perf level 6 (detailed hooks) then read after a short pause
        self._safe_rcon("perf 6")
        time.sleep(3)  # Let profiling data accumulate for a few seconds
        perf_result = self._safe_rcon("perf 0")  # Disable and read results
        raw["perf"] = perf_result
        if perf_result:
            slow_hooks = self._parse_perf_hooks(perf_result)
            if slow_hooks:
                # Calculate total time stolen from each tick
                total_ms = sum(h["time"] for h in slow_hooks)
                hook_list = "\n".join(f"  • {h['name']}: {h['time']:.1f}ms/call" for h in slow_hooks[:8])
                if total_ms > 20:
                    findings.append({
                        "cause": "Plugins consuming too much tick time",
                        "severity": "critical" if total_ms > 50 else "high",
                        "details": f"Slow plugin hooks are consuming ~{total_ms:.0f}ms per tick "
                                   f"(a 30fps tick budget is 33ms — these plugins alone use "
                                   f"{total_ms/33*100:.0f}% of it):\n{hook_list}",
                        "likely_reason": "Poorly optimized plugins running expensive operations every tick",
                        "fix": f"The worst offender is '{slow_hooks[0]['name']}' at {slow_hooks[0]['time']:.1f}ms. "
                               "Try disabling or replacing it and check if FPS improves. "
                               "Use `oxide.unload PluginName` to test.",
                    })
                elif slow_hooks:
                    hook_list_short = ", ".join(f"{h['name']} ({h['time']:.1f}ms)" for h in slow_hooks[:3])
                    findings.append({
                        "cause": "Some slow plugin hooks detected",
                        "severity": "medium",
                        "details": f"Found {len(slow_hooks)} slow hooks totaling ~{total_ms:.0f}ms: {hook_list_short}",
                        "likely_reason": "Plugins with room for optimization",
                        "fix": "Not critical unless FPS is low, but worth keeping an eye on.",
                    })

        # 5. Network and player pings
        self._progress(f"[5/{total_steps}] Checking player connections and ping...")
        status_result = self._safe_rcon("status")
        raw["status"] = status_result
        total_players = 0
        if status_result:
            high_ping = self._parse_high_ping_players(status_result)
            total_players = self._count_players(status_result)
            raw["players"] = total_players
            if high_ping and total_players:
                pct = (len(high_ping) / total_players) * 100
                player_list = ", ".join(f"{p['name']} ({p['ping']}ms)" for p in high_ping[:5])
                if pct > 50:
                    findings.append({
                        "cause": "Widespread high ping — possible network issue",
                        "severity": "high",
                        "details": f"{len(high_ping)}/{total_players} players ({pct:.0f}%) have ping >150ms: "
                                   f"{player_list}. When >50% of players have high ping, it usually "
                                   "indicates a server-side network problem, not individual client issues.",
                        "likely_reason": "Server network saturation, hosting provider routing issue, or DDoS",
                        "fix": "Check with your hosting provider. Look at network TX/RX in Pterodactyl. "
                               "If under DDoS, enable DDoS protection or contact host.",
                    })
                elif len(high_ping) > 3:
                    findings.append({
                        "cause": "Several players with high ping",
                        "severity": "medium",
                        "details": f"{len(high_ping)}/{total_players} players with ping >150ms: {player_list}",
                        "likely_reason": "Players connecting from far away or on poor connections",
                        "fix": "Likely client-side. These players may experience rubber-banding "
                               "that other players don't see.",
                    })

        # 6. Save performance — do saves cause stutters?
        self._progress(f"[6/{total_steps}] Testing world save performance...")
        try:
            start = time.monotonic()
            save_result = self.rcon.command("server.save", timeout=120) if self.rcon else None
            save_time = time.monotonic() - start if save_result is not None else None
            raw["save_time"] = save_time
            if save_time is not None:
                if save_time > 10:
                    findings.append({
                        "cause": "World saves causing lag spikes",
                        "severity": "high",
                        "details": f"World save took {save_time:.1f}s. During this time, ALL players "
                                   f"experience a freeze/stutter. With default save interval, this happens "
                                   f"every 10 minutes.",
                        "likely_reason": "Too many entities to serialize quickly — same root cause as low FPS",
                        "fix": "Reduce entity count (wipe/cleanup). You can increase server.saveinterval "
                               "to reduce frequency, but saves will get even longer as entities grow.",
                    })
                elif save_time > 5:
                    findings.append({
                        "cause": "Save times getting long",
                        "severity": "medium",
                        "details": f"World save took {save_time:.1f}s. Players may notice brief stutters "
                                   "every save interval.",
                        "likely_reason": "Growing entity count",
                        "fix": "Monitor this. If it gets above 10s, it will cause noticeable lag spikes.",
                    })
        except Exception:
            pass

        # 7. Check Oxide health and erroring plugins
        self._progress(f"[7/{total_steps}] Checking plugin health (errors/crashes)...")
        oxide_result = self._safe_rcon("oxide.plugins")
        if oxide_result:
            errored_plugins = []
            for line in oxide_result.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                # Oxide plugin lines: 87 "Plugin Name" (ver) by Author ... - File.cs
                # Strip the quoted plugin name before checking for error keywords,
                # so "Nav Mesh Error Fix" doesn't trigger a false positive.
                line_without_name = re.sub(r'"[^"]*"', '""', stripped)
                if any(kw in line_without_name.lower() for kw in
                       ("error", "failed", "crash", "unloaded", "not loaded")):
                    errored_plugins.append(stripped[:120])
            if errored_plugins:
                findings.append({
                    "cause": f"{len(errored_plugins)} plugin(s) in error state",
                    "severity": "high" if len(errored_plugins) > 3 else "medium",
                    "details": "Errored/crashed plugins can cause lag through repeated error logging, "
                               "failed hook calls, or resource leaks:\n" +
                               "\n".join(f"  • {p}" for p in errored_plugins[:10]),
                    "likely_reason": "Plugin incompatibility, outdated plugin, or missing dependency",
                    "fix": "Reload errored plugins with `oxide.reload PluginName`. If they keep crashing, "
                           "remove them or check for updates.",
                })

        # 8. Check for GC pressure
        self._progress(f"[8/{total_steps}] Checking garbage collection pressure...")
        gc_result = self._safe_rcon("gc.collect")
        if gc_result:
            # Parse memory freed from GC
            gc_numbers = re.findall(r'(\d+(?:\.\d+)?)\s*(?:MB|mb)', gc_result)
            if gc_numbers:
                freed = max(float(n) for n in gc_numbers)
                if freed > 500:
                    findings.append({
                        "cause": "Significant garbage collection pressure",
                        "severity": "medium",
                        "details": f"GC freed ~{freed:.0f}MB. Large GC collections can cause "
                                   "brief frame hitches. This often indicates plugins creating "
                                   "a lot of temporary objects.",
                        "likely_reason": "Plugin memory allocation patterns or large map",
                        "fix": "Schedule periodic `gc.collect` via a timer plugin to prevent buildup.",
                    })

        # 9. Check server config for lag-inducing settings
        self._progress(f"[9/{total_steps}] Checking server configuration...")
        if self.ptero and self.server_id:
            try:
                cfg = self.ptero.rust_get_server_cfg(self.server_id)
                if cfg:
                    settings = {}
                    for line in cfg.splitlines():
                        line = line.strip()
                        if not line or line.startswith("//") or line.startswith("#"):
                            continue
                        parts = line.split(None, 1)
                        if len(parts) == 2:
                            settings[parts[0].lower()] = parts[1].strip('"').strip("'")

                    config_issues = []
                    decay = settings.get("decay.scale", "1")
                    if decay == "0":
                        config_issues.append(
                            "decay.scale is 0 — entities NEVER decay. This is the #1 config cause of "
                            "entity buildup and lag. Even setting it to 0.5 would help enormously."
                        )
                    elif decay and float(decay) < 0.5:
                        config_issues.append(
                            f"decay.scale is {decay} — very low decay means entities accumulate faster "
                            "than they disappear, leading to eventual lag."
                        )

                    tick_rate = settings.get("server.tickrate")
                    if tick_rate and int(tick_rate) < 30:
                        config_issues.append(
                            f"server.tickrate is {tick_rate} — below 30 will feel laggy to players. "
                            "Set to 30 unless you have a specific reason not to."
                        )

                    si = settings.get("server.saveinterval")
                    if si:
                        try:
                            si_val = int(si)
                            if si_val < 300:
                                config_issues.append(
                                    f"server.saveinterval is {si_val}s — very frequent saves "
                                    "cause repeated lag spikes. Recommend 600 (default)."
                                )
                        except ValueError:
                            pass

                    stability = settings.get("server.stability", "true")
                    if stability.lower() == "false":
                        config_issues.append(
                            "server.stability is false — allows floating bases that create many extra entities."
                        )

                    if config_issues:
                        findings.append({
                            "cause": "Server configuration contributing to lag",
                            "severity": "high" if "decay.scale is 0" in str(config_issues) else "medium",
                            "details": "Found config settings that contribute to lag:\n" +
                                       "\n".join(f"  • {i}" for i in config_issues),
                            "likely_reason": "Server configured with settings that allow entity/performance problems",
                            "fix": "Edit server.cfg via Pterodactyl file manager. Key fixes: "
                                   "set decay.scale to 1, server.tickrate to 30, server.saveinterval to 600.",
                        })
            except Exception:
                pass

        # 10. Scan all server logs for errors/issues
        self._progress(f"[10/{total_steps}] Scanning server logs (Oxide, Steam, console)...")
        log_result = self.check_server_logs()
        if log_result and log_result.get("status") in ("warning", "critical"):
            findings.append({
                "cause": "Errors found in server logs",
                "severity": "medium" if log_result.get("severity") == "medium" else "high",
                "details": log_result.get("details", "Log errors detected"),
                "likely_reason": "Plugin errors, server instability, or configuration issues",
                "fix": "Review the errors above. Plugin errors often indicate outdated or "
                       "incompatible plugins. Repeated errors can cause performance degradation.",
            })

        # 11. Cross-correlate and build root cause analysis
        self._progress(f"[11/{total_steps}] Analyzing root cause...")
        root_cause = self._identify_root_cause(raw, findings)
        if root_cause:
            findings.insert(0, root_cause)

        # Sort by severity
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        findings.sort(key=lambda f: severity_order.get(f["severity"], 99))

        return {
            "lag_report": True,
            "findings": findings,
            "summary": self._build_lag_summary(findings),
            "raw_data": {
                "fps": raw.get("fps"),
                "entities": raw.get("entities"),
                "cpu": raw.get("cpu"),
                "mem_pct": raw.get("mem_pct"),
                "players": raw.get("players"),
                "uptime_hrs": raw.get("uptime_hrs"),
                "save_time": raw.get("save_time"),
            },
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }

    def _identify_root_cause(self, raw, findings):
        """Cross-correlate collected data to identify the most likely root cause."""
        fps = raw.get("fps")
        entities = raw.get("entities")
        cpu = raw.get("cpu")
        mem_pct = raw.get("mem_pct")

        # No issues found at all
        if not findings:
            return None

        # Only return a root cause analysis if we have enough data
        if fps is None and entities is None and cpu is None:
            return None

        # Cross-correlation patterns
        if entities and entities > 200000 and fps and fps < 20:
            return {
                "cause": "ROOT CAUSE: Entity overload is killing server performance",
                "severity": "critical",
                "details": f"The server has {entities:,} entities and is running at only {fps:.0f} FPS. "
                           "This is the classic Rust performance death spiral: too many entities → "
                           "each tick takes too long → FPS drops → rubber-banding.\n\n"
                           "Every entity (building block, deployed item, dropped loot, NPC) must be "
                           "processed every server tick. At this count, there is simply too much work "
                           "for the CPU to handle within a 33ms tick budget.",
                "likely_reason": "Overdue map wipe or decay disabled/too low",
                "fix": "The only real fix is reducing entity count. Options:\n"
                       "  1. Map wipe (most effective, instantly fixes lag)\n"
                       "  2. Enable/increase decay (server.cfg: decay.scale 1.0)\n"
                       "  3. Use cleanup plugins (RustCleaner, DecaySpeed)\n"
                       "  4. Manually remove massive abandoned bases",
            }

        # CPU root cause — only if there's a limit and we're hitting it
        cpu_limit = raw.get("cpu_limit", 0)
        cpu_is_capped = False
        if cpu and cpu_limit and cpu_limit > 0:
            cpu_is_capped = (cpu / cpu_limit) * 100 > 90

        if cpu_is_capped and fps and fps < 20 and (not entities or entities < 200000):
            return {
                "cause": "ROOT CAUSE: CPU hitting allocation limit (not entity-related)",
                "severity": "critical",
                "details": f"CPU is at {cpu:.1f}% of {cpu_limit}% limit, but entity count is "
                           f"{'only ' + f'{entities:,}' if entities else 'unknown'}. "
                           "The server is being throttled by Pterodactyl's CPU cap. "
                           "Something other than entities is consuming CPU — "
                           "likely one or more plugins running expensive operations.",
                "likely_reason": "Plugin performance issue, CPU allocation too low, "
                                 "or too many players for server allocation",
                "fix": "Increase CPU limit in Pterodactyl, or check plugin performance findings above. "
                       "Disable plugins one at a time to identify the CPU hog.",
            }

        if mem_pct and mem_pct > 90:
            return {
                "cause": "ROOT CAUSE: Server is running out of memory",
                "severity": "critical",
                "details": f"Memory is at {mem_pct:.1f}%. When a Rust server hits its memory limit, "
                           "it either gets OOM-killed by the system or starts swapping to disk. "
                           "Disk-based swap is orders of magnitude slower than RAM, causing "
                           "massive lag spikes that look like freezing.",
                "likely_reason": "Memory leak in plugin(s), too many plugins loaded, or insufficient memory allocation",
                "fix": "Restart the server immediately to free leaked memory. Then monitor if it "
                       "fills up again quickly — if so, a plugin is leaking.",
            }

        return None

    # ------------------------------------------------------------------
    # Log reading helpers (with path discovery and caching)
    # ------------------------------------------------------------------

    def _discover_oxide_log_dir(self):
        """Find the actual Oxide log directory dynamically.

        Uses the Pterodactyl API to list /server/rust/ and find the actual
        Oxide folder name, then discovers the logs subdirectory within it.
        Falls back to trying hardcoded path variants.

        Caches the result so subsequent reads don't repeat the discovery.
        """
        if self._oxide_log_dir:
            return self._oxide_log_dir
        if not self.ptero or not self.server_id:
            return None

        # Dynamic discovery: find actual Oxide root, then its logs subdir
        oxide_root = self.ptero._discover_oxide_root(self.server_id)
        if oxide_root:
            subdirs = self.ptero._discover_oxide_subdirs(self.server_id, oxide_root)
            if "logs" in subdirs:
                try:
                    files = self.ptero.list_files(self.server_id, subdirs["logs"])
                    if files:
                        self._oxide_log_dir = subdirs["logs"]
                        return self._oxide_log_dir
                except Exception:
                    pass

        # Fallback to hardcoded paths
        for path in self._OXIDE_LOG_DIRS:
            try:
                files = self.ptero.list_files(self.server_id, path)
                if files:
                    self._oxide_log_dir = path
                    return path
            except Exception:
                continue
        return None

    def _read_oxide_log(self, filename):
        """Read an Oxide log file, trying all known directory casings.

        Triggers dynamic directory discovery if the log dir isn't cached yet.
        """
        if not self.ptero or not self.server_id:
            return None

        # Ensure the log directory is discovered first
        if not self._oxide_log_dir:
            self._discover_oxide_log_dir()

        # Try cached path first
        if self._oxide_log_dir:
            try:
                content = self.ptero.get_file_contents(
                    self.server_id,
                    f"{self._oxide_log_dir}/{filename}"
                )
                if content:
                    return content
            except Exception:
                pass

        # Fall through to trying all hardcoded paths
        for log_dir in self._OXIDE_LOG_DIRS:
            if log_dir == self._oxide_log_dir:
                continue  # Already tried
            try:
                content = self.ptero.get_file_contents(
                    self.server_id,
                    f"{log_dir}/{filename}"
                )
                if content:
                    self._oxide_log_dir = log_dir  # cache it
                    return content
            except Exception:
                continue
        return None

    def _discover_steam_log_dir(self):
        """Find the actual Steam logs directory dynamically.

        Searches multiple container root paths since the layout varies
        by Pterodactyl egg. The path might be /Steam/logs, /server/rust/Steam/logs,
        /home/container/Steam/logs, etc.
        """
        if not self.ptero or not self.server_id:
            return None

        search_roots = [
            "/server/rust",
            "/",
            "/home/container",
            "/server",
        ]

        for root in search_roots:
            try:
                root_files = self.ptero.list_files(self.server_id, root)
                for f in root_files:
                    if not f["is_file"] and f["name"].lower() == "steam":
                        steam_root = f"{root}/{f['name']}" if root != "/" else f"/{f['name']}"
                        # Look for logs subdir
                        try:
                            steam_contents = self.ptero.list_files(
                                self.server_id, steam_root)
                            for sf in steam_contents:
                                if not sf["is_file"] and sf["name"].lower() == "logs":
                                    return f"{steam_root}/{sf['name']}"
                        except Exception:
                            pass
            except Exception:
                continue

        # Hardcoded fallback
        for path in ["/Steam/logs", "/server/rust/Steam/logs",
                     "/steam/logs", "/server/rust/steam/logs",
                     "/home/container/Steam/logs"]:
            try:
                files = self.ptero.list_files(self.server_id, path)
                if files:
                    return path
            except Exception:
                continue
        return None

    def _read_steam_logs(self, max_files=5):
        """Read Steam log files.

        Discovers the actual Steam logs directory dynamically.
        Returns list of (filename, content) tuples.
        """
        if not self.ptero or not self.server_id:
            return []

        steam_dir = self._discover_steam_log_dir()
        if not steam_dir:
            return []

        try:
            files = self.ptero.list_files(self.server_id, steam_dir)
            logs = sorted(
                [f for f in files if f["is_file"]],
                key=lambda f: f.get("modified_at", ""),
                reverse=True,
            )
            results = []
            for sf in logs[:max_files]:
                try:
                    content = self.ptero.get_file_contents(
                        self.server_id, f"{steam_dir}/{sf['name']}"
                    )
                    if content:
                        results.append((sf["name"], content))
                except Exception:
                    continue
            return results
        except Exception:
            return []

    def _read_console_logs(self):
        """Read server console output logs.

        Searches multiple container roots for log-like files since the
        layout varies by Pterodactyl egg.
        Returns list of (filename, content) tuples.
        """
        if not self.ptero or not self.server_id:
            return []

        results = []
        seen_names = set()

        # Check hardcoded paths across multiple roots
        roots = ["/server/rust", "/", "/home/container", "/server"]
        console_files = [
            "RustDedicated_Data/output_log.txt",
            "output_log.txt",
            "server_console.log",
        ]
        for root in roots:
            for cf in console_files:
                path = f"{root}/{cf}" if root != "/" else f"/{cf}"
                fname = cf.split("/")[-1]
                if fname in seen_names:
                    continue
                try:
                    content = self.ptero.get_file_contents(self.server_id, path)
                    if content:
                        results.append((fname, content))
                        seen_names.add(fname)
                except Exception:
                    continue

        # Scan each root for .log files and crash/error/dump files
        for root in roots:
            try:
                root_files = self.ptero.list_files(self.server_id, root)
                for f in root_files:
                    if not f["is_file"]:
                        continue
                    name = f["name"]
                    name_lower = name.lower()
                    if name in seen_names:
                        continue
                    if (name_lower.endswith(".log") or
                        ("log" in name_lower and name_lower.endswith(".txt")) or
                        "crash" in name_lower or "error" in name_lower or
                        "dump" in name_lower):
                        path = f"{root}/{name}" if root != "/" else f"/{name}"
                        try:
                            content = self.ptero.get_file_contents(
                                self.server_id, path)
                            if content:
                                results.append((name, content))
                                seen_names.add(name)
                        except Exception:
                            continue
            except Exception:
                continue

        return results

    # ------------------------------------------------------------------
    # Individual diagnostic checks
    # ------------------------------------------------------------------

    def check_server_fps(self):
        """Check server tick rate (FPS). Target is ~30 for Rust."""
        result = self._safe_rcon("fps")
        if result is None:
            return None

        fps = self._parse_fps(result)
        if fps is None:
            return self._ok("check_server_fps", f"FPS output: {result[:200]}", "performance")

        if fps < 10:
            return {
                "name": "check_server_fps",
                "status": "critical",
                "severity": "high",
                "details": f"Server FPS: {fps} (target: 30). Severe lag expected. "
                           "Players will experience constant rubber-banding.",
                "fix": [
                    {"command_rcon": "entity.count", "description": "Check entity count", "destructive": False},
                    {"command_rcon": "gc.collect", "description": "Force garbage collection", "destructive": False},
                    {"command_rcon": "pool.clear_prefabs", "description": "Clear prefab pool", "destructive": False},
                ],
                "category": "performance",
            }
        elif fps < 20:
            return {
                "name": "check_server_fps",
                "status": "warning",
                "severity": "high",
                "details": f"Server FPS: {fps} (target: 30). Players may experience rubber-banding.",
                "fix": [
                    {"command_rcon": "entity.count", "description": "Check entity count", "destructive": False},
                    {"command_rcon": "gc.collect", "description": "Force garbage collection", "destructive": False},
                ],
                "category": "performance",
            }
        elif fps < 28:
            return {
                "name": "check_server_fps",
                "status": "info",
                "severity": "medium",
                "details": f"Server FPS: {fps} — slightly below target of 30.",
                "fix": None,
                "category": "performance",
            }
        return self._ok("check_server_fps", f"Server FPS: {fps} — healthy", "performance")

    def check_entity_count(self):
        """Check total entity count. High counts cause lag."""
        result = self._safe_rcon("entity.count")
        if result is None:
            return None

        count = self._parse_entity_count(result)
        if count is None:
            return self._ok("check_entity_count", f"Entity output: {result[:200]}", "performance")

        if count > 300000:
            return {
                "name": "check_entity_count",
                "status": "critical",
                "severity": "high",
                "details": f"Entity count: {count:,}. This is extremely high and the primary cause "
                           "of server lag. A map wipe is strongly recommended.",
                "fix": [
                    {"command_rcon": "server.save", "description": "Save before any cleanup", "destructive": False},
                ],
                "category": "performance",
            }
        elif count > 200000:
            return {
                "name": "check_entity_count",
                "status": "warning",
                "severity": "high",
                "details": f"Entity count: {count:,}. Getting high — performance will degrade.",
                "fix": None,
                "category": "performance",
            }
        elif count > 150000:
            return {
                "name": "check_entity_count",
                "status": "info",
                "severity": "medium",
                "details": f"Entity count: {count:,}. Moderate — keep an eye on it.",
                "fix": None,
                "category": "performance",
            }
        return self._ok("check_entity_count", f"Entity count: {count:,} — healthy", "performance")

    def check_player_count(self):
        """Check player count and server capacity."""
        result = self._safe_rcon("status")
        if result is None:
            return None

        total = self._count_players(result)
        # Try to find max players
        info = self._safe_rcon("serverinfo")
        max_players = self._parse_max_players(info) if info else None

        details = f"Players online: {total}"
        if max_players:
            pct = (total / max_players) * 100
            details += f" / {max_players} ({pct:.0f}%)"
            if pct > 90:
                return {
                    "name": "check_player_count",
                    "status": "warning",
                    "severity": "medium",
                    "details": details + " — near capacity. Queue times likely.",
                    "fix": None,
                    "category": "players",
                }
        return self._ok("check_player_count", details, "players")

    def check_server_resources(self):
        """Check CPU/memory/disk via Pterodactyl API."""
        if not self.ptero or not self.server_id:
            return None

        try:
            resources = self.ptero.get_resources(self.server_id)
        except Exception as e:
            return {
                "name": "check_server_resources",
                "status": "error",
                "severity": "low",
                "details": f"Could not fetch resources: {e}",
                "fix": None,
                "category": "resources",
            }

        res = resources.get("resources", {})
        state = resources.get("current_state", "unknown")
        cpu = res.get("cpu_absolute", 0)
        mem_bytes = res.get("memory_bytes", 0)
        mem_limit = res.get("memory_limit_bytes", 0)
        disk_bytes = res.get("disk_bytes", 0)
        net_rx = res.get("network_rx_bytes", 0)
        net_tx = res.get("network_tx_bytes", 0)
        uptime = res.get("uptime", 0)

        mem_mb = mem_bytes // (1024 * 1024)
        mem_limit_mb = mem_limit // (1024 * 1024) if mem_limit else 0
        mem_pct = (mem_bytes / mem_limit) * 100 if mem_limit else 0
        disk_mb = disk_bytes // (1024 * 1024)
        uptime_hrs = uptime // (1000 * 3600)

        # CPU limit from Pterodactyl: 0 = unlimited
        cpu_limit = self._server_limits.get("cpu", 0)
        if cpu_limit and cpu_limit > 0:
            cpu_pct_of_limit = (cpu / cpu_limit) * 100
            cpu_str = f"CPU: {cpu:.1f}% of {cpu_limit}% limit ({cpu_pct_of_limit:.0f}% used)"
        else:
            cpu_str = f"CPU: {cpu:.1f}% (~{cpu/100:.1f} cores, no limit)"

        parts = [
            f"State: {state}",
            cpu_str,
            f"Memory: {mem_mb}MB / {mem_limit_mb}MB ({mem_pct:.1f}%)" if mem_limit_mb else f"Memory: {mem_mb}MB",
            f"Disk: {disk_mb}MB",
            f"Net: {net_rx // (1024*1024)}MB rx / {net_tx // (1024*1024)}MB tx",
            f"Uptime: {uptime_hrs}h",
        ]

        if state != "running":
            return {
                "name": "check_server_resources",
                "status": "critical",
                "severity": "high",
                "details": f"Server is {state}! " + " | ".join(parts),
                "fix": [
                    {"command_ptero": "power:start", "description": "Start the server", "destructive": False},
                ],
                "category": "resources",
            }

        # Only flag CPU as critical if there's an actual limit being hit
        if cpu_limit and cpu_limit > 0:
            cpu_pct_of_limit = (cpu / cpu_limit) * 100
            if cpu_pct_of_limit > 95:
                return {
                    "name": "check_server_resources",
                    "status": "critical",
                    "severity": "high",
                    "details": " | ".join(parts) + " — CPU hitting allocation limit!",
                    "fix": None,
                    "category": "resources",
                }
        # If unlimited, CPU >100% is normal multi-core usage — not a problem

        if mem_pct > 90:
            return {
                "name": "check_server_resources",
                "status": "critical",
                "severity": "high",
                "details": " | ".join(parts) + " — Memory almost full! Risk of OOM crash.",
                "fix": [
                    {"command_ptero": "power:restart", "description": "Restart to free memory", "destructive": True},
                ],
                "category": "resources",
            }

        # Check if CPU is approaching its limit (only meaningful with a limit set)
        cpu_warn = False
        if cpu_limit and cpu_limit > 0:
            cpu_warn = (cpu / cpu_limit) * 100 > 80
        if cpu_warn or mem_pct > 80:
            return {
                "name": "check_server_resources",
                "status": "warning",
                "severity": "medium",
                "details": " | ".join(parts),
                "fix": None,
                "category": "resources",
            }

        return self._ok("check_server_resources", " | ".join(parts), "resources")

    def check_network_quality(self):
        """Check for network-related issues using server status."""
        result = self._safe_rcon("status")
        if result is None:
            return None

        high_ping = self._parse_high_ping_players(result)
        total = self._count_players(result)

        if not total:
            return self._ok("check_network_quality", "No players online to measure", "network")

        if high_ping:
            pct = (len(high_ping) / total) * 100
            player_info = ", ".join(f"{p['name']}({p['ping']}ms)" for p in high_ping[:5])
            if pct > 50:
                return {
                    "name": "check_network_quality",
                    "status": "warning",
                    "severity": "high",
                    "details": f"{len(high_ping)}/{total} players ({pct:.0f}%) have ping >150ms: {player_info}. "
                               "Possible server-side network issue.",
                    "fix": None,
                    "category": "network",
                }
            return {
                "name": "check_network_quality",
                "status": "info",
                "severity": "low",
                "details": f"{len(high_ping)}/{total} players with high ping: {player_info}. "
                           "Likely client-side.",
                "fix": None,
                "category": "network",
            }

        return self._ok("check_network_quality", f"All {total} players have reasonable ping", "network")

    def check_oxide_health(self):
        """Check Oxide/uMod status and version."""
        result = self._safe_rcon("oxide.version")
        if result is None:
            # Try alternative
            result = self._safe_rcon("o.version")
            if result is None:
                return self._ok("check_oxide_health", "Oxide not detected (vanilla server?)", "plugins")

        plugins_result = self._safe_rcon("oxide.plugins")
        plugin_count = 0
        loaded = 0
        errored = 0
        if plugins_result:
            for line in plugins_result.splitlines():
                line = line.strip()
                if not line:
                    continue
                ll = line.lower()
                # Count plugin lines — they typically start with a number or
                # contain a quoted plugin name like "PluginName"
                is_plugin_line = bool(
                    re.match(r'^\d+\s', line) or  # "01 ..."
                    '"' in line  # Contains quoted plugin name
                )
                if is_plugin_line:
                    plugin_count += 1
                    # Check for loaded status — may say "loaded", or if it
                    # doesn't say "error"/"failed"/"unloaded", assume loaded
                    line_without_name = re.sub(r'"[^"]*"', '""', line)
                    lln = line_without_name.lower()
                    if "error" in lln or "failed" in lln:
                        errored += 1
                    elif "unloaded" in lln and "not loaded" not in lln:
                        pass  # Explicitly unloaded
                    else:
                        loaded += 1  # No error indicators = loaded
                elif "loaded" in ll:
                    # Summary line like "Listing 173 plugins:"
                    # or "173 plugin(s) loaded"
                    match = re.search(r'(\d+)\s*plugin', ll)
                    if match:
                        loaded = max(loaded, int(match.group(1)))

        # Cross-reference with Pterodactyl file listing for accurate count
        disk_plugin_count = 0
        if self.ptero and self.server_id:
            try:
                disk_plugins = self.ptero.rust_list_oxide_plugins(self.server_id)
                disk_plugin_count = len(disk_plugins)
            except Exception:
                pass

        # Use the higher of RCON-reported or disk count for accuracy
        effective_count = max(loaded, plugin_count, disk_plugin_count)

        details = f"Oxide: {result.strip()}" if result else "Oxide detected"
        if disk_plugin_count and disk_plugin_count != loaded:
            details += f" | Plugins: {disk_plugin_count} on disk, {loaded} reported loaded by RCON"
        else:
            details += f" | Plugins: {effective_count} loaded"
        if errored:
            details += f", {errored} with errors"
            return {
                "name": "check_oxide_health",
                "status": "warning",
                "severity": "medium",
                "details": details,
                "fix": [
                    {"command_rcon": "oxide.plugins", "description": "List all plugins with status", "destructive": False},
                    {"command_rcon": "oxide.reload *", "description": "Reload all plugins", "destructive": True},
                ],
                "category": "plugins",
            }

        return self._ok("check_oxide_health", details, "plugins")

    def check_plugin_errors(self):
        """Check for plugin errors in Oxide logs.

        Discovers the log directory, then scans up to 10 most recent log
        files (entire content) to catch errors across the full uptime period.
        """
        if not self.ptero or not self.server_id:
            return None

        try:
            # Ensure the log directory is discovered before reading files
            log_dir = self._discover_oxide_log_dir()

            log_files = self.ptero.rust_get_oxide_logs(self.server_id, limit=15)
            if not log_files:
                return {
                    "name": "check_plugin_errors",
                    "status": "info",
                    "severity": "low",
                    "details": f"No Oxide log files found. Log dir: {log_dir or 'not discovered'}",
                    "fix": None,
                    "category": "plugins",
                }

            errors = []
            files_scanned = 0
            total_lines = 0
            for log_file in log_files[:10]:
                content = self._read_oxide_log(log_file["name"])
                if not content:
                    continue
                files_scanned += 1
                lines = content.splitlines()
                total_lines += len(lines)
                for line in lines:
                    ll = line.lower()
                    # Only match actual error indicators, skip [Info] lines
                    if "[info]" in ll:
                        continue
                    if any(kw in ll for kw in
                           ("error", "exception", "nullref", "failed to",
                            "unloaded", "compilation failed", "stacktrace")):
                        errors.append({
                            "file": log_file["name"],
                            "line": line.strip()[:200],
                        })

            scan_info = f"Scanned {files_scanned} log file(s), {total_lines:,} lines"

            if errors:
                seen = {}
                for e in errors:
                    key = e["line"]
                    if key not in seen:
                        seen[key] = e
                unique_errors = list(seen.values())
                return {
                    "name": "check_plugin_errors",
                    "status": "warning",
                    "severity": "high" if len(unique_errors) > 20 else "medium",
                    "details": f"{scan_info}. Found {len(errors)} error(s) "
                               f"({len(unique_errors)} unique):\n" +
                               "\n".join(f"  - [{e['file']}] {e['line']}"
                                         for e in unique_errors[:10]),
                    "fix": [
                        {"command_rcon": "oxide.plugins", "description": "Check plugin status", "destructive": False},
                    ],
                    "category": "plugins",
                }
            return self._ok("check_plugin_errors",
                             f"No errors in Oxide logs ({scan_info})", "plugins")

        except Exception as e:
            return {
                "name": "check_plugin_errors",
                "status": "error",
                "severity": "low",
                "details": f"Could not check logs: {e}",
                "fix": None,
                "category": "plugins",
            }

    def check_server_config(self):
        """Check server.cfg for common misconfigurations."""
        if not self.ptero or not self.server_id:
            # Try via RCON
            info = self._safe_rcon("serverinfo")
            if info:
                return self._ok("check_server_config", f"Server info:\n{info[:500]}", "config")
            return None

        try:
            cfg = self.ptero.rust_get_server_cfg(self.server_id)
            if not cfg:
                return {
                    "name": "check_server_config",
                    "status": "info",
                    "severity": "low",
                    "details": "No server.cfg found — using defaults.",
                    "fix": None,
                    "category": "config",
                }

            issues = []
            settings = {}
            for line in cfg.splitlines():
                line = line.strip()
                if not line or line.startswith("//") or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                if len(parts) == 2:
                    settings[parts[0].lower()] = parts[1].strip('"').strip("'")

            # Check for common issues
            decay = settings.get("decay.scale", "1")
            if decay == "0":
                issues.append("decay.scale is 0 — entities will never decay, causing entity buildup")

            tick_rate = settings.get("server.tickrate")
            if tick_rate and int(tick_rate) < 30:
                issues.append(f"server.tickrate is {tick_rate} — should be 30 for best performance")

            stability = settings.get("server.stability", "true")
            if stability.lower() == "false":
                issues.append("server.stability is false — allows impossible builds that create more entities")

            save_interval = settings.get("server.saveinterval")
            if save_interval:
                try:
                    si = int(save_interval)
                    if si < 300:
                        issues.append(f"server.saveinterval is {si}s — frequent saves cause lag spikes")
                    elif si > 1200:
                        issues.append(f"server.saveinterval is {si}s — risk of data loss if crash occurs")
                except ValueError:
                    pass

            max_players = settings.get("server.maxplayers")
            world_size = settings.get("server.worldsize")
            if max_players and world_size:
                try:
                    mp = int(max_players)
                    ws = int(world_size)
                    # Rough guideline: 1 player per ~40,000 sq units
                    recommended_max = (ws * ws) // 40000
                    if mp > recommended_max * 1.5:
                        issues.append(
                            f"server.maxplayers ({mp}) seems high for worldsize {ws} "
                            f"(recommended ~{recommended_max})"
                        )
                except ValueError:
                    pass

            if issues:
                return {
                    "name": "check_server_config",
                    "status": "warning",
                    "severity": "medium",
                    "details": "Configuration issues found:\n" + "\n".join(f"  - {i}" for i in issues),
                    "fix": None,
                    "category": "config",
                }

            return self._ok("check_server_config", f"server.cfg looks good ({len(settings)} settings)", "config")

        except Exception as e:
            return {
                "name": "check_server_config",
                "status": "error",
                "severity": "low",
                "details": f"Config check failed: {e}",
                "fix": None,
                "category": "config",
            }

    def check_world_size(self):
        """Check map/world size and wipe age."""
        info = self._safe_rcon("serverinfo")
        if info is None:
            return None

        world_size = None
        save_count = None
        for line in info.splitlines():
            line_lower = line.lower().strip()
            if "worldsize" in line_lower or "world size" in line_lower:
                match = re.search(r'(\d+)', line)
                if match:
                    world_size = int(match.group(1))
            if "savecount" in line_lower or "save count" in line_lower:
                match = re.search(r'(\d+)', line)
                if match:
                    save_count = int(match.group(1))

        details_parts = []
        if world_size:
            details_parts.append(f"World size: {world_size}")
        if save_count:
            details_parts.append(f"Save count: {save_count}")
            # Rough age estimate (saves every 600s by default)
            est_hours = (save_count * 600) / 3600
            details_parts.append(f"Est. wipe age: ~{est_hours:.0f}h")

            if est_hours > 336:  # 14 days
                return {
                    "name": "check_world_size",
                    "status": "warning",
                    "severity": "medium",
                    "details": " | ".join(details_parts) + " — extended wipe cycle, expect entity buildup",
                    "fix": None,
                    "category": "performance",
                }

        if not details_parts:
            return None
        return self._ok("check_world_size", " | ".join(details_parts), "performance")

    def check_save_performance(self):
        """Check if world saves are causing lag spikes."""
        # This is detectable via timing a save command
        if not self.rcon:
            return None

        try:
            start = time.monotonic()
            result = self.rcon.command("server.save", timeout=60)
            elapsed = time.monotonic() - start

            if elapsed > 10:
                return {
                    "name": "check_save_performance",
                    "status": "critical",
                    "severity": "high",
                    "details": f"World save took {elapsed:.1f}s — this causes visible lag spikes for players. "
                               "Large saves indicate too many entities.",
                    "fix": None,
                    "category": "performance",
                }
            elif elapsed > 5:
                return {
                    "name": "check_save_performance",
                    "status": "warning",
                    "severity": "medium",
                    "details": f"World save took {elapsed:.1f}s — may cause brief stutters.",
                    "fix": None,
                    "category": "performance",
                }
            return self._ok("check_save_performance", f"Save completed in {elapsed:.1f}s — healthy", "performance")

        except Exception as e:
            return {
                "name": "check_save_performance",
                "status": "error",
                "severity": "low",
                "details": f"Save test failed: {e}",
                "fix": None,
                "category": "performance",
            }

    def check_garbage_collection(self):
        """Check GC pressure by running gc.collect and measuring time."""
        if not self.rcon:
            return None

        try:
            result = self.rcon.command("gc.collect")
            # Result often includes memory freed
            return self._ok("check_garbage_collection", f"GC result: {result.strip()[:200]}", "performance")
        except Exception:
            return None

    def check_process_health(self):
        """Check the RustDedicated process health.

        Tries SSH first, but on Pterodactyl/Docker setups the process runs
        inside a container where host-level ``ps`` can't see real stats.
        Falls back to Pterodactyl API resource data when SSH returns 0.0%
        or finds no process.
        """
        ssh_ok = False
        if self.ssh:
            try:
                result = self.ssh.execute(
                    "ps aux | grep -i '[R]ustDedicated' | head -5"
                )
                if result["stdout"].strip():
                    lines = result["stdout"].strip().splitlines()
                    for line in lines:
                        parts = line.split()
                        if len(parts) >= 4:
                            cpu = float(parts[2])
                            mem = float(parts[3])
                            # If both are 0.0%, we're likely looking at a
                            # containerised process — fall through to Ptero API
                            if cpu == 0.0 and mem == 0.0:
                                break
                            ssh_ok = True
                            details = f"RustDedicated — CPU: {cpu}%, MEM: {mem}%"
                            if cpu > 150:  # multi-core
                                return {
                                    "name": "check_process_health",
                                    "status": "warning",
                                    "severity": "medium",
                                    "details": details + " — heavy CPU usage",
                                    "fix": None,
                                    "category": "process",
                                }
                            return self._ok("check_process_health", details, "process")
            except Exception:
                pass

        # Fall back to Pterodactyl API resource data (works inside containers)
        if not ssh_ok and self.ptero and self.server_id:
            try:
                resources = self.ptero.get_resources(self.server_id)
                res = resources.get("resources", {})
                state = resources.get("current_state", "unknown")
                cpu = res.get("cpu_absolute", 0)
                mem_bytes = res.get("memory_bytes", 0)
                mem_limit = res.get("memory_limit_bytes", 0)
                mem_mb = mem_bytes // (1024 * 1024)
                mem_limit_mb = mem_limit // (1024 * 1024) if mem_limit else 0
                mem_pct = (mem_bytes / mem_limit * 100) if mem_limit else 0

                if state != "running":
                    return {
                        "name": "check_process_health",
                        "status": "critical",
                        "severity": "high",
                        "details": f"Server state: {state} — process is not running!",
                        "fix": None,
                        "category": "process",
                    }

                details = (
                    f"RustDedicated (via Pterodactyl) — CPU: {cpu:.1f}%, "
                    f"MEM: {mem_mb}MB"
                )
                if mem_limit_mb:
                    details += f" / {mem_limit_mb}MB ({mem_pct:.1f}%)"

                if cpu > 150:
                    return {
                        "name": "check_process_health",
                        "status": "warning",
                        "severity": "medium",
                        "details": details + " — heavy CPU usage",
                        "fix": None,
                        "category": "process",
                    }
                return self._ok("check_process_health", details, "process")
            except Exception as e:
                return {
                    "name": "check_process_health",
                    "status": "error",
                    "severity": "low",
                    "details": f"Process check failed: {e}",
                    "fix": None,
                    "category": "process",
                }

        if not self.ssh and not (self.ptero and self.server_id):
            return None

        return {
            "name": "check_process_health",
            "status": "critical",
            "severity": "high",
            "details": "No RustDedicated process found! Server may be down.",
            "fix": None,
            "category": "process",
        }

    def check_disk_space_game(self):
        """Check disk space on the game server directory."""
        if not self.ptero or not self.server_id:
            return None

        try:
            resources = self.ptero.get_resources(self.server_id)
            disk_bytes = resources.get("resources", {}).get("disk_bytes", 0)
            disk_mb = disk_bytes // (1024 * 1024)
            disk_gb = disk_mb / 1024

            # Also check for old log accumulation
            log_files = self.ptero.rust_get_oxide_logs(self.server_id, limit=100)
            total_log_size = sum(f.get("size", 0) for f in log_files)
            log_mb = total_log_size // (1024 * 1024)

            details = f"Disk usage: {disk_gb:.1f}GB"
            if log_mb > 100:
                details += f" | Oxide logs: {log_mb}MB (consider cleanup)"
                return {
                    "name": "check_disk_space_game",
                    "status": "info",
                    "severity": "low",
                    "details": details,
                    "fix": None,
                    "category": "resources",
                }

            return self._ok("check_disk_space_game", details, "resources")

        except Exception as e:
            return {
                "name": "check_disk_space_game",
                "status": "error",
                "severity": "low",
                "details": f"Disk check failed: {e}",
                "fix": None,
                "category": "resources",
            }

    def check_rust_update_status(self):
        """Check if the Rust server is up to date."""
        info = self._safe_rcon("serverinfo")
        if info is None:
            return None

        # Look for version/protocol info
        version = None
        for line in info.splitlines():
            if "version" in line.lower() and "oxide" not in line.lower():
                version = line.strip()
                break

        if version:
            return self._ok("check_rust_update_status", f"Rust {version}", "updates")
        return None

    def check_oxide_update_status(self):
        """Check Oxide/uMod version."""
        result = self._safe_rcon("oxide.version")
        if result is None:
            return None

        return self._ok("check_oxide_update_status", f"Oxide: {result.strip()}", "updates")

    def check_recent_crashes(self):
        """Check for recent crash logs.

        Scans crash files on disk, Oxide logs (up to 10 files, full content),
        Steam logs, and console output for crash indicators.
        """
        if not self.ptero or not self.server_id:
            return None

        crash_indicators = []
        sources_checked = []

        # Use specific phrases to avoid false positives from game events.
        # "oom" matches "mushroom", "crash" matches "crashsite" monument,
        # so we use multi-word phrases or regex patterns instead.
        crash_keywords = (
            "crashed", "crashing",  # past/present tense avoids "crashsite"
            "segfault", "sigsegv", "sigkill", "sigabrt",
            "out of memory", "system.outofmemoryexception",
            "unhandled exception", "stack overflow",
            "access violation", "fatal error",
            "application crash", "server crash",
            "oom kill", "oom-kill", "oom_kill",
        )

        # 1. Check for crash files in /server/rust
        try:
            files = self.ptero.list_files(self.server_id, "/server/rust")
            crash_files = [
                f for f in files
                if f["is_file"] and ("crash" in f["name"].lower() or
                                     "error" in f["name"].lower() or
                                     "dump" in f["name"].lower())
            ]
            if crash_files:
                sources_checked.append("crash files")
                recent = sorted(crash_files, key=lambda f: f.get("modified_at", ""), reverse=True)
                for cf in recent[:5]:
                    crash_indicators.append(f"File: {cf['name']} ({cf.get('modified_at', 'unknown')})")
        except Exception:
            pass

        # 2. Scan Oxide logs — full content, up to 10 files
        try:
            log_files = self.ptero.rust_get_oxide_logs(self.server_id, limit=15)
            if log_files:
                sources_checked.append(f"{min(len(log_files), 10)} Oxide log files")
                for log_file in log_files[:10]:
                    content = self._read_oxide_log(log_file["name"])
                    if content:
                        for line in content.splitlines():
                            if any(kw in line.lower() for kw in crash_keywords):
                                crash_indicators.append(
                                    f"Oxide/{log_file['name']}: {line.strip()[:150]}")
        except Exception:
            pass

        # 3. Scan Steam logs — full content, up to 5 files
        try:
            steam_results = self._read_steam_logs(max_files=5)
            if steam_results:
                sources_checked.append(f"{len(steam_results)} Steam log files")
                for fname, content in steam_results:
                    for line in content.splitlines():
                        if any(kw in line.lower() for kw in crash_keywords):
                            crash_indicators.append(
                                f"Steam/{fname}: {line.strip()[:150]}")
        except Exception:
            pass

        # 4. Scan console output logs
        try:
            console_results = self._read_console_logs()
            if console_results:
                sources_checked.append("console output")
                for fname, content in console_results:
                    for line in content.splitlines():
                        if any(kw in line.lower() for kw in crash_keywords):
                            crash_indicators.append(
                                f"{fname}: {line.strip()[:150]}")
        except Exception:
            pass

        scan_info = f"Checked: {', '.join(sources_checked)}" if sources_checked else "No log sources accessible"

        if crash_indicators:
            unique = list(dict.fromkeys(crash_indicators))[:15]
            return {
                "name": "check_recent_crashes",
                "status": "warning",
                "severity": "high" if len(unique) > 5 else "medium",
                "details": f"{scan_info}. Found {len(unique)} crash indicator(s):\n" +
                           "\n".join(f"  - {c}" for c in unique[:10]),
                "fix": None,
                "category": "stability",
            }

        return self._ok("check_recent_crashes",
                         f"No crash indicators found ({scan_info})", "stability")

    def check_connection_quality(self):
        """Check server network stats."""
        perf = self._safe_rcon("perf")
        if perf is None:
            return None

        # Look for network-related stats in perf output
        net_lines = [l for l in perf.splitlines() if any(
            kw in l.lower() for kw in ("network", "packet", "net.", "bytes")
        )]
        if net_lines:
            return self._ok(
                "check_connection_quality",
                "Network stats:\n" + "\n".join(f"  {l.strip()}" for l in net_lines[:10]),
                "network",
            )
        return None

    def check_memory_leak_indicators(self):
        """Check for signs of memory leaks (growing memory over time)."""
        if not self.ptero or not self.server_id:
            return None

        try:
            resources = self.ptero.get_resources(self.server_id)
            res = resources.get("resources", {})
            uptime_ms = res.get("uptime", 0)
            mem_bytes = res.get("memory_bytes", 0)
            mem_limit = res.get("memory_limit_bytes", 0)

            if not uptime_ms or not mem_bytes:
                return None

            uptime_hours = uptime_ms / (1000 * 3600)
            mem_mb = mem_bytes // (1024 * 1024)
            mem_pct = (mem_bytes / mem_limit * 100) if mem_limit else 0

            # Heuristic: if memory > 80% and uptime > 12h, likely a leak
            if mem_pct > 85 and uptime_hours > 12:
                return {
                    "name": "check_memory_leak_indicators",
                    "status": "warning",
                    "severity": "high",
                    "details": f"Memory at {mem_pct:.1f}% after {uptime_hours:.1f}h uptime ({mem_mb}MB). "
                               "Possible memory leak — consider a scheduled restart.",
                    "fix": [
                        {"command_ptero": "power:restart", "description": "Restart to free memory", "destructive": True},
                    ],
                    "category": "performance",
                }
            elif mem_pct > 70 and uptime_hours > 48:
                return {
                    "name": "check_memory_leak_indicators",
                    "status": "info",
                    "severity": "medium",
                    "details": f"Memory at {mem_pct:.1f}% after {uptime_hours:.1f}h. "
                               "Monitor for gradual increase.",
                    "fix": None,
                    "category": "performance",
                }
            return None

        except Exception:
            return None

    def _discover_all_log_sources(self):
        """Scan the container file system for ALL log directories and files.

        Walks the top two directory levels looking for directories named
        'logs', 'log', or files ending in .log/.txt that look like logs.
        Returns a list of (display_name, full_path, is_dir) tuples.
        """
        if not self.ptero or not self.server_id:
            return []

        sources = []
        seen_paths = set()

        search_roots = ["/", "/server/rust", "/home/container", "/server"]
        log_dir_names = {"logs", "log"}

        for root in search_roots:
            try:
                entries = self.ptero.list_files(self.server_id, root)
            except Exception:
                continue

            for entry in entries:
                name = entry["name"]
                name_lower = name.lower()
                full_path = f"{root}/{name}" if root != "/" else f"/{name}"

                if full_path in seen_paths:
                    continue
                seen_paths.add(full_path)

                if entry["is_file"]:
                    # Log-like files at the root level
                    if (name_lower.endswith(".log") or
                        name_lower.endswith(".txt") and
                        ("log" in name_lower or "crash" in name_lower or
                         "error" in name_lower or "output" in name_lower)):
                        sources.append((name, full_path, False))
                else:
                    # Check if this is a logs directory
                    if name_lower in log_dir_names:
                        sources.append((f"{root}/{name}", full_path, True))
                    else:
                        # Recurse one level into subdirectories to find logs/
                        try:
                            sub_entries = self.ptero.list_files(
                                self.server_id, full_path)
                            for sub in sub_entries:
                                sub_name = sub["name"]
                                sub_lower = sub["name"].lower()
                                sub_path = f"{full_path}/{sub_name}"
                                if sub_path in seen_paths:
                                    continue
                                seen_paths.add(sub_path)

                                if not sub["is_file"] and sub_lower in log_dir_names:
                                    sources.append((
                                        f"{name}/{sub_name}", sub_path, True))
                                elif sub["is_file"] and (
                                    sub_lower.endswith(".log") or
                                    (sub_lower.endswith(".txt") and
                                     "log" in sub_lower)):
                                    sources.append((
                                        f"{name}/{sub_name}", sub_path, False))
                        except Exception:
                            continue

        return sources

    def check_server_logs(self):
        """Comprehensive scan of ALL available server logs.

        Discovers all log directories and files across the entire container,
        not just known paths. Reads full content and classifies each line.
        """
        if not self.ptero or not self.server_id:
            return None

        issues = []
        sources_checked = []
        total_lines = 0
        total_files = 0

        # Keywords that indicate real problems (not just informational)
        # For Oxide logs: skip [Info] lines, only match [Error]/[Warning] or
        # lines with clear error indicators
        error_keywords = (
            "exception", "nullref", "nullreferenceexception",
            "failed to compile", "compilation failed",
            "stacktrace", "stack overflow", "out of memory",
            "access violation", "segfault",
        )
        # For non-Oxide logs (Steam, console): broader matching is ok
        broad_error_keywords = (
            "error", "exception", "failed", "crashed", "fatal",
            "timeout", "timed out",
        )
        warning_keywords = (
            "warning", "deprecated",
        )

        def _classify_oxide_line(line):
            """Classify an Oxide log line as error/warning/None."""
            ll = line.lower()
            # Oxide log format: [Oxide] HH:MM [Level] message
            # or: [PluginName] message
            if "[error]" in ll:
                return "error"
            if "[warning]" in ll or "[warn]" in ll:
                return "warning"
            # Skip [Info] lines entirely — they're not problems
            if "[info]" in ll:
                return None
            # Check for error indicators in non-tagged lines
            if any(kw in ll for kw in error_keywords):
                return "error"
            return None

        def _classify_general_line(line):
            """Classify a non-Oxide log line (Steam, console)."""
            ll = line.lower()
            if any(kw in ll for kw in broad_error_keywords):
                return "error"
            if any(kw in ll for kw in warning_keywords):
                return "warning"
            return None

        # 1. Scan Oxide logs — full content, up to 10 files
        try:
            # Ensure log dir is discovered
            self._discover_oxide_log_dir()
            log_files = self.ptero.rust_get_oxide_logs(self.server_id, limit=15)
            if log_files:
                oxide_files_read = 0
                for log_file in log_files[:10]:
                    content = self._read_oxide_log(log_file["name"])
                    if not content:
                        continue
                    oxide_files_read += 1
                    total_files += 1
                    lines = content.splitlines()
                    total_lines += len(lines)
                    for line in lines:
                        level = _classify_oxide_line(line)
                        if level:
                            issues.append({
                                "source": f"Oxide/{log_file['name']}",
                                "level": level,
                                "line": line.strip()[:200],
                            })
                if oxide_files_read:
                    sources_checked.append(
                        f"{oxide_files_read} Oxide log file(s)"
                        f" of {len(log_files)} available"
                    )
        except Exception:
            pass

        # 2. Scan Steam logs — full content, up to 5 files
        try:
            steam_results = self._read_steam_logs(max_files=5)
            if steam_results:
                sources_checked.append(f"{len(steam_results)} Steam log file(s)")
                for fname, content in steam_results:
                    total_files += 1
                    lines = content.splitlines()
                    total_lines += len(lines)
                    for line in lines:
                        level = _classify_general_line(line)
                        if level:
                            issues.append({
                                "source": f"Steam/{fname}",
                                "level": level,
                                "line": line.strip()[:200],
                            })
        except Exception:
            pass

        # 3. Scan console output logs — full content
        try:
            console_results = self._read_console_logs()
            if console_results:
                for fname, content in console_results:
                    sources_checked.append(fname)
                    total_files += 1
                    lines = content.splitlines()
                    total_lines += len(lines)
                    for line in lines:
                        # Console output mixes Oxide and engine output;
                        # use Oxide classifier for Oxide-tagged lines
                        if "[oxide]" in line.lower() or re.match(r'\[.*\]\s', line):
                            level = _classify_oxide_line(line)
                        else:
                            level = _classify_general_line(line)
                        if level:
                            issues.append({
                                "source": fname,
                                "level": level,
                                "line": line.strip()[:200],
                            })
        except Exception:
            pass

        # 4. Broad discovery — find any log dirs/files we missed above
        try:
            all_sources = self._discover_all_log_sources()
            already_scanned = set()
            for src_name in sources_checked:
                already_scanned.add(src_name.lower())

            for display_name, full_path, is_dir in all_sources:
                # Skip sources we already scanned
                if any(s.lower() in display_name.lower() for s in
                       ("oxide", "steam", "output_log", "server_console",
                        "Log.EAC", "latest.log")):
                    # Already covered by sections 1-3
                    if any(display_name.lower() in s.lower()
                           for s in sources_checked):
                        continue

                if is_dir:
                    # Read files from this log directory
                    try:
                        dir_files = self.ptero.list_files(
                            self.server_id, full_path)
                        log_entries = sorted(
                            [f for f in dir_files if f["is_file"]],
                            key=lambda f: f.get("modified_at", ""),
                            reverse=True,
                        )
                        dir_files_read = 0
                        for lf in log_entries[:5]:
                            try:
                                content = self.ptero.get_file_contents(
                                    self.server_id,
                                    f"{full_path}/{lf['name']}")
                                if content:
                                    dir_files_read += 1
                                    total_files += 1
                                    lines = content.splitlines()
                                    total_lines += len(lines)
                                    for line in lines:
                                        level = _classify_general_line(line)
                                        if level:
                                            issues.append({
                                                "source": f"{display_name}/{lf['name']}",
                                                "level": level,
                                                "line": line.strip()[:200],
                                            })
                            except Exception:
                                continue
                        if dir_files_read:
                            sources_checked.append(
                                f"{display_name} ({dir_files_read} files)")
                    except Exception:
                        continue
                else:
                    # Single file
                    try:
                        content = self.ptero.get_file_contents(
                            self.server_id, full_path)
                        if content:
                            total_files += 1
                            lines = content.splitlines()
                            total_lines += len(lines)
                            sources_checked.append(display_name)
                            for line in lines:
                                level = _classify_general_line(line)
                                if level:
                                    issues.append({
                                        "source": display_name,
                                        "level": level,
                                        "line": line.strip()[:200],
                                    })
                    except Exception:
                        continue
        except Exception:
            pass

        scan_summary = (
            f"Scanned {total_files} file(s), {total_lines:,} total lines "
            f"({', '.join(sources_checked)})"
            if sources_checked else
            "Could not access any log files"
        )

        if not sources_checked:
            return {
                "name": "check_server_logs",
                "status": "info",
                "severity": "low",
                "details": f"{scan_summary}. Checked Oxide, Steam, and console log paths "
                           "(all case variations).",
                "fix": None,
                "category": "logs",
            }

        errors = [i for i in issues if i["level"] == "error"]
        warnings = [i for i in issues if i["level"] == "warning"]

        if errors:
            # Deduplicate by line content, keep source info
            unique_errors = list({i["line"]: i for i in errors}.values())
            details = (
                f"{scan_summary}.\n"
                f"Found {len(errors)} error(s) ({len(unique_errors)} unique), "
                f"{len(warnings)} warning(s).\n"
                f"Errors:\n"
            )
            for e in unique_errors[:12]:
                details += f"  [{e['source']}] {e['line']}\n"
            if len(unique_errors) > 12:
                details += f"  ... and {len(unique_errors) - 12} more unique errors\n"
            return {
                "name": "check_server_logs",
                "status": "warning",
                "severity": "high" if len(unique_errors) > 20 else "medium",
                "details": details.strip(),
                "fix": None,
                "category": "logs",
            }

        if warnings:
            unique_warnings = list({i["line"]: i for i in warnings}.values())
            return {
                "name": "check_server_logs",
                "status": "info",
                "severity": "low",
                "details": f"{scan_summary}. No errors, "
                           f"{len(warnings)} warning(s) ({len(unique_warnings)} unique).",
                "fix": None,
                "category": "logs",
            }

        return self._ok(
            "check_server_logs",
            f"No issues found. {scan_summary}.",
            "logs",
        )

    def check_hook_performance(self):
        """Profile plugin hook execution times via RCON perf command.

        Enables level-6 profiling, waits briefly for data to accumulate,
        then reads and parses the results to identify slow hooks.
        """
        if not self.rcon:
            return None

        try:
            # Enable detailed profiling
            self._safe_rcon("perf 6")
            time.sleep(3)  # Let profiling data accumulate
            perf_result = self._safe_rcon("perf 0")  # Disable and read results
            if not perf_result:
                return self._ok("check_hook_performance", "No perf data returned", "performance")

            slow_hooks = self._parse_perf_hooks(perf_result)
            if slow_hooks:
                total_ms = sum(h["time"] for h in slow_hooks)
                hook_list = "\n".join(
                    f"  - {h['name']}: {h['time']:.1f}ms" for h in slow_hooks[:10]
                )
                if total_ms > 50:
                    return {
                        "name": "check_hook_performance",
                        "status": "critical",
                        "severity": "high",
                        "details": (
                            f"Plugin hooks consuming ~{total_ms:.0f}ms per tick "
                            f"(budget is 33ms at 30fps — {total_ms/33*100:.0f}% used):\n"
                            f"{hook_list}"
                        ),
                        "fix": [
                            {"command_rcon": "oxide.plugins", "description": "List plugins with status", "destructive": False},
                            {"command_rcon": f"oxide.unload {slow_hooks[0]['name'].split('.')[0]}",
                             "description": f"Unload worst offender: {slow_hooks[0]['name']}", "destructive": True},
                        ],
                        "category": "performance",
                    }
                elif total_ms > 20:
                    return {
                        "name": "check_hook_performance",
                        "status": "warning",
                        "severity": "medium",
                        "details": (
                            f"Slow plugin hooks totaling ~{total_ms:.0f}ms per tick:\n"
                            f"{hook_list}"
                        ),
                        "fix": None,
                        "category": "performance",
                    }
                else:
                    return self._ok(
                        "check_hook_performance",
                        f"{len(slow_hooks)} hooks above 5ms threshold, "
                        f"total ~{total_ms:.0f}ms — acceptable",
                        "performance",
                    )
            return self._ok("check_hook_performance", "All plugin hooks within normal limits", "performance")
        except Exception as e:
            return {
                "name": "check_hook_performance",
                "status": "error",
                "severity": "low",
                "details": f"Hook performance check failed: {e}",
                "fix": None,
                "category": "performance",
            }

    # ------------------------------------------------------------------
    # Plugin management helpers
    # ------------------------------------------------------------------

    def reload_plugin(self, plugin_name) -> dict:
        """Reload a specific Oxide plugin via RCON."""
        if not self.rcon:
            return {"success": False, "error": "RCON not connected"}
        try:
            result = self.rcon.oxide_reload(plugin_name)
            return {"success": True, "output": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_plugin_config(self, plugin_name) -> dict:
        """Get a plugin's config file contents."""
        if not self.ptero or not self.server_id:
            return {"success": False, "error": "Pterodactyl API not configured"}
        try:
            content = self.ptero.rust_get_oxide_config(self.server_id, plugin_name)
            if content:
                return {"success": True, "config": json.loads(content)}
            return {"success": False, "error": "Config not found"}
        except json.JSONDecodeError:
            return {"success": True, "config_raw": content}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_plugin_config(self, plugin_name, config) -> dict:
        """Update a plugin's config and reload it."""
        if not self.ptero or not self.server_id:
            return {"success": False, "error": "Pterodactyl API not configured"}
        try:
            self.ptero.rust_write_oxide_config(self.server_id, plugin_name, config)
            # Reload the plugin to apply changes
            reload_result = self.reload_plugin(plugin_name)
            return {
                "success": True,
                "config_written": True,
                "reload": reload_result,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    # Commands that may take a long time on loaded servers
    _HEAVY_COMMANDS = {"entity.count", "server.save", "status", "serverinfo", "perf"}

    def _safe_rcon(self, cmd, timeout=None):
        """Execute an RCON command, returning None if not available."""
        if not self.rcon:
            return None
        try:
            if timeout is None:
                timeout = 60 if cmd.split()[0] in self._HEAVY_COMMANDS else 30
            return self.rcon.command(cmd, timeout=timeout)
        except Exception as e:
            logger.debug("RCON command '%s' failed: %s", cmd, e)
            return None

    def _parse_fps(self, text):
        """Extract FPS value from fps command output."""
        if not text:
            return None
        # Common format: "34.76 fps" or "fps: 34.76"
        match = re.search(r'(\d+(?:\.\d+)?)\s*(?:fps|server\s*fps)', text, re.IGNORECASE)
        if match:
            return float(match.group(1))
        match = re.search(r'fps[:\s]+(\d+(?:\.\d+)?)', text, re.IGNORECASE)
        if match:
            return float(match.group(1))
        # Just try first number
        match = re.search(r'(\d+(?:\.\d+)?)', text)
        if match:
            return float(match.group(1))
        return None

    def _parse_entity_count(self, text):
        """Extract total entity count from entity.count output."""
        if not text:
            return None
        # Look for a large number
        numbers = re.findall(r'(\d+)', text)
        if numbers:
            # The largest number is likely the total
            return max(int(n) for n in numbers)
        return None

    def _parse_max_players(self, text):
        """Extract max players from serverinfo output."""
        if not text:
            return None
        match = re.search(r'maxplayers[:\s]+(\d+)', text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        match = re.search(r'max\s*players?[:\s]+(\d+)', text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def _count_players(self, status_text):
        """Count players from status output."""
        if not status_text:
            return 0
        # Each player line typically has a steam ID (17 digits)
        player_lines = [l for l in status_text.splitlines() if re.search(r'\d{17}', l)]
        return len(player_lines)

    def _parse_high_ping_players(self, status_text, threshold=150):
        """Find players with ping above threshold from status output."""
        if not status_text:
            return []
        high_ping = []
        for line in status_text.splitlines():
            # Look for ping values in the line
            ping_match = re.search(r'(\d+)\s*ms', line)
            if ping_match:
                ping = int(ping_match.group(1))
                if ping > threshold:
                    # Try to extract player name
                    name_match = re.search(r'"([^"]+)"', line)
                    name = name_match.group(1) if name_match else "Unknown"
                    high_ping.append({"name": name, "ping": ping})
        return high_ping

    def _parse_perf_hooks(self, perf_text, threshold_ms=5):
        """Parse perf output for slow plugin hooks."""
        if not perf_text:
            return []
        slow = []
        for line in perf_text.splitlines():
            # Look for hook timings
            match = re.search(r'(\w+(?:\.\w+)*)\s+.*?(\d+(?:\.\d+)?)\s*ms', line)
            if match:
                name = match.group(1)
                ms = float(match.group(2))
                if ms > threshold_ms:
                    slow.append({"name": name, "time": ms})
        slow.sort(key=lambda h: h["time"], reverse=True)
        return slow

    def _build_lag_summary(self, findings):
        """Build a human-readable lag diagnosis summary."""
        if not findings:
            return ("No obvious lag sources detected from server-side checks. "
                    "The issue may be intermittent, client-side, or network-related "
                    "between the player and the server.")
        critical = [f for f in findings if f["severity"] == "critical"]
        high = [f for f in findings if f["severity"] == "high"]
        medium = [f for f in findings if f["severity"] == "medium"]

        parts = []
        if critical:
            parts.append(f"{len(critical)} critical issue(s)")
        if high:
            parts.append(f"{len(high)} high-severity issue(s)")
        if medium:
            parts.append(f"{len(medium)} medium issue(s)")

        summary = f"Found {', '.join(parts)}. "

        # Lead with the root cause if we identified one
        root = next((f for f in findings if f["cause"].startswith("ROOT CAUSE")), None)
        if root:
            summary += root["cause"].replace("ROOT CAUSE: ", "") + "."
        elif critical:
            summary += f"Most urgent: {critical[0]['cause']}."
        elif high:
            summary += f"Likely cause: {high[0]['cause']}."
        return summary

    def _ok(self, name, details, category):
        return {
            "name": name,
            "status": "ok",
            "severity": "low",
            "details": details,
            "fix": None,
            "category": category,
        }
