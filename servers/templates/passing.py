from .base import render_template

_EXTRA_CSS = '''
.key-display {
    display: grid;
    grid-template-areas: ". up ." "left down right";
    gap: 4px;
    justify-content: center;
    margin: 8px 0 4px;
}
.key-box {
    width: 32px; height: 32px;
    display: flex; align-items: center; justify-content: center;
    border: 1px solid var(--border-color);
    border-radius: 4px;
    font-size: 13px; font-weight: 700;
    color: var(--text-muted);
    background: var(--bg-sidebar);
    transition: background 0.1s, border-color 0.1s, color 0.1s;
}
.key-box.active { background: rgba(63,185,80,0.2); border-color: var(--accent-green); color: var(--accent-green); }
.key-up    { grid-area: up; }
.key-down  { grid-area: down; }
.key-left  { grid-area: left; }
.key-right { grid-area: right; }

.mode-badge {
    display: inline-block;
    padding: 4px 10px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 600;
    margin-bottom: 10px;
}
.mode-badge.manual { background: rgba(31,111,235,0.15); color: var(--accent-blue); border: 1px solid rgba(31,111,235,0.35); }
.mode-badge.auto   { background: rgba(63,185,80,0.15); color: var(--accent-green); border: 1px solid rgba(63,185,80,0.35); }

.speed-display { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
.speed-box { text-align: center; padding: 8px; background: var(--bg-sidebar); border: 1px solid var(--border-color); border-radius: 6px; }
.speed-value { font-size: 22px; font-weight: 700; font-family: monospace; color: var(--accent-blue); }
.speed-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; margin-top: 3px; }

.instructions { font-size: 12px; color: var(--text-secondary); line-height: 1.6; }
.instructions code { background: var(--bg-sidebar); padding: 2px 6px; border-radius: 3px; font-size: 11px; color: var(--accent-orange); }
'''

_CONTENT = '''
    <div class="container">
        <div class="video-section">
            <img src="{{ url_for('video') }}" class="stream">
        </div>

        <div class="controls-section">
            <div class="card">
                <div class="card-header">Drive Mode</div>
                <span id="mode-badge" class="mode-badge manual">Manual — arrow keys / WASD</span>
                <div style="display:flex;gap:10px;margin-bottom:8px">
                    <button id="mode-btn" onclick="toggleMode()" class="button" style="flex:1;background:#555">Switch to Auto</button>
                    <button onclick="resetPosition()" class="button" style="flex:1;background:#444">Reset</button>
                </div>
            </div>

            <div class="card" id="auto-panel" style="display:none">
                <div class="card-header">Automatic (visual servoing)</div>
                <div style="display:flex;align-items:center;gap:16px;margin-bottom:12px">
                    <span id="run-indicator" style="display:inline-block;width:14px;height:14px;border-radius:50%;background:#e74c3c;flex-shrink:0"></span>
                    <span id="run-label" style="font-size:14px;font-weight:600;color:var(--text-secondary)">STOPPED</span>
                </div>
                <div style="display:flex;gap:10px;margin-bottom:8px">
                    <button onclick="driveStart()" class="button success" style="flex:1">Start</button>
                    <button onclick="driveStop()"  class="button" style="flex:1;background:var(--accent-orange,#e67e22)">Stop</button>
                </div>
                <p style="font-size:11px;color:var(--text-muted);line-height:1.5">
                    Bot follows yellow/white lane lines using the same PD controller as visual lane servoing.
                </p>
            </div>

            <div class="card" id="manual-panel">
                <div class="card-header">Manual Control</div>
                <div class="key-display">
                    <div class="key-box key-up"    id="key-up">&#9650;</div>
                    <div class="key-box key-left"  id="key-left">&#9664;</div>
                    <div class="key-box key-down"  id="key-down">&#9660;</div>
                    <div class="key-box key-right" id="key-right">&#9654;</div>
                </div>
                <div class="speed-display">
                    <div class="speed-box"><div class="speed-value" id="speed-left">0.00</div><div class="speed-label">Left wheel</div></div>
                    <div class="speed-box"><div class="speed-value" id="speed-right">0.00</div><div class="speed-label">Right wheel</div></div>
                </div>
            </div>

            <div class="card">
                <div class="card-header">Status</div>
                <div id="lane-status" style="font-size:12px;color:var(--text-secondary)">Lane: —</div>
            </div>
        </div>
    </div>
'''

_JS = '''
    let _manualMode = true;
    const keyState = {up: false, down: false, left: false, right: false};
    const keyMap = {
        'ArrowUp': 'up', 'w': 'up', 'W': 'up',
        'ArrowDown': 'down', 's': 'down', 'S': 'down',
        'ArrowLeft': 'left', 'a': 'left', 'A': 'left',
        'ArrowRight': 'right', 'd': 'right', 'D': 'right',
    };

    function setRunningUI(isRunning) {
        const indicator = document.getElementById('run-indicator');
        const label     = document.getElementById('run-label');
        if (!indicator || !label) return;
        indicator.style.background = isRunning ? '#2ecc71' : '#e74c3c';
        label.textContent = isRunning ? 'RUNNING' : 'STOPPED';
        label.style.color = isRunning ? '#2ecc71' : 'var(--text-secondary)';
    }

    function updateModeUI() {
        const badge = document.getElementById('mode-badge');
        const btn   = document.getElementById('mode-btn');
        const auto  = document.getElementById('auto-panel');
        const manual = document.getElementById('manual-panel');
        if (badge) {
            badge.className = 'mode-badge ' + (_manualMode ? 'manual' : 'auto');
            badge.textContent = _manualMode ? 'Manual — arrow keys / WASD' : 'Auto — visual lane servoing';
        }
        if (btn) btn.textContent = _manualMode ? 'Switch to Auto' : 'Switch to Manual';
        if (auto) auto.style.display = _manualMode ? 'none' : 'block';
        if (manual) manual.style.display = _manualMode ? 'block' : 'none';
    }

    function toggleMode() {
        _manualMode = !_manualMode;
        postJSON('/set_mode', {mode: _manualMode ? 'manual' : 'auto'});
        updateModeUI();
        if (!_manualMode) setRunningUI(false);
    }

    function driveStart() {
        postJSON('/start', {}).then(() => setRunningUI(true));
    }

    function driveStop() {
        postJSON('/stop', {}).then(() => setRunningUI(false));
    }

    function resetPosition() {
        postJSON('/reset', {}).then(data => {
            if (data && data.running !== undefined) setRunningUI(data.running);
        });
    }

    function updateKeyDisplay() {
        for (const [key, active] of Object.entries(keyState)) {
            const el = document.getElementById('key-' + key);
            if (el) el.classList.toggle('active', active);
        }
    }

    function sendKeys() {
        fetch('/keys', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(keyState)})
            .then(r => r.json())
            .then(data => {
                const sl = document.getElementById('speed-left');
                const sr = document.getElementById('speed-right');
                if (sl) sl.textContent = data.left.toFixed(2);
                if (sr) sr.textContent = data.right.toFixed(2);
            }).catch(() => {});
    }

    function releaseAll() {
        let changed = Object.values(keyState).some(Boolean);
        Object.keys(keyState).forEach(k => keyState[k] = false);
        if (changed) { updateKeyDisplay(); if (_manualMode) sendKeys(); }
    }

    document.addEventListener('keydown', e => {
        const dir = keyMap[e.key];
        if (dir && !keyState[dir]) { e.preventDefault(); keyState[dir] = true; updateKeyDisplay(); if (_manualMode) sendKeys(); }
    });
    document.addEventListener('keyup', e => {
        const dir = keyMap[e.key];
        if (dir && keyState[dir]) { e.preventDefault(); keyState[dir] = false; updateKeyDisplay(); if (_manualMode) sendKeys(); }
    });
    window.addEventListener('blur', releaseAll);
    document.addEventListener('visibilitychange', () => { if (document.hidden) releaseAll(); });
    setInterval(() => { if (_manualMode && Object.values(keyState).some(Boolean)) sendKeys(); }, 150);

    async function pollStatus() {
        try {
            const data = await fetch('/status').then(r => r.json());
            if (data.manual_mode !== undefined && data.manual_mode !== _manualMode) {
                _manualMode = data.manual_mode;
                updateModeUI();
            }
            if (!_manualMode) setRunningUI(data.running);
            const sl = document.getElementById('speed-left');
            const sr = document.getElementById('speed-right');
            if (sl && data.left !== undefined) sl.textContent = data.left.toFixed(2);
            if (sr && data.right !== undefined) sr.textContent = data.right.toFixed(2);
            const ls = document.getElementById('lane-status');
            if (ls) {
                if (data.manual_mode) {
                    ls.textContent = 'Manual drive active';
                } else if (data.lane_detected) {
                    ls.textContent = 'Lane OK — driving';
                } else {
                    ls.textContent = 'No lane detected — bot will stop';
                }
            }
        } catch (_) {}
    }
    setInterval(pollStatus, 500);
    updateModeUI();
'''

PASSING_TEMPLATE = render_template(
    'Passing',
    'Manual drive or automatic lane following',
    _CONTENT,
    extra_css=_EXTRA_CSS,
    extra_js=_JS,
)
