/* ===== SysAdmin Agent - Main Application JS ===== */

// ---------- Globals ----------
let socket = null;
let isConnected = false;
let totalTokens = 0;
let tokenBreakdown = null;
let pendingApprovals = {};
let profiles = [];
let currentPlanEl = null;

// ---------- Helpers ----------

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

function showFlash(message, category = 'info') {
    const container = $('#flashMessages');
    if (!container) return;
    const el = document.createElement('div');
    el.className = `flash-message flash-${category}`;
    el.innerHTML = `<span>${escapeHtml(message)}</span><button class="flash-close" onclick="this.parentElement.remove()">&times;</button>`;
    container.appendChild(el);
    setTimeout(() => { if (el.parentElement) el.remove(); }, 6000);
}

function formatNumber(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return String(n);
}

/**
 * Count total discovered items across all categories in the apps dict.
 * apps is a dict like {services:[], web_servers:[], databases:[], ...}
 */
function countApps(apps) {
    if (!apps || typeof apps !== 'object') return 0;
    let total = 0;
    for (const key of Object.keys(apps)) {
        if (Array.isArray(apps[key])) {
            total += apps[key].length;
        }
    }
    return total;
}

/**
 * Format apps dict into a human-readable summary string.
 */
function summarizeApps(apps) {
    if (!apps || typeof apps !== 'object') return '';
    const parts = [];
    const labels = {
        web_servers: 'Web Servers',
        databases: 'Databases',
        control_panels: 'Control Panels',
        cms: 'CMS',
        languages: 'Languages',
        containers: 'Containers',
        services: 'Services',
    };
    for (const [key, label] of Object.entries(labels)) {
        const items = apps[key];
        if (Array.isArray(items) && items.length > 0) {
            if (key === 'services') {
                const running = items.filter(s => s.status === 'running').length;
                parts.push(`${running}/${items.length} services running`);
            } else {
                const names = items.map(i => i.name || i).filter(Boolean);
                if (names.length) parts.push(`${label}: ${names.join(', ')}`);
            }
        }
    }
    return parts.join(' | ');
}

// ---------- SocketIO Setup ----------

function initSocket() {
    if (socket) return;

    // Use polling first to avoid the werkzeug WebSocket 500 error,
    // then upgrade to websocket once the connection is established.
    socket = io({ transports: ['polling', 'websocket'] });

    socket.on('connect', () => {
        console.log('SocketIO connected');
    });

    socket.on('disconnect', () => {
        console.log('SocketIO disconnected');
    });

    // ----- Server connection events -----
    socket.on('server_connected', onServerConnected);
    socket.on('server_disconnected', onServerDisconnected);

    // ----- Status / progress messages -----
    // Backend emits 'status' with {message: "..."} during connect, scan, etc.
    socket.on('status', onStatus);

    // ----- Scan events -----
    // scan_os sends {os_info: {...}}
    socket.on('scan_os', onScanOS);
    // scan_apps sends {apps: {...}}
    socket.on('scan_apps', onScanApps);
    // scan_diagnostics sends {diagnostics: [...]}
    socket.on('scan_diagnostics', onDiagnosticsResult);
    // scan_complete sends {os_info, apps, diagnostics}
    socket.on('scan_complete', onScanComplete);

    // ----- Diagnostics events -----
    // diagnostics_result sends {diagnostics: [...]}
    socket.on('diagnostics_result', onDiagnosticsResult);

    // ----- Agent events -----
    socket.on('agent_thinking', onAgentThinking);
    // agent_plan sends {plan: {explanation, questions, plan:[...]}, token_usage: {...}}
    socket.on('agent_plan', onAgentPlan);
    // step_executing sends {step, description, command}
    socket.on('step_executing', onStepExecuting);
    // step_result sends {step, command, exit_code, stdout, stderr, analysis, skipped, reason}
    socket.on('step_result', onStepResult);
    // agent_done sends {token_usage: {...}}
    socket.on('agent_done', onAgentDone);

    // ----- Approval events -----
    // approval_required sends {approval_id, command, description, destructive, snapshot_id}
    socket.on('approval_required', onApprovalRequired);
    // approval_resolved sends {approval_id, approved}
    socket.on('approval_resolved', onApprovalResolved);
    // approval_timeout sends {approval_id}
    socket.on('approval_timeout', onApprovalTimeout);

    // ----- Fix events -----
    // fix_attempting sends {check, action}
    socket.on('fix_attempting', onFixAttempting);
    // fix_result sends {check, action, result}
    socket.on('fix_result', onFixResult);
    // fix_complete sends {fixes: [...]}
    socket.on('fix_complete', onFixComplete);

    // ----- Command events -----
    socket.on('command_result', onCommandResult);

    // ----- Rollback events -----
    // rollback_list sends {snapshots: [...]}
    socket.on('rollback_list', onRollbackList);
    // rollback_result sends {snapshot_id, results: [...]}
    socket.on('rollback_result', onRollbackResult);

    // ----- Rust server events -----
    socket.on('rust_rcon_connected', onRconConnected);
    socket.on('rust_rcon_disconnected', onRconDisconnected);
    socket.on('rust_ptero_connected', onPteroConnected);
    socket.on('rust_ptero_disconnected', onPteroDisconnected);
    socket.on('rust_rcon_response', onRconResponse);
    socket.on('rust_action_result', onRustActionResult);
    socket.on('rust_diagnostics_result', onRustDiagnosticsResult);
    socket.on('rust_lag_result', onRustLagResult);
    socket.on('rust_plugin_result', onRustPluginResult);
    socket.on('rust_ptero_result', onRustPteroResult);

    // ----- Error events -----
    socket.on('error', onServerError);

    // ----- Install events (setup page) -----
    socket.on('install_progress', onInstallProgress);
}

// ---------- Connection Status ----------

function setConnectionStatus(state, text) {
    const dot = $('.status-dot');
    const label = $('.status-text');
    if (!dot || !label) return;
    dot.className = 'status-dot ' + state;
    label.textContent = text || state.charAt(0).toUpperCase() + state.slice(1);
}

function updateTokenUsage(tokenData, breakdown) {
    if (!tokenData) return;
    // Backend sends {total_input_tokens, total_output_tokens, total_requests}
    const added = (tokenData.total_input_tokens || 0) + (tokenData.total_output_tokens || 0);
    if (added > 0) {
        totalTokens = added; // Use absolute total from backend, not cumulative
    }
    if (breakdown) {
        tokenBreakdown = breakdown;
    }
    const el = $('.token-count');
    if (el) el.textContent = formatNumber(totalTokens);

    // Update the monthly counter in nav if available
    const monthEl = $('.token-monthly');
    if (monthEl && breakdown && breakdown.monthly) {
        monthEl.textContent = formatNumber(breakdown.monthly.total_tokens);
    }
}

// ---------- Sidebar Toggle ----------

function initSidebarToggle() {
    const toggle = $('#sidebarToggle');
    const sidebar = $('#sidebar');
    if (!toggle || !sidebar) return;

    // Create overlay element
    let overlay = $('.sidebar-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'sidebar-overlay';
        sidebar.parentElement.insertBefore(overlay, sidebar);
    }

    toggle.addEventListener('click', () => {
        sidebar.classList.toggle('open');
        overlay.classList.toggle('active');
    });

    overlay.addEventListener('click', () => {
        sidebar.classList.remove('open');
        overlay.classList.remove('active');
    });
}

// ---------- Chat Message Rendering ----------

function addMessage(type, content, extra = {}) {
    const container = $('#chatMessages');
    if (!container) return null;

    const wrapper = document.createElement('div');
    wrapper.className = `message ${type}-message`;
    if (extra.id) wrapper.id = extra.id;

    const contentEl = document.createElement('div');
    contentEl.className = 'message-content';

    if (typeof content === 'string') {
        contentEl.innerHTML = content;
    } else {
        contentEl.appendChild(content);
    }

    wrapper.appendChild(contentEl);
    container.appendChild(wrapper);
    scrollToBottom();
    return wrapper;
}

function scrollToBottom() {
    const container = $('#chatMessages');
    if (!container) return;
    requestAnimationFrame(() => {
        container.scrollTop = container.scrollHeight;
    });
}

function removeMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

/**
 * Update or create an in-place progress message (reuses same element).
 */
function updateProgress(text) {
    const existingId = 'progress-msg';
    let el = document.getElementById(existingId);
    if (!el) {
        el = addMessage('agent', `<span class="progress-indicator"><span class="progress-spinner"></span> <span id="progress-text">${escapeHtml(text)}</span></span>`, { id: existingId });
    } else {
        const textEl = el.querySelector('#progress-text');
        if (textEl) textEl.textContent = text;
    }
    scrollToBottom();
}

// ---------- Render Helpers ----------

function renderCodeBlock(text, exitCode = null, label = '') {
    const MAX_PREVIEW_LINES = 6;
    const raw = text || '(no output)';
    const lines = raw.split('\n');
    const isLong = lines.length > MAX_PREVIEW_LINES;

    const block = document.createElement('div');
    block.className = 'code-block' + (exitCode !== null && exitCode !== 0 ? ' code-block-error' : '');

    // Header bar with label + exit code
    if (label || (exitCode !== null && exitCode !== undefined)) {
        const header = document.createElement('div');
        header.className = 'code-block-header';
        if (label) {
            const lbl = document.createElement('span');
            lbl.className = 'code-block-label';
            lbl.textContent = label;
            header.appendChild(lbl);
        }
        if (exitCode !== null && exitCode !== undefined) {
            const exitEl = document.createElement('span');
            exitEl.className = 'exit-code ' + (exitCode === 0 ? 'success' : 'error');
            exitEl.textContent = exitCode === 0 ? 'OK' : `exit ${exitCode}`;
            header.appendChild(exitEl);
        }
        block.appendChild(header);
    }

    const code = document.createElement('code');
    if (isLong) {
        code.textContent = lines.slice(0, MAX_PREVIEW_LINES).join('\n');
        block.appendChild(code);

        const toggle = document.createElement('button');
        toggle.className = 'code-toggle';
        toggle.textContent = `Show all ${lines.length} lines`;
        toggle.onclick = () => {
            if (toggle.dataset.expanded === '1') {
                code.textContent = lines.slice(0, MAX_PREVIEW_LINES).join('\n');
                toggle.textContent = `Show all ${lines.length} lines`;
                toggle.dataset.expanded = '0';
            } else {
                code.textContent = raw;
                toggle.textContent = 'Collapse';
                toggle.dataset.expanded = '1';
            }
        };
        block.appendChild(toggle);
    } else {
        code.textContent = raw;
        block.appendChild(code);
    }

    return block;
}

function renderDiagnostics(results) {
    if (!results || results.length === 0) {
        const p = document.createElement('p');
        p.textContent = 'No issues found.';
        return p;
    }

    const container = document.createElement('div');
    container.className = 'diag-container';

    // Sort: critical/errors first, then warnings, then ok
    const order = { critical: 0, error: 1, warning: 2, info: 3, ok: 4, pass: 4 };
    const sorted = [...results].sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3));

    // Summary counts
    const counts = { critical: 0, warning: 0, ok: 0 };
    for (const c of sorted) {
        const st = c.status || '';
        if (st === 'critical' || st === 'error') counts.critical++;
        else if (st === 'warning') counts.warning++;
        else counts.ok++;
    }

    const summary = document.createElement('div');
    summary.className = 'diag-summary';
    const parts = [];
    if (counts.critical > 0) parts.push(`<span class="diag-count diag-count-critical">${counts.critical} critical</span>`);
    if (counts.warning > 0) parts.push(`<span class="diag-count diag-count-warn">${counts.warning} warning</span>`);
    parts.push(`<span class="diag-count diag-count-ok">${counts.ok} passed</span>`);
    summary.innerHTML = parts.join(' ');
    container.appendChild(summary);

    // Issue rows (non-ok)
    for (const check of sorted) {
        const st = check.status || '';
        const isOk = st === 'ok' || st === 'pass' || st === 'info';
        if (isOk) continue;

        const row = document.createElement('div');
        const isWarn = st === 'warning';
        row.className = 'diag-row diag-row-' + (isWarn ? 'warn' : 'critical');

        const name = (check.name || '').replace(/^check_/, '').replace(/_/g, ' ');
        const severity = (check.severity || 'info').toLowerCase();

        row.innerHTML = `
            <div class="diag-row-header">
                <span class="status-icon ${isWarn ? 'warn' : 'fail'}">${isWarn ? '\u26A0' : '\u2717'}</span>
                <span class="diag-row-name">${escapeHtml(name)}</span>
                <span class="severity-badge severity-${severity}">${escapeHtml(severity)}</span>
                <span class="diag-row-toggle">\u25B8</span>
            </div>
        `;

        const details = check.details || '';
        if (details) {
            const detailEl = document.createElement('div');
            detailEl.className = 'diag-row-details';
            detailEl.style.display = 'none';
            detailEl.textContent = details;
            row.appendChild(detailEl);

            row.querySelector('.diag-row-header').onclick = () => {
                const isOpen = detailEl.style.display !== 'none';
                detailEl.style.display = isOpen ? 'none' : 'block';
                row.querySelector('.diag-row-toggle').textContent = isOpen ? '\u25B8' : '\u25BE';
            };
            row.querySelector('.diag-row-header').style.cursor = 'pointer';
        }

        container.appendChild(row);
    }

    // Collapsed "passed checks" section
    if (counts.ok > 0) {
        const passedToggle = document.createElement('div');
        passedToggle.className = 'diag-passed-toggle';
        passedToggle.textContent = `\u25B8 ${counts.ok} passed checks`;
        passedToggle.style.cursor = 'pointer';

        const passedList = document.createElement('div');
        passedList.className = 'diag-passed-list';
        passedList.style.display = 'none';

        for (const check of sorted) {
            const st = check.status || '';
            if (st !== 'ok' && st !== 'pass' && st !== 'info') continue;
            const name = (check.name || '').replace(/^check_/, '').replace(/_/g, ' ');
            const item = document.createElement('div');
            item.className = 'diag-passed-item';
            item.innerHTML = `<span class="status-icon pass">\u2713</span> ${escapeHtml(name)}`;
            passedList.appendChild(item);
        }

        passedToggle.onclick = () => {
            const isOpen = passedList.style.display !== 'none';
            passedList.style.display = isOpen ? 'none' : 'block';
            passedToggle.textContent = (isOpen ? '\u25B8 ' : '\u25BE ') + `${counts.ok} passed checks`;
        };

        container.appendChild(passedToggle);
        container.appendChild(passedList);
    }

    return container;
}

function renderPlan(steps) {
    const list = document.createElement('ol');
    list.className = 'plan-steps';

    for (let i = 0; i < steps.length; i++) {
        const step = steps[i];
        const li = document.createElement('li');
        li.className = 'plan-step pending';
        li.dataset.stepIndex = i;
        // Also track by step number for backend matching
        const stepNum = typeof step === 'object' ? (step.step || i + 1) : i + 1;
        li.dataset.stepNum = stepNum;

        const indicator = document.createElement('div');
        indicator.className = 'step-indicator';
        indicator.textContent = i + 1;

        const text = document.createElement('span');
        text.className = 'step-text';
        text.textContent = typeof step === 'string' ? step : (step.description || step.name || `Step ${i + 1}`);

        li.appendChild(indicator);
        li.appendChild(text);
        list.appendChild(li);
    }

    return list;
}

function renderApprovalCard(data) {
    // Backend sends: {approval_id, command, description, destructive, snapshot_id}
    const approvalId = data.approval_id;
    const card = document.createElement('div');
    card.className = 'approval-card' + (data.destructive ? ' destructive' : '');
    card.id = `approval-${approvalId}`;

    card.innerHTML = `
        <div class="approval-header">
            <span class="warning-icon">${data.destructive ? '\u26A0' : '\u2753'}</span>
            <span>${data.destructive ? 'Destructive Action Requires Approval' : 'Action Requires Approval'}</span>
        </div>
        <div class="approval-command">${escapeHtml(data.command)}</div>
        <div class="approval-desc">${escapeHtml(data.description || '')}</div>
        ${data.snapshot_id ? `<div class="approval-desc" style="font-size:11px;color:var(--text-dim)">Snapshot: ${escapeHtml(data.snapshot_id)}</div>` : ''}
        <div class="approval-actions">
            <button class="btn btn-success" onclick="approveAction('${escapeHtml(approvalId)}', true)">Approve</button>
            <button class="btn btn-danger" onclick="approveAction('${escapeHtml(approvalId)}', false)">Deny</button>
        </div>
    `;

    pendingApprovals[approvalId] = data;
    return card;
}

// ---------- SocketIO Event Handlers ----------

function onServerConnected(data) {
    isConnected = true;
    setConnectionStatus('connected', 'Connected');
    removeMessage('progress-msg');

    // Show server info
    const infoPanel = $('#serverInfo');
    const quickActions = $('#quickActions');
    const connectBtn = $('#connectBtn');
    const disconnectBtn = $('#disconnectBtn');
    const connectForm = $('#connectForm');

    if (infoPanel) infoPanel.style.display = 'block';
    if (quickActions) quickActions.style.display = 'block';
    if (connectBtn) connectBtn.style.display = 'none';
    if ($('#saveProfileBtn')) $('#saveProfileBtn').style.display = 'none';
    if (disconnectBtn) disconnectBtn.style.display = 'block';
    // Show Rust admin panel
    const rustAdmin = $('#rustAdmin');
    if (rustAdmin) rustAdmin.style.display = 'block';

    // Disable form inputs
    if (connectForm) {
        for (const input of connectForm.querySelectorAll('input, select')) {
            input.disabled = true;
        }
    }

    // Populate sidebar info — OSDetector returns: distribution, type, version,
    // kernel, architecture, hostname, uptime
    if (data.os_info) {
        const info = data.os_info;
        if ($('#infoOS')) $('#infoOS').textContent = info.distribution || info.type || '-';
        if ($('#infoHostname')) $('#infoHostname').textContent = info.hostname || '-';
        if ($('#infoUptime')) $('#infoUptime').textContent = info.uptime || '-';
    }

    // Build a meaningful app count — apps is a dict of categories, not an array
    const appCount = countApps(data.apps);
    const appSummary = appCount > 0 ? ` ${appCount} components detected.` : '';
    addMessage('system', `Connected to server.${appSummary}`);
}

function onServerDisconnected() {
    isConnected = false;
    setConnectionStatus('disconnected', 'Disconnected');

    const infoPanel = $('#serverInfo');
    const quickActions = $('#quickActions');
    const connectBtn = $('#connectBtn');
    const disconnectBtn = $('#disconnectBtn');
    const connectForm = $('#connectForm');

    if (infoPanel) infoPanel.style.display = 'none';
    if (quickActions) quickActions.style.display = 'none';
    if (connectBtn) connectBtn.style.display = '';
    if ($('#saveProfileBtn')) $('#saveProfileBtn').style.display = '';
    if (disconnectBtn) disconnectBtn.style.display = 'none';
    // Hide Rust admin panel
    const rustAdmin = $('#rustAdmin');
    if (rustAdmin) rustAdmin.style.display = 'none';

    if (connectForm) {
        for (const input of connectForm.querySelectorAll('input, select')) {
            input.disabled = false;
        }
    }

    addMessage('system', 'Disconnected from server.');
}

function onStatus(data) {
    // Backend emits status with {message: "..."} during various operations
    updateProgress(data.message || '...');
}

function onScanOS(data) {
    // scan_os sends {os_info: {...}}
    if (data.os_info) {
        const info = data.os_info;
        // Update sidebar
        if ($('#infoOS')) $('#infoOS').textContent = info.distribution || info.type || '-';
        if ($('#infoHostname')) $('#infoHostname').textContent = info.hostname || '-';
        if ($('#infoUptime')) $('#infoUptime').textContent = info.uptime || '-';
    }
    updateProgress('OS detected, discovering applications...');
}

function onScanApps(data) {
    // scan_apps sends {apps: {...}}
    const count = countApps(data.apps);
    updateProgress(`Found ${count} components, running diagnostics...`);
}

function onScanComplete(data) {
    removeMessage('progress-msg');

    // Build one consolidated scan report instead of 3+ separate messages
    const report = document.createElement('div');
    report.className = 'scan-report';

    // OS + Apps on one compact line
    if (data.os_info) {
        const info = data.os_info;
        const os = escapeHtml(info.distribution || info.type || 'Unknown');
        const ver = info.version ? ' ' + escapeHtml(info.version) : '';
        const host = info.hostname ? escapeHtml(info.hostname) : '';
        const uptime = info.uptime ? escapeHtml(info.uptime) : '';

        const header = document.createElement('div');
        header.className = 'scan-header';
        header.innerHTML = `<strong>${os}${ver}</strong>` +
            (host ? ` &middot; ${host}` : '') +
            (uptime ? ` &middot; up ${uptime}` : '');

        if (data.apps) {
            const summary = summarizeApps(data.apps);
            if (summary) {
                header.innerHTML += `<div class="scan-apps">${escapeHtml(summary)}</div>`;
            }
        }
        report.appendChild(header);

        // Update sidebar
        if ($('#infoOS')) $('#infoOS').textContent = info.distribution || info.type || '-';
        if ($('#infoHostname')) $('#infoHostname').textContent = info.hostname || '-';
        if ($('#infoUptime')) $('#infoUptime').textContent = info.uptime || '-';
    }

    // Diagnostics
    if (data.diagnostics && data.diagnostics.length > 0) {
        const diagEl = renderDiagnostics(data.diagnostics);
        report.appendChild(diagEl);
    }

    addMessage('agent', report);
    addMessage('system', 'Scan complete.');
}

function onDiagnosticsResult(data) {
    removeMessage('thinking-msg');
    removeMessage('progress-msg');
    const results = data.diagnostics || data.results || data.checks || data;
    const table = renderDiagnostics(Array.isArray(results) ? results : []);
    addMessage('agent', table);
}

function onAgentThinking() {
    removeMessage('thinking-msg');
    addMessage('thinking', '<span class="thinking-dots">Thinking</span>', { id: 'thinking-msg' });
}

function onAgentPlan(data) {
    removeMessage('thinking-msg');
    removeMessage('progress-msg');

    // data.plan is the full AI response: {explanation, questions, plan: [...steps]}
    const planResponse = data.plan || {};
    const explanation = planResponse.explanation || '';
    const questions = planResponse.questions || [];
    const steps = planResponse.plan || [];

    // Show explanation
    if (explanation) {
        addMessage('agent', escapeHtml(explanation));
    }

    // Show questions if any
    if (questions.length > 0) {
        let qHtml = '<strong>Need more information:</strong><br>';
        questions.forEach((q, i) => {
            qHtml += `${i + 1}. ${escapeHtml(q)}<br>`;
        });
        addMessage('agent', qHtml);
    }

    // Show plan steps
    if (steps.length > 0) {
        const planEl = renderPlan(steps);
        currentPlanEl = planEl;
        addMessage('agent', planEl);
    }

    // Update token usage
    if (data.token_usage) {
        updateTokenUsage(data.token_usage, data.token_breakdown);
    }
}

function onStepExecuting(data) {
    // Mark the current step as running in the plan UI
    if (currentPlanEl) {
        const stepNum = data.step;
        const items = currentPlanEl.querySelectorAll('.plan-step');
        for (const item of items) {
            if (parseInt(item.dataset.stepNum) === stepNum) {
                item.className = 'plan-step running';
                break;
            }
        }
    }
}

function onStepResult(data) {
    if (currentPlanEl) {
        const stepNum = data.step;
        const items = currentPlanEl.querySelectorAll('.plan-step');
        for (const item of items) {
            const num = parseInt(item.dataset.stepNum);
            if (num < stepNum) {
                item.className = 'plan-step done';
                item.querySelector('.step-indicator').textContent = '\u2713';
            } else if (num === stepNum) {
                if (data.skipped) {
                    item.className = 'plan-step skipped';
                    item.querySelector('.step-indicator').textContent = '-';
                } else {
                    item.className = 'plan-step done';
                    item.querySelector('.step-indicator').textContent = '\u2713';
                }
            }
        }
    }

    // Show skip reason if skipped
    if (data.skipped && data.reason) {
        addMessage('system', `Step ${data.step} skipped: ${data.reason}`);
        return;
    }

    // Build a single consolidated message: analysis summary + issues on top,
    // collapsible command output underneath — not 4 separate messages.
    const wrapper = document.createElement('div');
    wrapper.className = 'step-result-card';

    const analysis = data.analysis || {};
    const hasIssues = analysis.issues_found && analysis.issues_found.length > 0;
    const hasRecs = analysis.recommendations && analysis.recommendations.length > 0;

    // Analysis summary line
    if (analysis.summary) {
        const summaryEl = document.createElement('div');
        summaryEl.className = 'step-summary' + (hasIssues ? ' step-summary-warn' : '');
        summaryEl.textContent = analysis.summary;
        wrapper.appendChild(summaryEl);
    }

    // Issues (if any) — these are critical, always visible
    if (hasIssues) {
        const issuesEl = document.createElement('div');
        issuesEl.className = 'step-issues';
        analysis.issues_found.forEach(i => {
            const item = document.createElement('div');
            item.className = 'step-issue-item';
            item.innerHTML = `<span class="status-icon warn">\u26A0</span> ${escapeHtml(i)}`;
            issuesEl.appendChild(item);
        });
        wrapper.appendChild(issuesEl);
    }

    // Recommendations — compact list
    if (hasRecs) {
        const recsEl = document.createElement('div');
        recsEl.className = 'step-recommendations';
        analysis.recommendations.forEach(r => {
            const item = document.createElement('div');
            item.className = 'step-rec-item';
            item.innerHTML = `\u2192 ${escapeHtml(r)}`;
            recsEl.appendChild(item);
        });
        wrapper.appendChild(recsEl);
    }

    // Command output — collapsible, below the analysis
    if (data.stdout || data.stderr) {
        const output = (data.stdout || '') + (data.stderr ? '\n' + data.stderr : '');
        const cmdLabel = data.command ? data.command.split(' ').slice(0, 3).join(' ') : '';
        const block = renderCodeBlock(output.trim(), data.exit_code, cmdLabel);
        wrapper.appendChild(block);
    }

    // Only add a message if there's actually content
    if (wrapper.children.length > 0) {
        addMessage('agent', wrapper);
    }
}

function onAgentDone(data) {
    removeMessage('thinking-msg');
    removeMessage('progress-msg');
    currentPlanEl = null;

    if (data.token_usage) {
        updateTokenUsage(data.token_usage, data.token_breakdown);
    }

    addMessage('system', 'Agent finished.');
}

function onApprovalRequired(data) {
    removeMessage('thinking-msg');
    // data has: {approval_id, command, description, destructive, snapshot_id}
    const card = renderApprovalCard(data);
    addMessage('agent', card);
}

function onApprovalResolved(data) {
    const card = document.getElementById(`approval-${data.approval_id}`);
    if (card) {
        card.classList.add('resolved');
        const actions = card.querySelector('.approval-actions');
        if (actions) {
            actions.innerHTML = `<span class="approval-resolved">${data.approved ? 'Approved' : 'Denied'}</span>`;
        }
    }
}

function onApprovalTimeout(data) {
    const card = document.getElementById(`approval-${data.approval_id}`);
    if (card) {
        card.classList.add('resolved');
        const actions = card.querySelector('.approval-actions');
        if (actions) {
            actions.innerHTML = '<span class="approval-resolved">Timed out (denied)</span>';
        }
    }
    delete pendingApprovals[data.approval_id];
}

function onFixAttempting(data) {
    const checkName = (data.check || '').replace('check_', '').replace(/_/g, ' ');
    const desc = data.action?.description || data.action?.command || '';
    updateProgress(`Fixing: ${checkName} — ${desc}`);
}

function onFixResult(data) {
    const checkName = (data.check || '').replace('check_', '').replace(/_/g, ' ');
    const result = data.result || {};
    if (result.applied) {
        addMessage('system', `Fix applied: ${checkName}`);
    } else {
        addMessage('system', `Fix not applied (${checkName}): ${result.reason || 'unknown'}`);
    }
}

function onFixComplete(data) {
    removeMessage('progress-msg');
    const fixes = data.fixes || [];
    const applied = fixes.filter(f => f.result?.applied).length;
    addMessage('system', `Fix run complete. ${applied}/${fixes.length} fixes applied.`);
}

function onCommandResult(data) {
    const output = (data.stdout || '') + (data.stderr ? '\n' + data.stderr : '');
    const block = renderCodeBlock(output.trim(), data.exit_code);
    addMessage('agent', block);
}

function onRollbackList(data) {
    const snapshots = data.snapshots || [];
    if (!snapshots || snapshots.length === 0) {
        addMessage('system', 'No snapshots available for rollback.');
        return;
    }

    let html = '<strong>Available Snapshots:</strong><br>';
    for (const snap of snapshots) {
        const id = escapeHtml(snap.id || '');
        const shortId = id.substring(0, 8);
        const cmd = escapeHtml(snap.command || '');
        const time = escapeHtml(snap.timestamp || '');
        const status = escapeHtml(snap.status || '');
        html += `<div style="margin: 4px 0;">
            <button class="btn btn-small btn-secondary" onclick="executeRollback('${id}')">${shortId}</button>
            <span style="font-size:12px;color:var(--text-secondary);margin-left:8px">${cmd} (${status}) — ${time}</span>
        </div>`;
    }
    addMessage('agent', html);
}

function onRollbackResult(data) {
    removeMessage('progress-msg');
    const results = data.results || [];
    let html = `<strong>Rollback ${escapeHtml(data.snapshot_id || '')}:</strong><br>`;
    for (const r of results) {
        const target = r.file || r.service || 'unknown';
        const status = r.status || 'unknown';
        const color = (status === 'restored' || status === 'started' || status === 'stopped') ? 'var(--success)' : 'var(--danger)';
        html += `<span style="color:${color}">${escapeHtml(target)}: ${escapeHtml(status)}</span>`;
        if (r.error) html += ` <span style="color:var(--danger)">${escapeHtml(r.error)}</span>`;
        html += '<br>';
    }
    addMessage('agent', html);
}

function onServerError(data) {
    removeMessage('thinking-msg');
    removeMessage('progress-msg');
    const msg = data.message || data.error || 'Unknown error';
    addMessage('system', `Error: ${escapeHtml(msg)}`);
    showFlash(msg, 'error');
}

function onInstallProgress(data) {
    const log = $('#installLog');
    if (!log) return;
    log.textContent += (data.message || data.line || data) + '\n';
    log.scrollTop = log.scrollHeight;
}

// ---------- User Actions ----------

function connectServer(e) {
    e.preventDefault();
    if (!socket) initSocket();

    const authType = $('#authType')?.value || 'password';
    const pwField = $('#sshPassword');
    const enteredPassword = pwField?.value || '';
    // If no password typed but profile has a saved one, the backend will look it up
    const password = authType === 'password' ? enteredPassword : undefined;

    const payload = {
        host: $('#host')?.value,
        port: parseInt($('#port')?.value || '22'),
        username: $('#username')?.value,
        password: password || undefined,
        key_path: authType === 'key' ? ($('#keyPath')?.value || undefined) : undefined,
        passphrase: authType === 'key' ? ($('#passphrase')?.value || undefined) : undefined,
    };

    // Include selected profile name so the backend can look up saved credentials
    const profileSelect = $('#profileSelect');
    if (profileSelect?.value) {
        payload.profile_name = profileSelect.value;
    }

    setConnectionStatus('connecting', 'Connecting...');
    addMessage('system', `Connecting to ${escapeHtml(payload.host)}:${payload.port}...`);
    socket.emit('connect_server', payload);
}

function disconnectServer() {
    if (!socket) return;
    socket.emit('disconnect_server');
}

function toggleAuthFields() {
    const authType = $('#authType')?.value;
    const pwFields = $('#passwordFields');
    const keyFields = $('#keyFields');
    if (pwFields) pwFields.style.display = authType === 'password' ? 'block' : 'none';
    if (keyFields) keyFields.style.display = authType === 'key' ? 'block' : 'none';
}

function sendMessage() {
    const input = $('#chatInput');
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;

    const cmdMode = $('#cmdMode')?.checked || false;
    const isCommand = cmdMode || text.startsWith('!');
    const cleanText = text.startsWith('!') ? text.slice(1).trim() : text;

    // Show user message
    addMessage('user', escapeHtml(text));
    input.value = '';
    autoResizeInput(input);

    if (!socket) {
        addMessage('system', 'Not connected to backend. Please reload the page.');
        return;
    }

    if (isCommand) {
        socket.emit('exec_command', { command: cleanText });
    } else {
        socket.emit('ask_agent', { request: cleanText });
    }
}

function approveAction(approvalId, approved) {
    if (!socket) return;
    // Send approval_id to match backend expectation
    socket.emit('approve_action', { approval_id: approvalId, approved });

    // Update the card UI immediately
    const card = document.getElementById(`approval-${approvalId}`);
    if (card) {
        card.classList.add('resolved');
        const actions = card.querySelector('.approval-actions');
        if (actions) {
            actions.innerHTML = `<span class="approval-resolved">${approved ? 'Approved' : 'Denied'}</span>`;
        }
    }
    delete pendingApprovals[approvalId];
}

function runScan() {
    if (!socket || !isConnected) { showFlash('Not connected to a server', 'error'); return; }
    addMessage('system', 'Starting server scan...');
    socket.emit('run_scan');
}

function runDiagnostics() {
    if (!socket || !isConnected) { showFlash('Not connected to a server', 'error'); return; }
    addMessage('system', 'Running diagnostics...');
    socket.emit('run_diagnostics');
}

function runFix() {
    if (!socket || !isConnected) { showFlash('Not connected to a server', 'error'); return; }
    addMessage('system', 'Running automated fix...');
    socket.emit('run_fix');
}

function requestRollback() {
    if (!socket || !isConnected) { showFlash('Not connected to a server', 'error'); return; }
    addMessage('system', 'Fetching snapshots...');
    socket.emit('rollback');
}

function executeRollback(snapshotId) {
    if (!socket) return;
    addMessage('system', `Rolling back to snapshot ${escapeHtml(snapshotId.substring(0, 8))}...`);
    socket.emit('rollback_execute', { snapshot_id: snapshotId });
}

// ---------- Profiles ----------

async function loadProfiles() {
    try {
        const res = await fetch('/api/profiles');
        if (!res.ok) return;
        profiles = await res.json();
        const select = $('#profileSelect');
        if (!select) return;
        select.innerHTML = '<option value="">-- Select profile --</option>';
        for (const p of profiles) {
            const name = typeof p === 'string' ? p : (p.name || p.host);
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            select.appendChild(opt);
        }
    } catch (e) {
        console.error('Failed to load profiles:', e);
    }
}

function loadProfile() {
    const select = $('#profileSelect');
    if (!select || !select.value) return;

    const profile = profiles.find(p => (typeof p === 'string' ? p : (p.name || p.host)) === select.value);
    if (!profile || typeof profile === 'string') return;

    if (profile.host && $('#host')) $('#host').value = profile.host;
    if (profile.port && $('#port')) $('#port').value = profile.port;
    if (profile.username && $('#username')) $('#username').value = profile.username;
    if (profile.auth_type && $('#authType')) {
        $('#authType').value = profile.auth_type;
        toggleAuthFields();
    }
    if (profile.key_path && $('#keyPath')) $('#keyPath').value = profile.key_path;

    // If password is saved in the profile, set a placeholder indicator
    if (profile.password_saved && $('#sshPassword')) {
        $('#sshPassword').value = '';
        $('#sshPassword').placeholder = '(saved in profile)';
        // Store a flag so connectServer knows to use the saved password
        $('#sshPassword').dataset.savedPassword = 'true';
    } else if ($('#sshPassword')) {
        $('#sshPassword').placeholder = 'Server password';
        delete $('#sshPassword').dataset.savedPassword;
    }

    // Rust admin fields
    if (profile.rcon_host && $('#rconHost')) $('#rconHost').value = profile.rcon_host;
    if (profile.rcon_port && $('#rconPort')) $('#rconPort').value = profile.rcon_port;
    if (profile.rcon_password_saved && $('#rconPassword')) {
        $('#rconPassword').value = '';
        $('#rconPassword').placeholder = '(saved in profile)';
        $('#rconPassword').dataset.savedPassword = 'true';
    } else if ($('#rconPassword')) {
        $('#rconPassword').placeholder = 'RCON password';
        delete $('#rconPassword').dataset.savedPassword;
    }

    if (profile.ptero_url && $('#pteroUrl')) $('#pteroUrl').value = profile.ptero_url;
    if (profile.ptero_server_id && $('#pteroServerId')) $('#pteroServerId').value = profile.ptero_server_id;
    if (profile.ptero_api_key_saved && $('#pteroApiKey')) {
        $('#pteroApiKey').value = '';
        $('#pteroApiKey').placeholder = '(saved in profile)';
        $('#pteroApiKey').dataset.savedPassword = 'true';
    } else if ($('#pteroApiKey')) {
        $('#pteroApiKey').placeholder = 'API key (ptlc_...)';
        delete $('#pteroApiKey').dataset.savedPassword;
    }

    // Show Rust admin panel if profile has Rust data
    if ((profile.rcon_host || profile.ptero_url) && $('#rustAdmin')) {
        $('#rustAdmin').style.display = 'block';
        if ($('#rustSaveSection')) $('#rustSaveSection').style.display = 'block';
    }

    showFlash('Profile loaded', 'success');
}

async function saveProfile() {
    const name = prompt('Profile name:');
    if (!name) return;

    const authType = $('#authType')?.value || 'password';
    const password = $('#sshPassword')?.value || '';
    const payload = {
        name,
        host: $('#host')?.value,
        port: parseInt($('#port')?.value || '22'),
        username: $('#username')?.value,
        auth_type: authType,
        key_path: authType === 'key' ? ($('#keyPath')?.value || '') : '',
    };

    // Include Rust admin credentials — use form inputs if visible, tracked state if hidden
    const rconHost = $('#rconHost')?.value || _rconState.host || '';
    const rconPort = $('#rconPort')?.value || _rconState.port || '';
    const rconPassword = $('#rconPassword')?.value || '';
    const pteroUrl = $('#pteroUrl')?.value || _pteroState.url || '';
    const pteroApiKey = $('#pteroApiKey')?.value || '';
    const pteroServerId = $('#pteroServerId')?.value || _pteroState.serverId || '';

    if (rconHost) payload.rcon_host = rconHost;
    if (rconPort) payload.rcon_port = parseInt(rconPort);
    if (rconPassword) payload.rcon_password = rconPassword;
    // If RCON password was already saved in profile and user didn't enter a new one, preserve it
    if (!rconPassword && (_rconState.passwordSaved || $('#rconPassword')?.dataset.savedPassword === 'true')) {
        payload.preserve_rcon_password = true;
    }
    if (pteroUrl) payload.ptero_url = pteroUrl;
    if (pteroApiKey) payload.ptero_api_key = pteroApiKey;
    // If API key was already saved and user didn't enter a new one, preserve it
    if (!pteroApiKey && (_pteroState.apiKeySaved || $('#pteroApiKey')?.dataset.savedPassword === 'true')) {
        payload.preserve_ptero_api_key = true;
    }
    if (pteroServerId) payload.ptero_server_id = pteroServerId;

    // If password auth and a password is entered, ask about saving it
    if (authType === 'password' && password) {
        const saveChoice = await showPasswordSaveDialog(name, password);
        if (saveChoice === 'save') {
            payload.password = password;
            payload.save_password = true;
        }
        // 'ssh_key' choice is handled inside showPasswordSaveDialog
        // 'skip' means save profile without password
    }

    try {
        const res = await fetch('/api/profiles', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (res.ok) {
            showFlash('Profile saved', 'success');
            await loadProfiles();
        } else {
            showFlash('Failed to save profile', 'error');
        }
    } catch (e) {
        showFlash('Error saving profile', 'error');
    }
}

/**
 * Show a dialog asking the user about password storage security.
 * Returns: 'save' | 'ssh_key' | 'skip'
 */
function showPasswordSaveDialog(profileName, password) {
    return new Promise((resolve) => {
        // Remove any existing dialog
        const existing = document.getElementById('passwordSaveDialog');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.id = 'passwordSaveDialog';
        overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);z-index:10000;display:flex;align-items:center;justify-content:center;';

        const dialog = document.createElement('div');
        dialog.style.cssText = 'background:var(--bg-secondary,#1e1e2e);border:1px solid var(--border,#333);border-radius:12px;padding:24px;max-width:480px;width:90%;color:var(--text-primary,#cdd6f4);';
        dialog.innerHTML = `
            <h3 style="margin:0 0 12px;color:var(--warning,#f9e2af);">Save Password?</h3>
            <p style="margin:0 0 16px;font-size:14px;line-height:1.5;color:var(--text-secondary,#a6adc8);">
                Saving your password stores it in the config file with basic obfuscation (base64).
                <strong style="color:var(--danger,#f38ba8);">This is NOT secure encryption</strong> —
                anyone with access to the config file can decode it.
            </p>
            <p style="margin:0 0 20px;font-size:14px;line-height:1.5;color:var(--text-secondary,#a6adc8);">
                For better security, you can set up an SSH key instead. This will generate a key pair
                and automatically install it on the server.
            </p>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <button id="pwdSaveBtn" class="btn btn-primary" style="flex:1;min-width:120px;">Save Password</button>
                <button id="pwdKeyBtn" class="btn btn-success" style="flex:1;min-width:120px;">Setup SSH Key</button>
                <button id="pwdSkipBtn" class="btn btn-secondary" style="flex:1;min-width:120px;">Don't Save</button>
            </div>
        `;

        overlay.appendChild(dialog);
        document.body.appendChild(overlay);

        document.getElementById('pwdSaveBtn').onclick = () => { overlay.remove(); resolve('save'); };
        document.getElementById('pwdKeyBtn').onclick = () => { overlay.remove(); setupSSHKey(profileName, password); resolve('skip'); };
        document.getElementById('pwdSkipBtn').onclick = () => { overlay.remove(); resolve('skip'); };
    });
}

/**
 * Set up SSH key authentication for a profile.
 * Generates a key pair on the backend and installs it on the remote server.
 */
async function setupSSHKey(profileName, password) {
    showFlash('Setting up SSH key...', 'info');

    try {
        const res = await fetch('/api/profiles/setup-ssh-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile_name: profileName, password }),
        });
        const data = await res.json();

        if (res.ok) {
            showFlash(data.message || 'SSH key installed successfully!', 'success');
            // Reload profiles to reflect the auth_type change
            await loadProfiles();
            // Update the form to show key auth
            if ($('#authType')) {
                $('#authType').value = 'key';
                toggleAuthFields();
            }
            if ($('#keyPath') && data.key_path) {
                $('#keyPath').value = data.key_path;
            }
        } else {
            showFlash('SSH key setup failed: ' + (data.error || 'Unknown error'), 'error');
        }
    } catch (e) {
        showFlash('SSH key setup error: ' + e.message, 'error');
    }
}

async function deleteProfile() {
    const select = $('#profileSelect');
    if (!select || !select.value) return;
    if (!confirm(`Delete profile "${select.value}"?`)) return;

    try {
        const res = await fetch(`/api/profiles/${encodeURIComponent(select.value)}`, { method: 'DELETE' });
        if (res.ok) {
            showFlash('Profile deleted', 'success');
            await loadProfiles();
        } else {
            showFlash('Failed to delete profile', 'error');
        }
    } catch (e) {
        showFlash('Error deleting profile', 'error');
    }
}

// ---------- Chat Input Handling ----------

function autoResizeInput(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
}

function initChatInput() {
    const input = $('#chatInput');
    if (!input) return;

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    input.addEventListener('input', () => autoResizeInput(input));
}

// ---------- Setup Page Functions ----------

async function runSystemCheck() {
    const checkPython = $('#checkPython');
    const checkApiKey = $('#checkApiKey');
    const depList = $('#depList');
    const nextBtn = $('#step1Next');

    // Reset states
    if (checkPython) {
        checkPython.querySelector('.check-icon').className = 'check-icon loading';
        checkPython.querySelector('.check-status').textContent = 'Checking...';
    }
    if (checkApiKey) {
        checkApiKey.querySelector('.check-icon').className = 'check-icon loading';
        checkApiKey.querySelector('.check-status').textContent = 'Checking...';
    }
    if (depList) depList.innerHTML = '';
    if (nextBtn) nextBtn.disabled = true;

    try {
        const res = await fetch('/api/setup/check', { method: 'POST' });
        const data = await res.json();

        // Python check
        if (checkPython) {
            const icon = checkPython.querySelector('.check-icon');
            const status = checkPython.querySelector('.check-status');
            icon.className = 'check-icon ' + (data.python_ok ? 'pass' : 'fail');
            status.textContent = data.python_ok ? 'OK' : 'Python 3.8+ required';
        }

        // API key check
        if (checkApiKey) {
            const icon = checkApiKey.querySelector('.check-icon');
            const status = checkApiKey.querySelector('.check-status');
            icon.className = 'check-icon ' + (data.api_key_set ? 'pass' : 'fail');
            status.textContent = data.api_key_set ? 'Configured' : 'Not set';
        }

        // Dependencies
        if (depList && data.dependencies) {
            for (const dep of data.dependencies) {
                const item = document.createElement('div');
                item.className = 'check-item';
                item.innerHTML = `
                    <span class="check-icon ${dep.installed ? 'pass' : 'fail'}"></span>
                    <span class="check-name">${escapeHtml(dep.name)}</span>
                    <span class="check-status">${dep.installed ? 'Installed' : 'Missing'}</span>
                `;
                depList.appendChild(item);
            }
        }

        // Enable next button (always allow proceeding)
        if (nextBtn) nextBtn.disabled = false;

    } catch (e) {
        showFlash('Failed to run system check: ' + e.message, 'error');
    }
}

function goToStep(step) {
    // Hide all panels
    for (let i = 1; i <= 4; i++) {
        const panel = $(`#step${i}`);
        if (panel) panel.style.display = 'none';
    }

    // Show target panel
    const target = $(`#step${step}`);
    if (target) target.style.display = 'block';

    // Update stepper
    const steps = $$('.setup-stepper .step');
    const lines = $$('.setup-stepper .step-line');

    steps.forEach((el, idx) => {
        const stepNum = idx + 1;
        el.classList.remove('active', 'completed');
        if (stepNum < step) el.classList.add('completed');
        else if (stepNum === step) el.classList.add('active');
    });

    lines.forEach((el, idx) => {
        el.classList.remove('active', 'completed');
        if (idx + 1 < step) el.classList.add('completed');
        else if (idx + 1 === step) el.classList.add('active');
    });
}

async function runInstall() {
    const btn = $('#installBtn');
    const log = $('#installLog');
    const nextBtn = $('#step2Next');

    if (btn) btn.disabled = true;
    if (log) log.textContent = 'Starting installation...\n';
    if (nextBtn) nextBtn.style.display = 'none';

    // Ensure socket is connected for progress events
    if (!socket) initSocket();

    try {
        const res = await fetch('/api/setup/install', { method: 'POST' });
        const data = await res.json();

        if (log) log.textContent += '\nInstallation complete.\n';
        if (data.success !== false) {
            if (nextBtn) nextBtn.style.display = 'inline-flex';
            showFlash('Dependencies installed successfully', 'success');
        } else {
            showFlash('Some installations failed. Check the log.', 'warning');
            if (btn) btn.disabled = false;
        }
    } catch (e) {
        if (log) log.textContent += '\nError: ' + e.message + '\n';
        if (btn) btn.disabled = false;
        showFlash('Installation failed', 'error');
    }
}

async function saveConfig(e) {
    e.preventDefault();
    const btn = $('#saveConfigBtn');
    if (btn) btn.disabled = true;

    const payload = {
        api_key: $('#apiKey')?.value || '',
        web_password: $('#webPassword')?.value || '',
    };

    try {
        const res = await fetch('/api/setup/configure', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();

        if (data.success !== false) {
            showFlash('Configuration saved', 'success');
            goToStep(4);
        } else {
            showFlash(data.error || 'Failed to save configuration', 'error');
            if (btn) btn.disabled = false;
        }
    } catch (e) {
        showFlash('Error saving configuration', 'error');
        if (btn) btn.disabled = false;
    }
}

// ---------- Upgrade ----------

async function checkForUpdates() {
    try {
        const res = await fetch('/api/version');
        if (!res.ok) return;
        const data = await res.json();
        const btn = $('#upgradeBtn');
        const verEl = $('#currentVersion');

        if (verEl) verEl.textContent = `v${data.current_version}`;

        if (btn) {
            if (data.update_available) {
                btn.style.display = 'inline-flex';
                btn.title = data.remote_version
                    ? `Update to v${data.remote_version} (${data.commits_behind} commits behind)`
                    : `${data.commits_behind} commits behind`;
            } else {
                btn.style.display = 'none';
            }
        }
    } catch (e) {
        console.error('Version check failed:', e);
    }
}

async function doUpgrade() {
    const btn = $('#upgradeBtn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Updating...';
    }

    try {
        const res = await fetch('/api/upgrade', { method: 'POST' });
        const data = await res.json();

        if (res.ok) {
            const newVer = data.new_version ? ` to v${data.new_version}` : '';
            showFlash(`Update complete${newVer}. Restarting...`, 'success');

            // Re-enable button immediately so it's usable if restart fails
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Update';
            }

            // Auto-restart after a short delay
            if (data.restart_required) {
                setTimeout(async () => {
                    try {
                        await fetch('/api/restart', { method: 'POST' });
                    } catch (e) {
                        // Expected — server is restarting
                    }
                    // Wait and reload the page
                    showFlash('Server restarting, page will reload in 5 seconds...', 'info');
                    setTimeout(() => { window.location.reload(); }, 5000);
                }, 1000);
            } else {
                // No restart needed — re-check for updates
                await checkForUpdates();
            }
        } else {
            showFlash('Update failed: ' + (data.message || 'Unknown error'), 'error');
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Update';
            }
        }
    } catch (e) {
        showFlash('Update error: ' + e.message, 'error');
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Update';
        }
    }
}

// ---------- Token Detail Popup ----------

function showTokenDetails() {
    // Fetch latest from API
    fetch('/api/tokens')
        .then(r => r.json())
        .then(data => {
            tokenBreakdown = data;
            _renderTokenPopup(data);
        })
        .catch(() => {
            if (tokenBreakdown) _renderTokenPopup(tokenBreakdown);
        });
}

function _renderTokenPopup(data) {
    const existing = document.getElementById('tokenDetailPopup');
    if (existing) { existing.remove(); return; } // Toggle off

    const overlay = document.createElement('div');
    overlay.id = 'tokenDetailPopup';
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;z-index:10000;display:flex;align-items:flex-start;justify-content:flex-end;padding:56px 12px 0 0;';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    const s = data.session || {};
    const d = data.daily || {};
    const m = data.monthly || {};
    const a = data.all_time || {};
    const billingDay = data.billing_cycle_day || 1;

    const popup = document.createElement('div');
    popup.style.cssText = 'background:var(--bg-secondary,#1e1e2e);border:1px solid var(--border,#333);border-radius:12px;padding:16px;min-width:280px;color:var(--text-primary,#cdd6f4);box-shadow:0 8px 32px rgba(0,0,0,0.4);';
    popup.innerHTML = `
        <h3 style="margin:0 0 12px;font-size:14px;color:var(--text-secondary,#a6adc8);">Token Usage</h3>

        <div style="margin-bottom:12px;">
            <div style="font-size:11px;color:var(--text-dim,#6c7086);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">This Session</div>
            <div style="font-size:18px;font-weight:600;">${formatNumber(s.total_tokens || 0)}</div>
            <div style="font-size:11px;color:var(--text-dim,#6c7086);">${formatNumber(s.input_tokens || 0)} in / ${formatNumber(s.output_tokens || 0)} out &middot; ${s.requests || 0} requests</div>
        </div>

        <div style="margin-bottom:12px;">
            <div style="font-size:11px;color:var(--text-dim,#6c7086);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Today (${d.date || '-'})</div>
            <div style="font-size:18px;font-weight:600;">${formatNumber(d.total_tokens || 0)}</div>
            <div style="font-size:11px;color:var(--text-dim,#6c7086);">${formatNumber(d.input_tokens || 0)} in / ${formatNumber(d.output_tokens || 0)} out &middot; ${d.requests || 0} requests</div>
        </div>

        <div style="margin-bottom:12px;">
            <div style="font-size:11px;color:var(--text-dim,#6c7086);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">This Month (billing day: ${billingDay})</div>
            <div style="font-size:18px;font-weight:600;color:var(--accent,#89b4fa);">${formatNumber(m.total_tokens || 0)}</div>
            <div style="font-size:11px;color:var(--text-dim,#6c7086);">${formatNumber(m.input_tokens || 0)} in / ${formatNumber(m.output_tokens || 0)} out &middot; ${m.requests || 0} requests</div>
        </div>

        <div style="margin-bottom:12px;">
            <div style="font-size:11px;color:var(--text-dim,#6c7086);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">All Time</div>
            <div style="font-size:14px;font-weight:500;">${formatNumber(a.total_tokens || 0)}</div>
            <div style="font-size:11px;color:var(--text-dim,#6c7086);">${a.requests || 0} requests</div>
        </div>

        <div style="border-top:1px solid var(--border,#333);padding-top:8px;margin-top:8px;">
            <label style="font-size:11px;color:var(--text-dim,#6c7086);display:flex;align-items:center;gap:6px;">
                Billing cycle day:
                <input type="number" id="billingCycleDay" value="${billingDay}" min="1" max="28"
                    style="width:48px;background:var(--bg-primary,#11111b);border:1px solid var(--border,#333);border-radius:4px;color:var(--text-primary,#cdd6f4);padding:2px 6px;font-size:12px;">
                <button class="btn btn-small btn-secondary" onclick="saveBillingCycle()" style="font-size:10px;padding:2px 8px;">Save</button>
            </label>
        </div>
    `;

    overlay.appendChild(popup);
    document.body.appendChild(overlay);
}

async function saveBillingCycle() {
    const input = document.getElementById('billingCycleDay');
    if (!input) return;
    const day = parseInt(input.value);
    if (isNaN(day) || day < 1 || day > 28) {
        showFlash('Billing cycle day must be 1-28', 'error');
        return;
    }
    try {
        await fetch('/api/tokens/billing-cycle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ day }),
        });
        showFlash(`Billing cycle set to day ${day}`, 'success');
        // Close and reopen to refresh
        const popup = document.getElementById('tokenDetailPopup');
        if (popup) popup.remove();
        showTokenDetails();
    } catch (e) {
        showFlash('Failed to save billing cycle', 'error');
    }
}

// ---------- Rust Server Admin ----------

let rconConnected = false;
let pteroConnected = false;

// Track connection values so they survive form hiding
let _rconState = { host: '', port: 28016, passwordEntered: false, passwordSaved: false };
let _pteroState = { url: '', serverId: '', apiKeyEntered: false, apiKeySaved: false };

function toggleRustPanel() {
    const panel = $('#rustPanel');
    const arrow = $('#rustToggle');
    if (!panel) return;
    const show = panel.style.display === 'none';
    panel.style.display = show ? 'block' : 'none';
    if (arrow) arrow.textContent = show ? '▾' : '▸';
    // Show save button whenever the panel has any connection data
    if (show && $('#rustSaveSection')) {
        const hasData = ($('#rconHost')?.value || _rconState.host ||
                         $('#pteroUrl')?.value || _pteroState.url ||
                         rconConnected || pteroConnected);
        if (hasData) $('#rustSaveSection').style.display = 'block';
    }
}

function connectRcon() {
    const host = ($('#rconHost') || {}).value || '';
    const port = ($('#rconPort') || {}).value || '28016';
    const password = ($('#rconPassword') || {}).value || '';
    const useSaved = !password && $('#rconPassword')?.dataset.savedPassword === 'true';
    if (!host || (!password && !useSaved)) {
        showFlash('RCON host and password are required', 'error');
        return;
    }
    // Track values before the form hides
    _rconState.host = host;
    _rconState.port = parseInt(port);
    _rconState.passwordEntered = !!password;
    _rconState.passwordSaved = useSaved;

    updateProgress('Connecting to RCON...');
    const payload = { host, port: parseInt(port) };
    if (password) {
        payload.password = password;
    } else if (useSaved) {
        payload.use_saved_password = true;
        // Include profile name so backend can look up the saved password
        const profileSelect = $('#profileSelect');
        if (profileSelect?.value) payload.profile_name = profileSelect.value;
    }
    socket.emit('rust_connect_rcon', payload);
}

function disconnectRcon() {
    socket.emit('rust_disconnect_rcon', {});
}

function connectPtero() {
    const baseUrl = ($('#pteroUrl') || {}).value || '';
    const apiKey = ($('#pteroApiKey') || {}).value || '';
    const serverId = ($('#pteroServerId') || {}).value || '';
    const useSaved = !apiKey && $('#pteroApiKey')?.dataset.savedPassword === 'true';
    if (!baseUrl || (!apiKey && !useSaved)) {
        showFlash('Panel URL and API key are required', 'error');
        return;
    }
    // Track values before the form hides
    _pteroState.url = baseUrl;
    _pteroState.serverId = serverId;
    _pteroState.apiKeyEntered = !!apiKey;
    _pteroState.apiKeySaved = useSaved;

    updateProgress('Connecting to Pterodactyl Panel...');
    const payload = { base_url: baseUrl, server_id: serverId };
    if (apiKey) {
        payload.api_key = apiKey;
    } else if (useSaved) {
        payload.use_saved_key = true;
        const profileSelect = $('#profileSelect');
        if (profileSelect?.value) payload.profile_name = profileSelect.value;
    }
    socket.emit('rust_connect_pterodactyl', payload);
}

function disconnectPtero() {
    socket.emit('rust_disconnect_pterodactyl', {});
}

function onRconConnected(data) {
    removeMessage('progress-msg');
    rconConnected = true;
    if ($('#rconForm')) $('#rconForm').style.display = 'none';
    if ($('#rconConnected')) $('#rconConnected').style.display = 'flex';
    if ($('#rustActions')) $('#rustActions').style.display = 'block';
    if ($('#rustSaveSection')) $('#rustSaveSection').style.display = 'block';

    let msg = 'RCON connected.';
    if (data.server_info) {
        msg += '\n```\n' + escapeHtml(data.server_info) + '\n```';
    }
    addMessage('system', msg);
}

function onRconDisconnected() {
    rconConnected = false;
    if ($('#rconForm')) $('#rconForm').style.display = 'block';
    if ($('#rconConnected')) $('#rconConnected').style.display = 'none';
    if (!pteroConnected) {
        if ($('#rustActions')) $('#rustActions').style.display = 'none';
    }
    addMessage('system', 'RCON disconnected.');
}

function onPteroConnected(data) {
    removeMessage('progress-msg');
    pteroConnected = true;
    if ($('#pteroForm')) $('#pteroForm').style.display = 'none';
    if ($('#pteroConnected')) $('#pteroConnected').style.display = 'flex';
    if ($('#rustSaveSection')) $('#rustSaveSection').style.display = 'block';

    const servers = data.servers || [];
    const selectedId = data.selected_server || '';

    // Find the selected server's name for display
    const selectedServer = servers.find(s => s.identifier === selectedId);
    const selectedName = selectedServer ? selectedServer.name : selectedId;

    let msg = 'Pterodactyl Panel connected.';
    if (servers.length > 0) {
        msg += ` Found ${servers.length} server(s):\n`;
        for (const s of servers) {
            const marker = s.identifier === selectedId ? ' ← selected' : '';
            const desc = s.description ? ` — ${s.description}` : '';
            msg += `  • <strong>${escapeHtml(s.name || s.identifier)}</strong>${escapeHtml(desc)}${marker}\n`;
        }
        if (selectedName) {
            msg += `\nActive server: <strong>${escapeHtml(selectedName)}</strong>`;
        }
    }
    addMessage('system', msg);

    // If multiple servers, show a selector so the user can switch
    if (servers.length > 1) {
        let selectHtml = '<div class="ptero-server-select" style="margin:8px 0;">';
        selectHtml += '<label>Switch server: </label>';
        selectHtml += '<select id="pteroServerSelect" onchange="switchPteroServer(this.value)">';
        for (const s of servers) {
            const sel = s.identifier === selectedId ? ' selected' : '';
            const label = s.name || s.identifier;
            selectHtml += `<option value="${escapeHtml(s.identifier)}"${sel}>${escapeHtml(label)}</option>`;
        }
        selectHtml += '</select></div>';
        addMessage('system', selectHtml);
    }

    if (data.warning) {
        addMessage('system', `⚠️ ${escapeHtml(data.warning)}`);
    }
}

function switchPteroServer(serverId) {
    if (!serverId) return;
    addMessage('system', `Switching to server ${escapeHtml(serverId)}...`);
    // Re-emit with the new server_id — the backend will update the stored connection
    const payload = { server_id: serverId };
    // Reuse existing connection credentials
    if (_pteroState.url) payload.base_url = _pteroState.url;
    if (_pteroState.apiKeyEntered) {
        const apiKey = ($('#pteroApiKey') || {}).value || '';
        if (apiKey) payload.api_key = apiKey;
    }
    if (_pteroState.apiKeySaved) {
        payload.use_saved_key = true;
        const profileSelect = $('#profileSelect');
        if (profileSelect?.value) payload.profile_name = profileSelect.value;
    }
    socket.emit('rust_connect_pterodactyl', payload);
}

function onPteroDisconnected() {
    pteroConnected = false;
    if ($('#pteroForm')) $('#pteroForm').style.display = 'block';
    if ($('#pteroConnected')) $('#pteroConnected').style.display = 'none';
    addMessage('system', 'Pterodactyl Panel disconnected.');
}

function sendRconCommand() {
    const input = $('#rconCommand');
    if (!input) return;
    const cmd = input.value.trim();
    if (!cmd) return;
    input.value = '';
    addMessage('user', `RCON: ${escapeHtml(cmd)}`);
    socket.emit('rust_rcon_command', { command: cmd });
}

function onRconResponse(data) {
    const response = data.response || '(no output)';
    addMessage('agent', `<strong>RCON &gt; ${escapeHtml(data.command)}</strong>\n<pre class="rcon-output">${escapeHtml(response)}</pre>`);
}

function rustAction(action) {
    updateProgress(`Running: ${action}...`);
    socket.emit('rust_quick_action', { action });
}

function onRustActionResult(data) {
    removeMessage('progress-msg');
    const action = data.action || 'action';
    const result = typeof data.result === 'string' ? data.result : JSON.stringify(data.result, null, 2);
    addMessage('agent', `<strong>${escapeHtml(action)}</strong>\n<pre class="rcon-output">${escapeHtml(result)}</pre>`);
}

function rustRunDiagnostics() {
    updateProgress('Running Rust server diagnostics...');
    socket.emit('rust_run_diagnostics', {});
}

function rustDiagnoseLag() {
    updateProgress('Diagnosing lag and rubber-banding...');
    socket.emit('rust_diagnose_lag', {});
}

function onRustDiagnosticsResult(data) {
    removeMessage('progress-msg');
    const results = data.diagnostics || [];
    if (results.length === 0) {
        addMessage('agent', 'No diagnostic results.');
        return;
    }

    // Separate issues from healthy checks
    const issues = results.filter(r => r.status !== 'ok');
    const healthy = results.filter(r => r.status === 'ok');

    // Sort issues by severity
    const sevOrder = { critical: 0, high: 1, medium: 2, low: 3 };
    issues.sort((a, b) => (sevOrder[a.severity] || 99) - (sevOrder[b.severity] || 99));

    const html = renderRustDiagnostics(issues, healthy);
    addMessage('agent', html);
}

function onRustLagResult(data) {
    removeMessage('progress-msg');
    const report = data.diagnosis || {};
    const findings = report.findings || [];
    const summary = report.summary || '';
    const timestamp = report.timestamp || '';

    if (findings.length === 0) {
        let html = '<div class="diag-report">';
        html += '<div class="diag-header diag-header-ok"><strong>Lag Diagnosis</strong> — No obvious issues detected</div>';
        if (summary) html += `<div class="diag-summary">${escapeHtml(summary)}</div>`;
        if (timestamp) html += `<div class="diag-timestamp">${escapeHtml(timestamp)}</div>`;
        html += '</div>';
        addMessage('agent', html);
        return;
    }

    let html = '<div class="diag-report">';
    html += '<div class="diag-header diag-header-warn"><strong>Lag Diagnosis</strong> — Issues found (ranked by severity)</div>';
    if (summary) html += `<div class="diag-summary">${escapeHtml(summary)}</div>`;

    for (const f of findings) {
        const sevClass = f.severity === 'critical' ? 'diag-card-critical' :
                         f.severity === 'high' ? 'diag-card-high' :
                         f.severity === 'medium' ? 'diag-card-medium' : 'diag-card-low';
        html += `<div class="diag-card ${sevClass}">`;
        html += `<div class="diag-card-title"><span class="diag-sev-badge diag-sev-${escapeHtml(f.severity)}">${escapeHtml(f.severity).toUpperCase()}</span> ${escapeHtml(f.cause || '')}</div>`;
        html += `<div class="diag-card-details">${escapeHtml(f.details || '')}</div>`;
        if (f.likely_reason) html += `<div class="diag-card-reason"><strong>Why:</strong> ${escapeHtml(f.likely_reason)}</div>`;
        if (f.fix) html += `<div class="diag-card-fix"><strong>Fix:</strong> ${escapeHtml(f.fix)}</div>`;
        html += '</div>';
    }

    if (timestamp) html += `<div class="diag-timestamp">${escapeHtml(timestamp)}</div>`;
    html += '</div>';
    addMessage('agent', html);
}

function renderRustDiagnostics(issues, healthy) {
    let html = '<div class="diag-report">';

    // Header with issue count
    if (issues.length === 0) {
        html += '<div class="diag-header diag-header-ok"><strong>Server Diagnostics</strong> — All checks passed</div>';
    } else {
        const critical = issues.filter(r => r.severity === 'high' && r.status === 'critical').length;
        const warnings = issues.length - critical;
        let label = '';
        if (critical) label += `${critical} critical`;
        if (critical && warnings) label += ', ';
        if (warnings) label += `${warnings} warning(s)`;
        html += `<div class="diag-header diag-header-warn"><strong>Server Diagnostics</strong> — ${label} found</div>`;
    }

    // Issue cards with full details and fix buttons
    for (const r of issues) {
        const sevClass = r.status === 'critical' ? 'diag-card-critical' :
                         r.severity === 'high' ? 'diag-card-high' :
                         r.severity === 'medium' ? 'diag-card-medium' : 'diag-card-low';
        const friendlyName = (r.name || '').replace('check_', '').replace(/_/g, ' ');
        html += `<div class="diag-card ${sevClass}">`;
        html += `<div class="diag-card-title"><span class="diag-sev-badge diag-sev-${escapeHtml(r.severity)}">${escapeHtml(r.status || r.severity).toUpperCase()}</span> ${escapeHtml(friendlyName)}</div>`;
        html += `<div class="diag-card-details">${escapeHtml(r.details || '(no details)')}</div>`;

        // Render fix suggestions as actionable buttons
        if (r.fix && Array.isArray(r.fix) && r.fix.length > 0) {
            html += '<div class="diag-card-fixes">';
            html += '<strong>Suggested fixes:</strong>';
            for (const fix of r.fix) {
                const isDestructive = fix.destructive;
                const btnClass = isDestructive ? 'btn-fix btn-fix-destructive' : 'btn-fix';
                const icon = isDestructive ? '⚠ ' : '▶ ';
                if (fix.command_rcon) {
                    html += `<button class="${btnClass}" onclick="confirmAndRunFix('rcon', '${escapeHtml(fix.command_rcon)}', ${isDestructive})" title="${escapeHtml(fix.description || '')}">`;
                    html += `${icon}${escapeHtml(fix.description || fix.command_rcon)} <code>${escapeHtml(fix.command_rcon)}</code></button>`;
                } else if (fix.command_ptero) {
                    html += `<button class="${btnClass}" onclick="confirmAndRunFix('ptero', '${escapeHtml(fix.command_ptero)}', ${isDestructive})" title="${escapeHtml(fix.description || '')}">`;
                    html += `${icon}${escapeHtml(fix.description || fix.command_ptero)} <code>${escapeHtml(fix.command_ptero)}</code></button>`;
                }
            }
            html += '</div>';
        }
        html += '</div>';
    }

    // Collapsible healthy checks summary
    if (healthy.length > 0) {
        html += '<details class="diag-healthy-section">';
        html += `<summary class="diag-healthy-summary">${healthy.length} check(s) passed</summary>`;
        html += '<div class="diag-healthy-list">';
        for (const r of healthy) {
            const friendlyName = (r.name || '').replace('check_', '').replace(/_/g, ' ');
            html += `<div class="diag-healthy-item"><span class="diag-check-ok">✓</span> <strong>${escapeHtml(friendlyName)}</strong>`;
            if (r.details) html += ` — ${escapeHtml(r.details)}`;
            html += '</div>';
        }
        html += '</div></details>';
    }

    html += '</div>';
    return html;
}

function confirmAndRunFix(type, command, isDestructive) {
    const label = isDestructive ? 'DESTRUCTIVE' : 'safe';
    const msg = isDestructive
        ? `⚠️ This is a DESTRUCTIVE action.\n\nCommand: ${command}\n\nAre you sure you want to run this?`
        : `Run this fix?\n\nCommand: ${command}`;
    if (!confirm(msg)) return;

    if (type === 'rcon') {
        updateProgress(`Running fix: ${command}...`);
        socket.emit('rust_rcon_command', { command });
    } else if (type === 'ptero') {
        // Handle ptero commands like "power:restart"
        const parts = command.split(':');
        if (parts.length === 2 && parts[0] === 'power') {
            updateProgress(`Running fix: ${parts[1]} server...`);
            socket.emit('rust_ptero_action', { action: parts[1] });
        }
    }
}

function onRustPluginResult(data) {
    const action = data.action || '';
    const plugin = data.plugin || '';
    if (action === 'get_config' && data.config) {
        const config = typeof data.config === 'string' ? data.config : JSON.stringify(data.config, null, 2);
        addMessage('agent', `<strong>${escapeHtml(plugin)} Config</strong>\n<pre class="rcon-output">${escapeHtml(config)}</pre>`);
    } else {
        const result = typeof data.result === 'string' ? data.result : JSON.stringify(data.result, null, 2);
        addMessage('agent', `<strong>Plugin ${escapeHtml(action)}: ${escapeHtml(plugin)}</strong>\n<pre class="rcon-output">${escapeHtml(result)}</pre>`);
    }
}

function onRustPteroResult(data) {
    removeMessage('progress-msg');
    const action = data.action || '';
    const result = typeof data.result === 'string' ? data.result : JSON.stringify(data.result, null, 2);
    addMessage('agent', `<strong>Pterodactyl: ${escapeHtml(action)}</strong>\n<pre class="rcon-output">${escapeHtml(result)}</pre>`);
}

// ---------- Model Selection ----------

function loadModels() {
    fetch('/api/models')
        .then(r => r.json())
        .then(data => {
            const select = $('#modelSelect');
            if (!select) return;

            const models = data.models || [];
            const selected = data.selected || '';

            // Preserve current selection if no server-side selection
            const currentVal = select.value;

            select.innerHTML = '';
            if (models.length === 0) {
                select.innerHTML = '<option value="">No models available</option>';
                return;
            }

            models.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m.id;
                opt.textContent = m.display_name || m.id;
                select.appendChild(opt);
            });

            // Set selected model: server selection > current > default
            if (selected) {
                select.value = selected;
            } else if (currentVal && [...select.options].some(o => o.value === currentVal)) {
                select.value = currentVal;
            }
            // If nothing matched, the first option is selected by default
        })
        .catch(() => {
            const select = $('#modelSelect');
            if (select && select.options.length <= 1) {
                select.innerHTML = '<option value="">Failed to load models</option>';
            }
        });
}

function changeModel(modelId) {
    if (!modelId) return;
    fetch('/api/models/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelId }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'ok') {
            showFlash(`Model switched to ${modelId}`, 'success');
        } else {
            showFlash(data.error || 'Failed to switch model', 'error');
        }
    })
    .catch(() => showFlash('Failed to switch model', 'error'));
}

// ---------- Dashboard Init ----------

function initDashboard() {
    initSocket();
    initSidebarToggle();
    initChatInput();
    loadProfiles();
    checkForUpdates();
    loadModels();
    // Fetch initial token data
    fetch('/api/tokens').then(r => r.json()).then(data => {
        tokenBreakdown = data;
        if (data.session) {
            totalTokens = data.session.total_tokens || 0;
            const el = $('.token-count');
            if (el) el.textContent = formatNumber(totalTokens);
        }
        const monthEl = $('.token-monthly');
        if (monthEl && data.monthly) {
            monthEl.textContent = formatNumber(data.monthly.total_tokens);
        }
    }).catch(() => {});
    // Check for updates periodically (every 30 minutes)
    setInterval(checkForUpdates, 30 * 60 * 1000);
    // Refresh model list every 5 minutes
    setInterval(loadModels, 5 * 60 * 1000);
}

// ---------- Auto-init on page load ----------

document.addEventListener('DOMContentLoaded', () => {
    initSocket();
    initSidebarToggle();
});
