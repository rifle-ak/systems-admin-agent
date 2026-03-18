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

    def __init__(self, rcon=None, ptero=None, server_id=None, ssh=None,
                 on_progress=None):
        self.rcon = rcon
        self.ptero = ptero
        self.server_id = server_id
        self.ssh = ssh
        self._on_progress = on_progress  # callback(message_str)

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
        """Focused diagnosis specifically for lag / rubber-banding reports.

        Returns a structured analysis with likely causes ranked by probability.
        """
        findings = []

        # 1. Check tick rate — #1 cause of rubber-banding
        self._progress("[1/5] Checking server tick rate (FPS)...")
        fps_result = self._safe_rcon("fps")
        if fps_result:
            fps_val = self._parse_fps(fps_result)
            if fps_val is not None:
                if fps_val < 10:
                    findings.append({
                        "cause": "Critically low server tick rate",
                        "severity": "critical",
                        "details": f"Server running at {fps_val} FPS (should be ~30). "
                                   "This directly causes rubber-banding.",
                        "likely_reason": "Entity overload, plugin lag, or insufficient CPU",
                        "fix": "Check entity count, review plugin performance, consider map wipe",
                    })
                elif fps_val < 20:
                    findings.append({
                        "cause": "Low server tick rate",
                        "severity": "high",
                        "details": f"Server at {fps_val} FPS (target is 30). "
                                   "Players will experience intermittent rubber-banding.",
                        "likely_reason": "Growing entity count or heavy plugin load",
                        "fix": "Run entity.count, check for entity-heavy bases, review plugins",
                    })

        # 2. Check entity count — #2 cause
        self._progress("[2/5] Checking entity count...")
        entity_result = self._safe_rcon("entity.count")
        if entity_result:
            count = self._parse_entity_count(entity_result)
            if count is not None:
                if count > 300000:
                    findings.append({
                        "cause": "Extreme entity count",
                        "severity": "critical",
                        "details": f"{count:,} entities on the map. This is a major performance drain.",
                        "likely_reason": "Overdue wipe, entity-heavy builds, or loot accumulation",
                        "fix": "Consider map wipe, or use entitiy cleanup plugins",
                    })
                elif count > 200000:
                    findings.append({
                        "cause": "High entity count",
                        "severity": "high",
                        "details": f"{count:,} entities. Performance degradation expected.",
                        "likely_reason": "Long wipe cycle or large player base",
                        "fix": "Schedule wipe soon, or run entity cleanup commands",
                    })

        # 3. Check server resources via Pterodactyl
        self._progress("[3/5] Checking server resources...")
        if self.ptero and self.server_id:
            try:
                resources = self.ptero.get_resources(self.server_id)
                res = resources.get("resources", {})
                cpu = res.get("cpu_absolute", 0)
                mem_bytes = res.get("memory_bytes", 0)
                mem_limit = res.get("memory_limit_bytes", 0)

                if cpu > 90:
                    findings.append({
                        "cause": "CPU saturation",
                        "severity": "critical",
                        "details": f"CPU at {cpu:.1f}%. Server cannot maintain tick rate.",
                        "likely_reason": "Too many entities/players for allocated CPU",
                        "fix": "Reduce entity count, lower max players, or upgrade CPU allocation",
                    })

                if mem_limit > 0 and mem_bytes > 0:
                    mem_pct = (mem_bytes / mem_limit) * 100
                    if mem_pct > 90:
                        findings.append({
                            "cause": "Memory exhaustion",
                            "severity": "critical",
                            "details": f"Memory at {mem_pct:.1f}% — "
                                       f"{mem_bytes // (1024*1024)}MB / {mem_limit // (1024*1024)}MB. "
                                       "Server may be swapping or about to OOM.",
                            "likely_reason": "Large map, many plugins, or memory leak",
                            "fix": "Restart server, check for leaky plugins, increase memory limit",
                        })
            except Exception as e:
                logger.warning("Resource check failed: %s", e)

        # 4. Check for plugin lag
        self._progress("[4/5] Checking plugin performance...")
        perf_result = self._safe_rcon("perf")
        if perf_result:
            slow_hooks = self._parse_perf_hooks(perf_result)
            if slow_hooks:
                findings.append({
                    "cause": "Slow plugin hooks",
                    "severity": "high",
                    "details": f"Found {len(slow_hooks)} slow plugin hooks:\n" +
                               "\n".join(f"  {h['name']}: {h['time']}ms avg" for h in slow_hooks[:5]),
                    "likely_reason": "Plugin code running too slowly per tick",
                    "fix": "Reload or replace the slow plugins",
                })

        # 5. Network check
        self._progress("[5/5] Checking network and player pings...")
        status_result = self._safe_rcon("status")
        if status_result:
            high_ping = self._parse_high_ping_players(status_result)
            if high_ping:
                total_players = self._count_players(status_result)
                pct = (len(high_ping) / max(total_players, 1)) * 100
                if pct > 50:
                    findings.append({
                        "cause": "Widespread high ping",
                        "severity": "high",
                        "details": f"{len(high_ping)}/{total_players} players have ping >150ms. "
                                   "Could indicate server-side network issue.",
                        "likely_reason": "Server network saturation or hosting provider issue",
                        "fix": "Check server bandwidth usage, contact host if persistent",
                    })
                else:
                    findings.append({
                        "cause": "Some players with high ping",
                        "severity": "medium",
                        "details": f"{len(high_ping)}/{total_players} players have ping >150ms. "
                                   "Likely client-side for affected players.",
                        "likely_reason": "Distant players or their ISP issues",
                        "fix": "This is typically client-side — not a server issue",
                    })

        # Sort by severity
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        findings.sort(key=lambda f: severity_order.get(f["severity"], 99))

        return {
            "lag_report": True,
            "findings": findings,
            "summary": self._build_lag_summary(findings),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }

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

        parts = [
            f"State: {state}",
            f"CPU: {cpu:.1f}%",
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

        if cpu > 95:
            return {
                "name": "check_server_resources",
                "status": "critical",
                "severity": "high",
                "details": " | ".join(parts) + " — CPU is maxed out!",
                "fix": None,
                "category": "resources",
            }

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

        if cpu > 80 or mem_pct > 80:
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
                plugin_count += 1
                if "loaded" in line.lower():
                    loaded += 1
                if "error" in line.lower() or "failed" in line.lower():
                    errored += 1

        details = f"Oxide: {result.strip()}" if result else "Oxide detected"
        details += f" | Plugins: {loaded} loaded"
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
        """Check for plugin errors in recent logs."""
        if not self.ptero or not self.server_id:
            return None

        try:
            log_files = self.ptero.rust_get_oxide_logs(self.server_id, limit=3)
            if not log_files:
                return self._ok("check_plugin_errors", "No Oxide log files found", "plugins")

            errors = []
            for log_file in log_files[:2]:  # Check last 2 logs
                try:
                    content = self.ptero.get_file_contents(
                        self.server_id,
                        f"/server/rust/oxide/logs/{log_file['name']}"
                    )
                    for line in content.splitlines()[-100:]:  # Last 100 lines
                        if any(kw in line.lower() for kw in ("error", "exception", "nullref", "failed to")):
                            errors.append(line.strip()[:200])
                except Exception:
                    continue

            if errors:
                unique_errors = list(set(errors))[:10]
                return {
                    "name": "check_plugin_errors",
                    "status": "warning",
                    "severity": "medium",
                    "details": f"Found {len(errors)} error(s) in recent Oxide logs:\n" +
                               "\n".join(f"  - {e}" for e in unique_errors[:5]),
                    "fix": [
                        {"command_rcon": "oxide.plugins", "description": "Check plugin status", "destructive": False},
                    ],
                    "category": "plugins",
                }
            return self._ok("check_plugin_errors", "No errors in recent Oxide logs", "plugins")

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
        """Check the RustDedicated process via SSH."""
        if not self.ssh:
            return None

        try:
            result = self.ssh.execute(
                "ps aux | grep -i '[R]ustDedicated' | head -5"
            )
            if not result["stdout"].strip():
                return {
                    "name": "check_process_health",
                    "status": "critical",
                    "severity": "high",
                    "details": "No RustDedicated process found! Server may be down.",
                    "fix": None,
                    "category": "process",
                }

            lines = result["stdout"].strip().splitlines()
            for line in lines:
                parts = line.split()
                if len(parts) >= 4:
                    cpu = float(parts[2])
                    mem = float(parts[3])
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

            return self._ok("check_process_health", "RustDedicated process running", "process")
        except Exception as e:
            return {
                "name": "check_process_health",
                "status": "error",
                "severity": "low",
                "details": f"Process check failed: {e}",
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
        """Check for recent crash logs."""
        if not self.ptero or not self.server_id:
            return None

        try:
            files = self.ptero.list_files(self.server_id, "/server/rust")
            crash_files = [
                f for f in files
                if f["is_file"] and ("crash" in f["name"].lower() or "error" in f["name"].lower())
            ]

            if crash_files:
                recent = sorted(crash_files, key=lambda f: f.get("modified_at", ""), reverse=True)
                details = f"Found {len(crash_files)} crash/error file(s):\n"
                for cf in recent[:5]:
                    details += f"  - {cf['name']} ({cf.get('modified_at', 'unknown')})\n"
                return {
                    "name": "check_recent_crashes",
                    "status": "warning",
                    "severity": "medium",
                    "details": details.strip(),
                    "fix": None,
                    "category": "stability",
                }

            return self._ok("check_recent_crashes", "No crash files found", "stability")

        except Exception:
            return None

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

    def _safe_rcon(self, cmd):
        """Execute an RCON command, returning None if not available."""
        if not self.rcon:
            return None
        try:
            return self.rcon.command(cmd)
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
            return "No obvious lag sources detected. Issue may be intermittent or client-side."
        critical = [f for f in findings if f["severity"] == "critical"]
        high = [f for f in findings if f["severity"] == "high"]
        if critical:
            return f"CRITICAL: {critical[0]['cause']}. {critical[0]['details']}"
        if high:
            return f"Likely cause: {high[0]['cause']}. {high[0]['details']}"
        return f"Minor issues found: {findings[0]['cause']}."

    def _ok(self, name, details, category):
        return {
            "name": name,
            "status": "ok",
            "severity": "low",
            "details": details,
            "fix": None,
            "category": category,
        }
