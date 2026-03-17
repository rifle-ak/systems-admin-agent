/* ===== SysAdmin Agent - Main Application JS ===== */

// ---------- Globals ----------
let socket = null;
let isConnected = false;
let totalTokens = 0;
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

// ---------- SocketIO Setup ----------

function initSocket() {
    if (socket) return;
    socket = io({ transports: ['websocket', 'polling'] });

    socket.on('connect', () => {
        console.log('SocketIO connected');
    });

    socket.on('disconnect', () => {
        console.log('SocketIO disconnected');
    });

    // Server connection events
    socket.on('server_connected', onServerConnected);
    socket.on('server_disconnected', onServerDisconnected);

    // Scan events
    socket.on('scan_progress', onScanProgress);
    socket.on('scan_complete', onScanComplete);

    // Diagnostics events
    socket.on('diagnostics_result', onDiagnosticsResult);

    // Agent events
    socket.on('agent_thinking', onAgentThinking);
    socket.on('agent_plan', onAgentPlan);
    socket.on('agent_step_result', onAgentStepResult);
    socket.on('agent_complete', onAgentComplete);

    // Approval events
    socket.on('approval_required', onApprovalRequired);

    // Command events
    socket.on('command_result', onCommandResult);

    // Snapshot events
    socket.on('snapshots_list', onSnapshotsList);

    // Install events (setup page)
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

function updateTokenUsage(tokens) {
    totalTokens += (tokens || 0);
    const el = $('.token-count');
    if (el) el.textContent = formatNumber(totalTokens);
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

// ---------- Render Helpers ----------

function renderCodeBlock(text, exitCode = null) {
    const block = document.createElement('div');
    block.className = 'code-block';

    const code = document.createElement('code');
    code.textContent = text || '(no output)';
    block.appendChild(code);

    if (exitCode !== null && exitCode !== undefined) {
        const exitEl = document.createElement('span');
        exitEl.className = 'exit-code ' + (exitCode === 0 ? 'success' : 'error');
        exitEl.textContent = `Exit code: ${exitCode}`;
        block.appendChild(exitEl);
    }

    return block;
}

function renderDiagnostics(results) {
    if (!results || results.length === 0) {
        const p = document.createElement('p');
        p.textContent = 'No issues found.';
        return p;
    }

    const table = document.createElement('table');
    table.className = 'diag-table';
    table.innerHTML = `
        <thead>
            <tr>
                <th>Status</th>
                <th>Check</th>
                <th>Severity</th>
                <th>Details</th>
            </tr>
        </thead>
    `;
    const tbody = document.createElement('tbody');

    for (const check of results) {
        const tr = document.createElement('tr');
        const statusClass = check.status === 'pass' ? 'pass' : (check.status === 'warning' ? 'warn' : 'fail');
        const statusSymbol = check.status === 'pass' ? '\u2713' : (check.status === 'warning' ? '\u26A0' : '\u2717');
        const severity = (check.severity || 'info').toLowerCase();

        tr.innerHTML = `
            <td><span class="status-icon ${statusClass}">${statusSymbol}</span></td>
            <td>${escapeHtml(check.name)}</td>
            <td><span class="severity-badge severity-${severity}">${escapeHtml(severity)}</span></td>
            <td>${escapeHtml(check.details || '-')}</td>
        `;
        tbody.appendChild(tr);
    }

    table.appendChild(tbody);
    return table;
}

function renderPlan(steps) {
    const list = document.createElement('ol');
    list.className = 'plan-steps';

    for (let i = 0; i < steps.length; i++) {
        const step = steps[i];
        const li = document.createElement('li');
        li.className = 'plan-step pending';
        li.dataset.stepIndex = i;

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

function renderApprovalCard(action) {
    const card = document.createElement('div');
    card.className = 'approval-card' + (action.destructive ? ' destructive' : '');
    card.id = `approval-${action.id}`;

    card.innerHTML = `
        <div class="approval-header">
            <span class="warning-icon">${action.destructive ? '\u26A0' : '\u2753'}</span>
            <span>${action.destructive ? 'Destructive Action Requires Approval' : 'Action Requires Approval'}</span>
        </div>
        <div class="approval-command">${escapeHtml(action.command)}</div>
        <div class="approval-desc">${escapeHtml(action.description || '')}</div>
        <div class="approval-actions">
            <button class="btn btn-success" onclick="approveAction('${escapeHtml(action.id)}', true)">Approve</button>
            <button class="btn btn-danger" onclick="approveAction('${escapeHtml(action.id)}', false)">Deny</button>
        </div>
    `;

    pendingApprovals[action.id] = action;
    return card;
}

// ---------- SocketIO Event Handlers ----------

function onServerConnected(data) {
    isConnected = true;
    setConnectionStatus('connected', 'Connected');

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

    // Disable form inputs
    if (connectForm) {
        for (const input of connectForm.querySelectorAll('input, select')) {
            input.disabled = true;
        }
    }

    // Populate info
    if (data.os_info) {
        const info = data.os_info;
        if ($('#infoOS')) $('#infoOS').textContent = info.distro || info.os || '-';
        if ($('#infoHostname')) $('#infoHostname').textContent = info.hostname || '-';
        if ($('#infoUptime')) $('#infoUptime').textContent = info.uptime || '-';
    }

    addMessage('system', `Connected to server. ${data.apps ? data.apps.length + ' applications detected.' : ''}`);
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

    if (connectForm) {
        for (const input of connectForm.querySelectorAll('input, select')) {
            input.disabled = false;
        }
    }

    addMessage('system', 'Disconnected from server.');
}

function onScanProgress(data) {
    const existingId = 'scan-progress-msg';
    let el = document.getElementById(existingId);
    if (!el) {
        el = addMessage('agent', `<span id="scan-progress-text">Scanning: ${escapeHtml(data.message || data.step || '...')}</span>`, { id: existingId });
    } else {
        const textEl = el.querySelector('#scan-progress-text');
        if (textEl) textEl.innerHTML = `Scanning: ${escapeHtml(data.message || data.step || '...')}`;
    }
    scrollToBottom();
}

function onScanComplete(data) {
    removeMessage('scan-progress-msg');
    addMessage('agent', 'Scan complete. ' + escapeHtml(data.summary || ''));
}

function onDiagnosticsResult(data) {
    removeMessage('thinking-msg');
    const results = data.results || data.checks || data;
    const table = renderDiagnostics(Array.isArray(results) ? results : []);
    addMessage('agent', table);
}

function onAgentThinking() {
    removeMessage('thinking-msg');
    addMessage('thinking', '<span class="thinking-dots">Thinking</span>', { id: 'thinking-msg' });
}

function onAgentPlan(data) {
    removeMessage('thinking-msg');
    const steps = data.steps || data.plan || [];
    const planEl = renderPlan(steps);
    currentPlanEl = planEl;
    addMessage('agent', planEl);
}

function onAgentStepResult(data) {
    if (currentPlanEl) {
        const index = data.step_index ?? data.index;
        const items = currentPlanEl.querySelectorAll('.plan-step');

        // Mark previous steps as done
        for (let i = 0; i < items.length; i++) {
            if (i < index) {
                items[i].className = 'plan-step done';
                items[i].querySelector('.step-indicator').textContent = '\u2713';
            } else if (i === index) {
                const status = data.status || 'done';
                items[i].className = `plan-step ${status}`;
                if (status === 'done') {
                    items[i].querySelector('.step-indicator').textContent = '\u2713';
                } else if (status === 'skipped') {
                    items[i].querySelector('.step-indicator').textContent = '-';
                }
            }
        }
    }

    // Show result if present
    if (data.output || data.result) {
        const block = renderCodeBlock(data.output || data.result, data.exit_code);
        addMessage('agent', block);
    }
}

function onAgentComplete(data) {
    removeMessage('thinking-msg');
    currentPlanEl = null;

    if (data.token_usage) {
        updateTokenUsage(data.token_usage.total || data.token_usage.input + data.token_usage.output || 0);
    }

    if (data.summary || data.message) {
        addMessage('agent', escapeHtml(data.summary || data.message));
    }
}

function onApprovalRequired(data) {
    removeMessage('thinking-msg');
    const card = renderApprovalCard(data);
    addMessage('agent', card);
}

function onCommandResult(data) {
    const output = (data.stdout || '') + (data.stderr ? '\n' + data.stderr : '');
    const block = renderCodeBlock(output.trim(), data.exit_code);
    addMessage('agent', block);
}

function onSnapshotsList(data) {
    const snapshots = data.snapshots || data;
    if (!snapshots || snapshots.length === 0) {
        addMessage('system', 'No snapshots available for rollback.');
        return;
    }

    let html = '<strong>Available Snapshots:</strong><br>';
    for (const snap of snapshots) {
        const id = escapeHtml(snap.id || snap.snapshot_id);
        const desc = escapeHtml(snap.description || snap.timestamp || id);
        html += `<div style="margin: 4px 0;">
            <button class="btn btn-small btn-secondary" onclick="executeRollback('${id}')">${desc}</button>
        </div>`;
    }
    addMessage('agent', html);
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
    const payload = {
        host: $('#host')?.value,
        port: parseInt($('#port')?.value || '22'),
        username: $('#username')?.value,
        password: authType === 'password' ? $('#sshPassword')?.value : undefined,
        key_path: authType === 'key' ? $('#keyPath')?.value : undefined,
        passphrase: authType === 'key' ? $('#passphrase')?.value : undefined,
    };

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

function approveAction(id, approved) {
    if (!socket) return;
    socket.emit('approve_action', { id, approved });

    // Update the card UI
    const card = document.getElementById(`approval-${id}`);
    if (card) {
        card.classList.add('resolved');
        const actions = card.querySelector('.approval-actions');
        if (actions) {
            actions.innerHTML = `<span class="approval-resolved">${approved ? 'Approved' : 'Denied'}</span>`;
        }
    }
    delete pendingApprovals[id];
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
    addMessage('system', `Rolling back to snapshot ${escapeHtml(snapshotId)}...`);
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

    showFlash('Profile loaded', 'success');
}

async function saveProfile() {
    const name = prompt('Profile name:');
    if (!name) return;

    const authType = $('#authType')?.value || 'password';
    const payload = {
        name,
        host: $('#host')?.value,
        port: parseInt($('#port')?.value || '22'),
        username: $('#username')?.value,
        auth_type: authType,
        key_path: authType === 'key' ? ($('#keyPath')?.value || '') : '',
    };

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

// ---------- Dashboard Init ----------

function initDashboard() {
    initSocket();
    initSidebarToggle();
    initChatInput();
    loadProfiles();
}

// ---------- Auto-init on page load ----------

document.addEventListener('DOMContentLoaded', () => {
    initSocket();
    initSidebarToggle();
});
