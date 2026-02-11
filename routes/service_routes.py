"""Service management endpoints."""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from services import ServiceManager

router = APIRouter(prefix="/services", tags=["services"])

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>💻 Service Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #eee; padding: 24px; }
  h1 { margin-bottom: 8px; }
  .subtitle { color: #888; margin-bottom: 20px; font-size: 14px; }
  .dry-run-badge { background: #ff9800; color: #000; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin-left: 10px; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 24px; background: #16213e; border-radius: 8px; overflow: hidden; }
  th, td { padding: 14px 18px; text-align: left; border-bottom: 1px solid #1a1a2e; }
  th { background: #0f3460; color: #aaa; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
  tr:last-child td { border-bottom: none; }
  tr:hover { background: #1a2744; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; }
  .dot.on  { background: #4caf50; box-shadow: 0 0 8px #4caf50; }
  .dot.off { background: #f44336; }
  button { padding: 8px 20px; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; color: #fff; font-weight: 500; transition: all 0.2s; }
  .btn-start { background: #2e7d32; }
  .btn-start:hover { background: #388e3c; }
  .btn-stop  { background: #c62828; }
  .btn-stop:hover { background: #d32f2f; }
  .btn-restart { background: #1565c0; margin-left: 8px; }
  .btn-restart:hover { background: #1976d2; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  .logs-section { margin-top: 24px; }
  .log-box { background: #0d1117; padding: 14px; border-radius: 8px; margin-bottom: 20px;
             max-height: 250px; overflow-y: auto; font-family: 'SF Mono', Monaco, monospace; font-size: 12px;
             white-space: pre-wrap; color: #8b949e; border: 1px solid #30363d; }
  .log-title { font-weight: 600; margin-bottom: 8px; font-size: 14px; color: #c9d1d9; }
  .status-text { font-size: 13px; }
  .status-text.on { color: #4caf50; }
  .status-text.off { color: #f44336; }
  .uptime { color: #888; font-size: 12px; }
  .actions { display: flex; gap: 8px; }
  .refresh-info { color: #666; font-size: 12px; margin-top: 16px; }
  .state-section { margin-bottom: 24px; }
  .state-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
  .state-card { background: #16213e; border-radius: 8px; padding: 16px; }
  .state-card h3 { font-size: 14px; color: #aaa; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }
  .state-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #1a1a2e; }
  .state-row:last-child { border-bottom: none; }
  .state-label { color: #888; font-size: 13px; }
  .state-value { font-family: 'SF Mono', Monaco, monospace; font-size: 13px; color: #4caf50; }
  .state-value.disconnected { color: #f44336; }
  /* Robot Control Section */
  .control-section { margin-bottom: 24px; }
  .control-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
  .control-card { background: #16213e; border-radius: 8px; padding: 16px; }
  .control-card h3 { font-size: 14px; color: #aaa; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
  .control-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #1a1a2e; }
  .control-row:last-child { border-bottom: none; }
  .control-label { color: #888; font-size: 13px; }
  .control-input { width: 80px; padding: 6px 10px; border: 1px solid #30363d; border-radius: 4px; background: #0d1117; color: #eee; font-size: 13px; text-align: center; }
  .control-input:focus { outline: none; border-color: #1565c0; }
  .toggle-switch { position: relative; width: 48px; height: 24px; }
  .toggle-switch input { opacity: 0; width: 0; height: 0; }
  .toggle-slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background: #444; border-radius: 24px; transition: 0.3s; }
  .toggle-slider:before { position: absolute; content: ""; height: 18px; width: 18px; left: 3px; bottom: 3px; background: #fff; border-radius: 50%; transition: 0.3s; }
  .toggle-switch input:checked + .toggle-slider { background: #4caf50; }
  .toggle-switch input:checked + .toggle-slider:before { transform: translateX(24px); }
  .btn-action { padding: 10px 24px; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; color: #fff; font-weight: 500; transition: all 0.2s; width: 100%; margin-top: 8px; }
  .btn-rewind { background: #ff9800; }
  .btn-rewind:hover { background: #ffa726; }
  .btn-home { background: #9c27b0; }
  .btn-home:hover { background: #ab47bc; }
  .btn-action:disabled { opacity: .5; cursor: not-allowed; }
  .status-badge { font-size: 11px; padding: 2px 8px; border-radius: 4px; text-transform: uppercase; }
  .status-badge.enabled { background: #4caf50; color: #fff; }
  .status-badge.disabled { background: #666; color: #ccc; }
  .status-badge.active { background: #ff9800; color: #000; animation: pulse 1s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.6; } }
  .boundary-status { font-size: 12px; padding: 4px 8px; border-radius: 4px; }
  .boundary-status.safe { background: #1b5e20; color: #4caf50; }
  .boundary-status.warning { background: #e65100; color: #ff9800; }
  /* Info Row: Rewind Logs | Base Trajectory | Lease Queue */
  .info-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  @media (max-width: 1200px) { .info-row { grid-template-columns: 1fr; } }
  /* Trajectory Visualization */
  .trajectory-card { background: #16213e; border-radius: 8px; padding: 16px; }
  .trajectory-card h3 { font-size: 14px; color: #aaa; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }
  .trajectory-canvas-container { position: relative; width: 100%; aspect-ratio: 1; max-width: 267px; margin: 0 auto; }
  #trajectory-canvas { width: 100%; height: 100%; background: #0d1117; border-radius: 8px; border: 1px solid #30363d; }
  .trajectory-legend { display: flex; gap: 16px; margin-top: 12px; justify-content: center; flex-wrap: wrap; }
  .legend-item { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #888; }
  .legend-dot { width: 12px; height: 12px; border-radius: 50%; }
  .legend-dot.current { background: #4caf50; box-shadow: 0 0 8px #4caf50; }
  .legend-dot.odom-path { background: #64b5f6; }
  .legend-dot.mocap-path { background: #0d47a1; }
  .legend-dot.start { background: #9c27b0; }
  .legend-line { width: 20px; height: 2px; }
  .legend-line.boundary { background: #f44336; border: 1px dashed #f44336; }
  .trajectory-info { display: flex; gap: 24px; margin-top: 12px; justify-content: center; font-size: 12px; color: #888; }
  .trajectory-info span { font-family: 'SF Mono', Monaco, monospace; color: #4caf50; }
  /* Lease Queue */
  .lease-card { background: #16213e; border-radius: 8px; padding: 16px; }
  .lease-card h3 { font-size: 14px; color: #aaa; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }
  .lease-holder { font-family: 'SF Mono', Monaco, monospace; font-size: 14px; color: #4caf50; padding: 8px 0; }
  .lease-holder.none { color: #666; font-style: italic; }
  .lease-holder.resetting { color: #ff9800; }
  .lease-queue-list { list-style: none; padding: 0; }
  .lease-queue-list li { padding: 6px 0; border-bottom: 1px solid #1a1a2e; font-size: 13px; color: #888; display: flex; justify-content: space-between; }
  .lease-queue-list li:last-child { border-bottom: none; }
  .lease-queue-pos { color: #666; font-size: 11px; }
  .lease-queue-name { font-family: 'SF Mono', Monaco, monospace; color: #c9d1d9; }
  .activity-badge { font-size: 12px; padding: 4px 10px; border-radius: 4px; font-weight: 500; }
  .activity-badge.idle { background: #1b5e20; color: #4caf50; }
  .activity-badge.executing { background: #0d47a1; color: #42a5f5; animation: pulse 1.5s infinite; }
  .activity-badge.rewinding { background: #e65100; color: #ff9800; animation: pulse 1s infinite; }
  .activity-badge.resetting { background: #4a148c; color: #ce93d8; animation: pulse 1s infinite; }
  .activity-badge.recovering { background: #b71c1c; color: #ef9a9a; animation: pulse 1s infinite; }
</style></head><body>
<h1>Service Dashboard<span id="dry-run-badge" class="dry-run-badge" style="display:none">DRY-RUN</span></h1>
<p class="subtitle">TidyBot Agent Server — Backend Service Manager
  <span style="margin-left: 16px;">
    <a href="/face" target="_blank" style="color: #64b5f6; text-decoration: none; margin-right: 12px;">Face Display ↗</a>
    <a id="camera-link" href="#" target="_blank" style="color: #64b5f6; text-decoration: none;">Cameras ↗</a>
    <script>document.getElementById('camera-link').href = 'http://' + window.location.hostname + ':5581';</script>
  </span>
</p>

<!-- Row 1: Current State -->
<div class="state-section">
  <div class="state-grid">
    <div class="state-card">
      <h3>Base Odom</h3>
      <div class="state-row"><span class="state-label">X</span><span class="state-value" id="odom-x">—</span></div>
      <div class="state-row"><span class="state-label">Y</span><span class="state-value" id="odom-y">—</span></div>
      <div class="state-row"><span class="state-label">Theta</span><span class="state-value" id="odom-theta">—</span></div>
    </div>
    <div class="state-card">
      <h3>Base Mocap</h3>
      <div class="state-row"><span class="state-label">X</span><span class="state-value" id="mocap-x">—</span></div>
      <div class="state-row"><span class="state-label">Y</span><span class="state-value" id="mocap-y">—</span></div>
      <div class="state-row"><span class="state-label">Theta</span><span class="state-value" id="mocap-theta">—</span></div>
    </div>
    <div class="state-card">
      <h3>Base Pose (Active)</h3>
      <div class="state-row"><span class="state-label">X</span><span class="state-value" id="base-x">—</span></div>
      <div class="state-row"><span class="state-label">Y</span><span class="state-value" id="base-y">—</span></div>
      <div class="state-row"><span class="state-label">Theta</span><span class="state-value" id="base-theta">—</span></div>
    </div>
    <div class="state-card">
      <h3>Arm EE (Base Frame)</h3>
      <div class="state-row"><span class="state-label">X</span><span class="state-value" id="ee-x">—</span></div>
      <div class="state-row"><span class="state-label">Y</span><span class="state-value" id="ee-y">—</span></div>
      <div class="state-row"><span class="state-label">Z</span><span class="state-value" id="ee-z">—</span></div>
    </div>
    <div class="state-card">
      <h3>Arm EE (World Frame)</h3>
      <div class="state-row"><span class="state-label">X</span><span class="state-value" id="ee-world-x">—</span></div>
      <div class="state-row"><span class="state-label">Y</span><span class="state-value" id="ee-world-y">—</span></div>
      <div class="state-row"><span class="state-label">Z</span><span class="state-value" id="ee-world-z">—</span></div>
    </div>
    <div class="state-card">
      <h3>Gripper</h3>
      <div class="state-row"><span class="state-label">Width</span><span class="state-value" id="gripper-width">—</span></div>
      <div class="state-row"><span class="state-label">Grasped</span><span class="state-value" id="gripper-grasped">—</span></div>
    </div>
  </div>
</div>

<!-- Row 2: Rewind Logs | Manual Rewind | Reset to Home -->
<div class="control-section">
  <div class="control-grid">
    <div class="control-card">
      <h3>Rewind Logs</h3>
      <div id="rewind-logs" class="log-box" style="height: 125px; font-size: 11px;"></div>
    </div>
    <div class="control-card">
      <h3>Manual Rewind</h3>
      <div class="control-row">
        <span class="control-label">Rewind %</span>
        <input type="number" id="manual-rewind-pct" class="control-input" min="0.1" max="100" step="0.1" value="5" onchange="updateManualRewindPct(this.value)">
      </div>
      <div class="control-row">
        <span class="control-label">Current Status</span>
        <span id="rewind-status" class="state-value">Idle</span>
      </div>
      <button id="btn-manual-rewind" class="btn-action btn-rewind" onclick="triggerManualRewind(this)">
        Rewind
      </button>
    </div>
    <div class="control-card">
      <h3>Reset to Home</h3>
      <div class="control-row">
        <span class="control-label">Rewinds 100%</span>
        <span class="state-value">of trajectory</span>
      </div>
      <div class="control-row">
        <span class="control-label">Current Status</span>
        <span id="reset-status" class="state-value">Idle</span>
      </div>
      <button id="btn-reset-home" class="btn-action btn-home" onclick="resetToHome(this)">
        Reset to Home
      </button>
    </div>
  </div>
</div>

<!-- Row 3: Safety Monitor | Base Trajectory | Lease Queue -->
<div class="info-row">
  <div class="control-card">
    <h3>Safety Monitor <span id="auto-rewind-badge" class="status-badge disabled">Disabled</span></h3>
    <div class="control-row">
      <span class="control-label">Auto-Rewind</span>
      <label class="toggle-switch">
        <input type="checkbox" id="auto-rewind-toggle" onchange="toggleAutoRewind(this.checked)">
        <span class="toggle-slider"></span>
      </label>
    </div>
    <div class="control-row">
      <span class="control-label">Auto-Rewind %</span>
      <input type="number" id="auto-rewind-pct" class="control-input" min="0.1" max="100" step="0.1" value="10" onchange="updateAutoRewindPct(this.value)">
    </div>
    <div class="control-row">
      <span class="control-label">Boundary Status</span>
      <span id="boundary-status" class="boundary-status safe">Safe</span>
    </div>
    <div class="control-row">
      <span class="control-label">Collision Status</span>
      <span id="collision-status" class="boundary-status safe">None</span>
    </div>
    <div class="control-row">
      <span class="control-label">Base Velocity</span>
      <span class="state-value" id="base-velocity">0.000 m/s</span>
    </div>
    <div class="control-row">
      <span class="control-label">Trajectory Length</span>
      <span class="state-value" id="trajectory-length">0</span>
    </div>
    <button class="btn-action" style="background: #666; margin-top: 8px;" onclick="clearTrajectory(this)">
      Clear Trajectory
    </button>
  </div>

  <div class="trajectory-card">
    <h3>Base Trajectory</h3>
    <div class="trajectory-canvas-container">
      <canvas id="trajectory-canvas"></canvas>
    </div>
    <div class="trajectory-legend">
      <div class="legend-item"><div class="legend-dot current" style="border-radius: 3px; width: 10px; height: 14px;"></div>Robot</div>
      <div class="legend-item"><div class="legend-dot odom-path"></div>Odom</div>
      <div class="legend-item"><div class="legend-dot mocap-path"></div>Mocap</div>
      <div class="legend-item"><div class="legend-dot start"></div>Start</div>
      <div class="legend-item"><div class="legend-line boundary"></div>Boundary</div>
    </div>
    <div class="trajectory-info">
      <div>Pts: <span id="traj-points">0</span></div>
      <div>Dur: <span id="traj-duration">0.0s</span></div>
      <div>X: <span id="traj-x">—</span></div>
      <div>Y: <span id="traj-y">—</span></div>
    </div>
  </div>

  <div class="lease-card">
    <h3>Lease Queue</h3>
    <div class="control-row">
      <span class="control-label">Current Holder</span>
      <span id="lease-holder" class="lease-holder none">(none)</span>
    </div>
    <div class="control-row">
      <span class="control-label">Remaining</span>
      <span class="state-value" id="lease-remaining">—</span>
    </div>
    <div class="control-row">
      <span class="control-label">Status</span>
      <span id="lease-status-badge" class="boundary-status safe">Free</span>
    </div>
    <div class="control-row">
      <span class="control-label">Activity</span>
      <span id="robot-activity" class="activity-badge idle">Idle</span>
    </div>
    <div style="margin-top: 12px;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
        <span class="control-label">Queue (<span id="lease-queue-len">0</span> waiting) <span id="queue-paused-badge" class="status-badge" style="display:none; background:#ff9800; color:#000;">Paused</span></span>
        <div style="display: flex; gap: 6px;">
          <button class="btn-action" id="btn-pause-queue" style="background: #ff9800; color: #000; font-size: 11px; padding: 3px 10px;" onclick="togglePauseQueue(this)">Pause Queue</button>
          <button class="btn-action" id="btn-clear-queue" style="background: #b33; font-size: 11px; padding: 3px 10px;" onclick="clearQueue(this)">Stop &amp; Reset</button>
        </div>
      </div>
      <ul class="lease-queue-list" id="lease-queue-list">
        <li style="color: #666; font-style: italic;">Empty</li>
      </ul>
    </div>
  </div>
</div>

<!-- Row 4: Code Execution (3-column: Submitted Code | Output | Status) -->
<div class="control-card" style="margin-bottom: 24px;">
  <h3>Code Execution <span id="code-status-badge" style="font-size: 11px; padding: 2px 8px; border-radius: 4px; display: none;"></span></h3>
  <div id="code-execution-grid" style="max-height: 600px; overflow-y: auto; overflow-x: hidden;"></div>
</div>

<!-- Row 5: Logs (2 columns) -->
<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px;">
  <div class="control-card">
    <h3>Server Logs</h3>
    <div id="server-logs" class="log-box" style="height: 300px; font-size: 11px;"></div>
  </div>
  <div class="control-card">
    <h3>Service Logs</h3>
    <div id="service-logs-combined" class="log-box" style="height: 300px; font-size: 11px;"></div>
  </div>
</div>

<!-- Row 5: Services (start and forget) -->
<table>
  <thead><tr><th>Service</th><th>Status</th><th>PID</th><th>Uptime</th><th>Actions</th></tr></thead>
  <tbody id="tbl"><tr><td colspan="5" style="text-align:center;color:#666">Loading...</td></tr></tbody>
</table>
<!-- Port Reference -->
<table style="margin-top: 16px;">
  <thead><tr><th>Port</th><th>Service</th><th>Protocol</th><th>Bind</th></tr></thead>
  <tbody>
    <tr><td style="font-family: monospace;">8080</td><td>Agent Server</td><td>HTTP / WebSocket</td><td>0.0.0.0</td></tr>
    <tr><td style="font-family: monospace;">50000</td><td>Base Server</td><td>RPC</td><td>localhost</td></tr>
    <tr><td style="font-family: monospace;">5555</td><td>Franka Server (commands)</td><td>ZMQ</td><td>localhost</td></tr>
    <tr><td style="font-family: monospace;">5556</td><td>Franka Server (state)</td><td>ZMQ</td><td>localhost</td></tr>
    <tr><td style="font-family: monospace;">5557</td><td>Franka Server (stream)</td><td>ZMQ</td><td>localhost</td></tr>
    <tr><td style="font-family: monospace;">5570</td><td>Gripper Server (commands)</td><td>ZMQ</td><td>localhost</td></tr>
    <tr><td style="font-family: monospace;">5571</td><td>Gripper Server (state)</td><td>ZMQ</td><td>localhost</td></tr>
    <tr><td style="font-family: monospace;">5580</td><td>Camera Server</td><td>WebSocket</td><td>localhost</td></tr>
    <tr><td style="font-family: monospace;">5581</td><td>Camera Dashboard</td><td>HTTP</td><td>0.0.0.0</td></tr>
    <tr><td style="font-family: monospace;">5590</td><td>Mocap Server</td><td>ZMQ</td><td>localhost</td></tr>
  </tbody>
</table>
<p class="refresh-info">Auto-refreshes every 2 seconds</p>
<script>
let serviceManagerEnabled = true;  // Replaced by server when disabled
let serviceKeys = [];
function fmt(s) {
  if (s == null) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return h + "h " + m + "m";
  return m + "m " + sec + "s";
}

async function act(method, url, btn) {
  if (btn) btn.disabled = true;
  try {
    await fetch(url, { method });
    await poll();
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function poll() {
  // Skip service polling if service manager is disabled
  if (!serviceManagerEnabled) {
    // Hide service table when service manager is disabled
    const tbl = document.querySelector("table");
    if (tbl) tbl.style.display = "none";
    // Update subtitle
    const subtitle = document.querySelector(".subtitle");
    if (subtitle) subtitle.textContent = "TidyBot Agent Server — Service Manager Disabled";
    return;
  }

  try {
    const data = await (await fetch("/services")).json();
    let rows = "";
    let isDryRun = false;
    const newKeys = [];

    for (const s of data) {
      newKeys.push(s.key);
      const on = s.running;
      if (s.dry_run) isDryRun = true;

      // Special handling for unlock service - single toggle button
      let actionBtns;
      if (s.key === "unlock") {
        actionBtns = on
          ? `<button class="btn-stop" onclick="act('POST','/services/unlock/lock',this)">Lock</button>`
          : `<button class="btn-start" onclick="act('POST','/services/unlock/start',this)">Unlock</button>`;
      } else {
        actionBtns = on
          ? `<button class="btn-stop" onclick="act('POST','/services/${s.key}/stop',this)">Stop</button>
             <button class="btn-restart" onclick="act('POST','/services/${s.key}/restart',this)">Restart</button>`
          : `<button class="btn-start" onclick="act('POST','/services/${s.key}/start',this)">Start</button>`;
      }

      rows += `<tr>
        <td><span class="dot ${on ? "on" : "off"}"></span>${s.name}</td>
        <td><span class="status-text ${on ? "on" : "off"}">${on ? (s.key === "unlock" ? "Unlocked" : "Running") : (s.key === "unlock" ? "Locked" : "Stopped")}</span></td>
        <td>${s.pid || "—"}</td>
        <td class="uptime">${fmt(s.uptime)}</td>
        <td class="actions">${actionBtns}</td></tr>`;
    }

    document.getElementById("tbl").innerHTML = rows;
    document.getElementById("dry-run-badge").style.display = isDryRun ? "inline" : "none";

    serviceKeys = newKeys;

    // Combined service logs
    await pollServiceLogs(data);
  } catch (e) {
    console.error("Poll error:", e);
  }
}

async function pollState() {
  try {
    const resp = await fetch("/state");
    const state = await resp.json();

    // Base state
    const base = state.base || {};

    // Raw odometry
    const odom = base.odom_pose || [0, 0, 0];
    document.getElementById("odom-x").textContent = odom[0].toFixed(3) + " m";
    document.getElementById("odom-y").textContent = odom[1].toFixed(3) + " m";
    document.getElementById("odom-theta").textContent = (odom[2] * 180 / Math.PI).toFixed(1) + "°";

    // Raw mocap
    const mocap = base.mocap_pose;
    const mocapTracking = base.mocap_tracking || false;
    if (mocap && mocapTracking) {
      document.getElementById("mocap-x").textContent = mocap[0].toFixed(3) + " m";
      document.getElementById("mocap-y").textContent = mocap[1].toFixed(3) + " m";
      document.getElementById("mocap-theta").textContent = (mocap[2] * 180 / Math.PI).toFixed(1) + "°";
      document.getElementById("mocap-x").classList.remove("disconnected");
      document.getElementById("mocap-y").classList.remove("disconnected");
      document.getElementById("mocap-theta").classList.remove("disconnected");
    } else {
      document.getElementById("mocap-x").textContent = "—";
      document.getElementById("mocap-y").textContent = "—";
      document.getElementById("mocap-theta").textContent = "—";
      document.getElementById("mocap-x").classList.add("disconnected");
      document.getElementById("mocap-y").classList.add("disconnected");
      document.getElementById("mocap-theta").classList.add("disconnected");
    }

    // Active pose (selected source)
    const pose = base.pose || [0, 0, 0];
    document.getElementById("base-x").textContent = pose[0].toFixed(3) + " m";
    document.getElementById("base-y").textContent = pose[1].toFixed(3) + " m";
    document.getElementById("base-theta").textContent = (pose[2] * 180 / Math.PI).toFixed(1) + "°";

    // Base velocity
    const vel = base.velocity || [0, 0, 0];
    const speed = Math.sqrt(vel[0] * vel[0] + vel[1] * vel[1]);
    document.getElementById("base-velocity").textContent = speed.toFixed(3) + " m/s";

    // Arm EE pose (4x4 matrix stored column-major, position is indices 12,13,14)
    const arm = state.arm || {};
    const ee = arm.ee_pose || [];
    if (ee.length >= 15) {
      document.getElementById("ee-x").textContent = ee[12].toFixed(3) + " m";
      document.getElementById("ee-y").textContent = ee[13].toFixed(3) + " m";
      document.getElementById("ee-z").textContent = ee[14].toFixed(3) + " m";
      document.getElementById("ee-x").classList.remove("disconnected");
      document.getElementById("ee-y").classList.remove("disconnected");
      document.getElementById("ee-z").classList.remove("disconnected");
    } else {
      document.getElementById("ee-x").textContent = "—";
      document.getElementById("ee-y").textContent = "—";
      document.getElementById("ee-z").textContent = "—";
      document.getElementById("ee-x").classList.add("disconnected");
      document.getElementById("ee-y").classList.add("disconnected");
      document.getElementById("ee-z").classList.add("disconnected");
    }

    // Arm EE pose in world frame
    const eeWorld = arm.ee_pose_world || [];
    if (eeWorld.length >= 15) {
      document.getElementById("ee-world-x").textContent = eeWorld[12].toFixed(3) + " m";
      document.getElementById("ee-world-y").textContent = eeWorld[13].toFixed(3) + " m";
      document.getElementById("ee-world-z").textContent = eeWorld[14].toFixed(3) + " m";
      document.getElementById("ee-world-x").classList.remove("disconnected");
      document.getElementById("ee-world-y").classList.remove("disconnected");
      document.getElementById("ee-world-z").classList.remove("disconnected");
    } else {
      document.getElementById("ee-world-x").textContent = "—";
      document.getElementById("ee-world-y").textContent = "—";
      document.getElementById("ee-world-z").textContent = "—";
      document.getElementById("ee-world-x").classList.add("disconnected");
      document.getElementById("ee-world-y").classList.add("disconnected");
      document.getElementById("ee-world-z").classList.add("disconnected");
    }

    // Gripper
    const gripper = state.gripper || {};
    document.getElementById("gripper-width").textContent = ((gripper.width || 0) * 1000).toFixed(1) + " mm";
    document.getElementById("gripper-grasped").textContent = gripper.is_grasped ? "Yes" : "No";

  } catch (e) {
    console.error("State poll error:", e);
  }
}

// Acquire a lease, use it, then release it when done
async function acquireLease() {
  try {
    const resp = await fetch("/lease/acquire", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ holder: "dashboard" })
    });
    const data = await resp.json();
    return data.lease_id || null;
  } catch (e) {
    console.error("Failed to acquire lease:", e);
    return null;
  }
}

async function releaseLease(id) {
  if (!id) return;
  try {
    await fetch("/lease/release", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lease_id: id })
    });
  } catch (e) {
    console.error("Failed to release lease:", e);
  }
}

async function toggleAutoRewind(enabled) {
  try {
    const endpoint = enabled ? "/rewind/monitor/enable" : "/rewind/monitor/disable";
    await fetch(endpoint, { method: "POST" });
    await pollRewind();
  } catch (e) {
    console.error("Failed to toggle auto-rewind:", e);
  }
}

async function updateAutoRewindPct(pct) {
  try {
    await fetch("/rewind/monitor/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto_rewind_percentage: parseFloat(pct) })
    });
  } catch (e) {
    console.error("Failed to update auto-rewind percentage:", e);
  }
}

async function updateManualRewindPct(pct) {
  try {
    await fetch("/rewind/monitor/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ manual_rewind_percentage: parseFloat(pct) })
    });
  } catch (e) {
    console.error("Failed to update manual-rewind percentage:", e);
  }
}

async function clearTrajectory(btn) {
  if (!confirm("Clear all trajectory waypoints?")) return;
  btn.disabled = true;
  try {
    const resp = await fetch("/rewind/trajectory/clear", { method: "POST" });
    const result = await resp.json();
    if (result.success) {
      document.getElementById("trajectory-length").textContent = "0";
      await pollRewind();
      await pollTrajectory();
    } else {
      alert("Failed to clear trajectory");
    }
  } catch (e) {
    console.error("Failed to clear trajectory:", e);
    alert("Error: " + e.message);
  } finally {
    btn.disabled = false;
  }
}

async function clearQueue(btn) {
  if (!confirm("Stop current code, clear queue, and rewind to home?")) return;
  btn.disabled = true;
  try {
    const resp = await fetch("/lease/clear-queue", { method: "POST" });
    const result = await resp.json();
    if (result.status === "cleared") {
      await pollLease();
    } else {
      alert("Failed: " + JSON.stringify(result));
    }
  } catch (e) {
    console.error("Failed to clear queue:", e);
    alert("Error: " + e.message);
  } finally {
    btn.disabled = false;
  }
}

let queuePaused = false;
async function togglePauseQueue(btn) {
  btn.disabled = true;
  try {
    const endpoint = queuePaused ? "/lease/resume-queue" : "/lease/pause-queue";
    await fetch(endpoint, { method: "POST" });
    await pollLease();
  } catch (e) {
    console.error("Failed to toggle pause queue:", e);
  } finally {
    btn.disabled = false;
  }
}

async function triggerManualRewind(btn) {
  btn.disabled = true;
  let lease = null;
  try {
    lease = await acquireLease();
    if (!lease) {
      alert("Failed to acquire lease for rewind");
      return;
    }
    const resp = await fetch("/rewind/manual", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Lease-Id": lease
      },
      body: JSON.stringify({ dry_run: false })
    });
    if (resp.status === 403) {
      lease = null;
      alert("Lease expired. Please try again.");
      return;
    }
    const result = await resp.json();
    if (!result.success && result.error) {
      alert("Rewind failed: " + result.error);
    }
    await pollRewind();
  } catch (e) {
    console.error("Failed to trigger manual rewind:", e);
    alert("Error: " + e.message);
  } finally {
    await releaseLease(lease);
    btn.disabled = false;
  }
}

async function resetToHome(btn) {
  btn.disabled = true;
  let lease = null;
  try {
    lease = await acquireLease();
    if (!lease) {
      alert("Failed to acquire lease");
      return;
    }
    const resp = await fetch("/rewind/reset-to-home", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Lease-Id": lease
      },
      body: JSON.stringify({ dry_run: false })
    });
    if (resp.status === 403) {
      lease = null;
      alert("Lease expired. Please try again.");
      return;
    }
    const result = await resp.json();
    if (!result.success && result.error) {
      alert("Reset failed: " + result.error);
    }
    await pollRewind();
  } catch (e) {
    console.error("Failed to reset to home:", e);
    alert("Error: " + e.message);
  } finally {
    await releaseLease(lease);
    btn.disabled = false;
  }
}

async function pollRewind() {
  try {
    // Get rewind status
    const statusResp = await fetch("/rewind/status");
    const status = await statusResp.json();

    // Update trajectory length
    document.getElementById("trajectory-length").textContent = status.trajectory_length || 0;

    // Update rewind status
    const rewindStatusEl = document.getElementById("rewind-status");
    const resetStatusEl = document.getElementById("reset-status");
    if (status.is_rewinding) {
      rewindStatusEl.textContent = "Rewinding...";
      rewindStatusEl.style.color = "#ff9800";
      resetStatusEl.textContent = "Rewinding...";
      resetStatusEl.style.color = "#9c27b0";
    } else {
      rewindStatusEl.textContent = "Idle";
      rewindStatusEl.style.color = "#4caf50";
      resetStatusEl.textContent = "Idle";
      resetStatusEl.style.color = "#4caf50";
    }

    // Update boundary status
    const boundaryEl = document.getElementById("boundary-status");
    const boundaryStatus = status.base_boundary_status || {};
    if (boundaryStatus.out_of_bounds) {
      boundaryEl.textContent = "OUT OF BOUNDS";
      boundaryEl.className = "boundary-status warning";
    } else {
      boundaryEl.textContent = "Safe";
      boundaryEl.className = "boundary-status safe";
    }

    // Update collision status
    const collisionEl = document.getElementById("collision-status");
    if (status.collision_detected) {
      collisionEl.textContent = "COLLISION";
      collisionEl.className = "boundary-status warning";
    } else {
      collisionEl.textContent = "None";
      collisionEl.className = "boundary-status safe";
    }

    // Get monitor status
    const monitorResp = await fetch("/rewind/monitor/status");
    const monitor = await monitorResp.json();

    // Update auto-rewind toggle
    const toggle = document.getElementById("auto-rewind-toggle");
    if (toggle && toggle !== document.activeElement) {
      toggle.checked = monitor.auto_rewind_enabled;
    }

    // Update badge
    const badge = document.getElementById("auto-rewind-badge");
    if (monitor.auto_rewind_enabled) {
      badge.textContent = "Enabled";
      badge.className = "status-badge enabled";
    } else {
      badge.textContent = "Disabled";
      badge.className = "status-badge disabled";
    }

    // Update percentage inputs (only if not focused)
    const autoPctEl = document.getElementById("auto-rewind-pct");
    if (autoPctEl && autoPctEl !== document.activeElement) {
      autoPctEl.value = monitor.auto_rewind_percentage || 10;
    }
    const manualPctEl = document.getElementById("manual-rewind-pct");
    if (manualPctEl && manualPctEl !== document.activeElement) {
      manualPctEl.value = monitor.manual_rewind_percentage || 5;
    }

  } catch (e) {
    console.error("Rewind poll error:", e);
  }
}

// Trajectory visualization
let trajectoryCanvas = null;
let trajectoryCtx = null;
let workspaceBounds = { x_min: -5, x_max: 5, y_min: -5, y_max: 5 };

function initTrajectoryCanvas() {
  trajectoryCanvas = document.getElementById("trajectory-canvas");
  if (!trajectoryCanvas) return;

  // Set actual pixel size for sharp rendering
  const container = trajectoryCanvas.parentElement;
  const size = Math.min(container.clientWidth, container.clientHeight) || 400;
  trajectoryCanvas.width = size;
  trajectoryCanvas.height = size;
  trajectoryCtx = trajectoryCanvas.getContext("2d");
}

function worldToCanvas(x, y) {
  // Map world coordinates to canvas: "behind the robot" view
  // World +X (forward) → screen up, World +Y (left) → screen left
  const padding = 1.0; // 1 meter padding
  const xMin = workspaceBounds.x_min - padding;
  const xMax = workspaceBounds.x_max + padding;
  const yMin = workspaceBounds.y_min - padding;
  const yMax = workspaceBounds.y_max + padding;

  const xRange = xMax - xMin;
  const yRange = yMax - yMin;
  // World X range maps to canvas height, world Y range maps to canvas width
  const scale = Math.min(trajectoryCanvas.width / yRange, trajectoryCanvas.height / xRange);

  const cx = trajectoryCanvas.width - (y - yMin) * scale;   // +Y → left on screen
  const cy = trajectoryCanvas.height - (x - xMin) * scale;  // +X → up on screen
  return { x: cx, y: cy };
}

function drawTrajectory(waypoints, currentPose) {
  if (!trajectoryCtx || !trajectoryCanvas) {
    initTrajectoryCanvas();
    if (!trajectoryCtx) return;
  }

  const ctx = trajectoryCtx;
  const w = trajectoryCanvas.width;
  const h = trajectoryCanvas.height;

  // Clear canvas
  ctx.fillStyle = "#0d1117";
  ctx.fillRect(0, 0, w, h);

  // Draw grid
  ctx.strokeStyle = "#1a2744";
  ctx.lineWidth = 1;
  const padding = 1.0;
  const xMin = workspaceBounds.x_min - padding;
  const xMax = workspaceBounds.x_max + padding;
  const yMin = workspaceBounds.y_min - padding;
  const yMax = workspaceBounds.y_max + padding;

  // Draw 1m grid lines
  // World X maps to canvas Y → horizontal grid lines
  for (let x = Math.ceil(xMin); x <= Math.floor(xMax); x++) {
    const p = worldToCanvas(x, 0);
    ctx.beginPath();
    ctx.moveTo(0, p.y);
    ctx.lineTo(w, p.y);
    ctx.stroke();
  }
  // World Y maps to canvas X → vertical grid lines
  for (let y = Math.ceil(yMin); y <= Math.floor(yMax); y++) {
    const p = worldToCanvas(0, y);
    ctx.beginPath();
    ctx.moveTo(p.x, 0);
    ctx.lineTo(p.x, h);
    ctx.stroke();
  }

  // Draw origin axes
  ctx.strokeStyle = "#30363d";
  ctx.lineWidth = 2;
  const origin = worldToCanvas(0, 0);
  ctx.beginPath();
  ctx.moveTo(0, origin.y);
  ctx.lineTo(w, origin.y);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(origin.x, 0);
  ctx.lineTo(origin.x, h);
  ctx.stroke();

  // Draw workspace boundary
  ctx.strokeStyle = "#f44336";
  ctx.lineWidth = 2;
  ctx.setLineDash([8, 4]);
  const c1 = worldToCanvas(workspaceBounds.x_min, workspaceBounds.y_min);
  const c2 = worldToCanvas(workspaceBounds.x_max, workspaceBounds.y_max);
  const rx = Math.min(c1.x, c2.x);
  const ry = Math.min(c1.y, c2.y);
  ctx.strokeRect(rx, ry, Math.abs(c2.x - c1.x), Math.abs(c2.y - c1.y));
  ctx.setLineDash([]);

  // Draw odom trajectory path (lighter blue)
  if (waypoints && waypoints.length > 1) {
    let hasOdom = false;
    ctx.strokeStyle = "#64b5f6";
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (let i = 0; i < waypoints.length; i++) {
      const pose = waypoints[i].odom_pose;
      if (!pose) continue;
      const p = worldToCanvas(pose[0], pose[1]);
      if (!hasOdom) { ctx.moveTo(p.x, p.y); hasOdom = true; }
      else ctx.lineTo(p.x, p.y);
    }
    if (hasOdom) ctx.stroke();

    // Draw mocap trajectory path (darker blue)
    let hasMocap = false;
    ctx.strokeStyle = "#0d47a1";
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (let i = 0; i < waypoints.length; i++) {
      const pose = waypoints[i].mocap_pose;
      if (!pose) continue;
      const p = worldToCanvas(pose[0], pose[1]);
      if (!hasMocap) { ctx.moveTo(p.x, p.y); hasMocap = true; }
      else ctx.lineTo(p.x, p.y);
    }
    if (hasMocap) ctx.stroke();

    // Draw start position
    if (waypoints.length > 0) {
      const startPose = waypoints[0].base_pose || [0, 0, 0];
      const startP = worldToCanvas(startPose[0], startPose[1]);
      ctx.fillStyle = "#9c27b0";
      ctx.beginPath();
      ctx.arc(startP.x, startP.y, 6, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // Draw robot icon at current position
  if (currentPose && currentPose.length >= 2) {
    const p = worldToCanvas(currentPose[0], currentPose[1]);
    const theta = currentPose[2] || 0;

    // "Behind the robot" view: world +X → screen up, world +Y → screen left
    // Canvas rotation angle: -theta maps world heading to screen
    const canvasAngle = -theta;

    ctx.save();
    ctx.translate(p.x, p.y);
    ctx.rotate(canvasAngle);

    // Body: rounded rectangle (forward = up in local frame = -Y on canvas)
    const bw = 10, bh = 16; // half-widths
    ctx.fillStyle = "#4caf50";
    ctx.shadowColor = "#4caf50";
    ctx.shadowBlur = 10;
    ctx.beginPath();
    ctx.roundRect(-bw, -bh, bw * 2, bh * 2, 3);
    ctx.fill();
    ctx.shadowBlur = 0;

    // Wheels: two small rects on each side
    ctx.fillStyle = "#2e7d32";
    ctx.fillRect(-bw - 3, -bh + 3, 3, 8);  // left-front
    ctx.fillRect(-bw - 3, bh - 11, 3, 8);   // left-rear
    ctx.fillRect(bw, -bh + 3, 3, 8);         // right-front
    ctx.fillRect(bw, bh - 11, 3, 8);          // right-rear

    // Forward arrow inside body
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(0, 6);
    ctx.lineTo(0, -8);
    ctx.lineTo(-4, -4);
    ctx.moveTo(0, -8);
    ctx.lineTo(4, -4);
    ctx.stroke();

    ctx.restore();
  }

  // Draw axis labels (behind-robot view: X up, Y left)
  ctx.fillStyle = "#666";
  ctx.font = "11px sans-serif";
  ctx.fillText("X (fwd)", origin.x + 5, 15);
  ctx.fillText("Y (left)", 5, origin.y - 5);
}

async function pollTrajectory() {
  try {
    // Get trajectory data
    const trajResp = await fetch("/trajectory");
    const trajData = await trajResp.json();
    const waypoints = trajData.waypoints || [];

    // Get workspace bounds from rewind status
    const statusResp = await fetch("/rewind/status");
    const status = await statusResp.json();
    const boundary = status.base_boundary_status || {};

    if (boundary.bounds) {
      workspaceBounds = {
        x_min: boundary.bounds.x_min,
        x_max: boundary.bounds.x_max,
        y_min: boundary.bounds.y_min,
        y_max: boundary.bounds.y_max
      };
    }

    // Get current pose from state
    const stateResp = await fetch("/state");
    const state = await stateResp.json();
    const currentPose = state.base?.pose || [0, 0, 0];

    // Update info display
    document.getElementById("traj-points").textContent = waypoints.length;

    // Calculate duration
    if (waypoints.length > 1) {
      const duration = waypoints[waypoints.length - 1].t - waypoints[0].t;
      document.getElementById("traj-duration").textContent = duration.toFixed(1) + "s";
    } else {
      document.getElementById("traj-duration").textContent = "0.0s";
    }

    document.getElementById("traj-x").textContent = currentPose[0].toFixed(3) + " m";
    document.getElementById("traj-y").textContent = currentPose[1].toFixed(3) + " m";

    // Draw trajectory
    drawTrajectory(waypoints, currentPose);

  } catch (e) {
    console.error("Trajectory poll error:", e);
  }
}

// Initialize canvas on load
window.addEventListener("load", initTrajectoryCanvas);
window.addEventListener("resize", initTrajectoryCanvas);

// Code execution polling — 3-column layout: Status | Output | Code
// Track which rows are expanded (persists across re-renders)
let _expandedRows = new Set();

function escHtml(s) { return s ? s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") : ""; }

// Lightweight Python syntax highlighter — single-pass tokenizer, VS Code dark colors
// Note: triple-quoted strings not handled (would conflict with Python raw string delimiters)
function highlightPy(raw) {
  if (!raw) return "";
  const e = s => s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  const re = /(f?"(?:[^"\\\n]|\\.)*"|f?'(?:[^'\\\n]|\\.)*'|#[^\n]*|@\w+|\b[a-zA-Z_]\w*\b|\b\d[\d_.]*(?:e[+-]?\d+)?\b)/g;
  const kw = new Set("def,class,return,if,elif,else,for,while,in,not,and,or,is,with,as,try,except,finally,raise,import,from,pass,break,continue,yield,lambda,async,await,None,True,False,global,nonlocal,del,assert".split(","));
  const bi = new Set("print,range,len,list,dict,tuple,set,int,float,str,bool,type,isinstance,enumerate,zip,map,filter,sorted,reversed,sum,min,max,abs,round,open,super,format,input,hex,oct,bin,chr,ord,repr,hash,id,dir,vars,next,iter,any,all,math,time,random".split(","));
  let out = "", last = 0, m;
  while ((m = re.exec(raw)) !== null) {
    out += e(raw.slice(last, m.index));
    last = m.index + m[0].length;
    const t = m[0], et = e(t);
    if (t[0]==="#") out += `<span style="color:#6a9955">${et}</span>`;
    else if (t[0]==='"'||t[0]==="'"||t.startsWith("f'")||t.startsWith('f"')) out += `<span style="color:#ce9178">${et}</span>`;
    else if (t[0]==="@") out += `<span style="color:#dcdcaa">${et}</span>`;
    else if (kw.has(t)) out += `<span style="color:#c586c0">${et}</span>`;
    else if (bi.has(t)) out += `<span style="color:#dcdcaa">${et}</span>`;
    else if (/^\d/.test(t)) out += `<span style="color:#b5cea8">${et}</span>`;
    else out += et;
  }
  out += e(raw.slice(last));
  return out;
}

function toggleExecRow(execId) {
  if (_expandedRows.has(execId)) _expandedRows.delete(execId);
  else _expandedRows.add(execId);
  // Re-render immediately so it feels instant
  pollCodeLogs();
}

async function pollCodeLogs() {
  try {
    const gridEl = document.getElementById("code-execution-grid");
    if (!gridEl) return;

    const [statusResp, histResp] = await Promise.all([
      fetch("/code/status"),
      fetch("/code/history?count=10")
    ]);
    const status = await statusResp.json();
    const histData = await histResp.json();
    const history = histData.history || [];

    // Update header badge
    const badgeEl = document.getElementById("code-status-badge");
    if (badgeEl) {
      if (status.is_running) {
        badgeEl.style.display = "inline";
        badgeEl.style.background = "#42a5f5";
        badgeEl.style.color = "#fff";
        badgeEl.style.animation = "pulse 1s infinite";
        badgeEl.textContent = "RUNNING";
      } else if (status.code) {
        badgeEl.style.display = "inline";
        badgeEl.style.animation = "none";
        const s = status.status;
        badgeEl.style.background = s === "completed" ? "#4caf50" : s === "failed" || s === "timeout" ? "#f85149" : s === "stopped" ? "#ff9800" : "#666";
        badgeEl.style.color = "#fff";
        badgeEl.textContent = s.toUpperCase();
      } else {
        badgeEl.style.display = "none";
      }
    }

    if (history.length === 0 && !status.is_running) {
      gridEl.innerHTML = '<span style="color: #666; font-size: 12px;">No code executed yet...</span>';
      return;
    }

    let html = "";

    // Column headers: Status | Output | Code
    html += `<div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 6px; padding: 0 4px;">
      <div style="color: #666; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;">Execution Info</div>
      <div style="color: #666; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;">Output</div>
      <div style="color: #666; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;">Submitted Code</div>
    </div>`;

    // Running execution row (always expanded)
    if (status.is_running) {
      const dur = status.duration?.toFixed(1) || "0";
      let codeHtml = '<span style="color: #666; font-style: italic; font-size: 11px;">No code</span>';
      if (status.code) {
        codeHtml = `<pre style="margin:0; font-family: 'SF Mono', Monaco, monospace; font-size: 11px; line-height: 1.5; color: #c9d1d9; white-space: pre-wrap; word-break: break-all;">${highlightPy(status.code)}</pre>`;
      }
      let outHtml = "";
      if (status.stdout) {
        const lines = status.stdout.trim().split("\n").filter(l => !l.startsWith("[SDK]") && l.trim());
        for (const line of lines.slice(-12)) {
          outHtml += `<div style="color: #8b949e; white-space: pre-wrap; word-break: break-all;">${escHtml(line)}</div>`;
        }
      }
      if (status.stderr) {
        const errLines = status.stderr.trim().split("\n").filter(l => l.trim()).slice(-3);
        for (const line of errLines) {
          outHtml += `<div style="color: #f85149; white-space: pre-wrap; word-break: break-all;">${escHtml(line)}</div>`;
        }
      }
      if (!outHtml) outHtml = '<span style="color: #666; font-style: italic; font-size: 11px;">Waiting for output...</span>';

      html += `<div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; border: 1px solid #42a5f5; border-radius: 6px; padding: 8px; margin-bottom: 6px; background: #1a2332; min-width: 0;">
        <div style="min-width: 0; font-size: 12px;">
          <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px;">
            <span style="color: #42a5f5; font-weight: 700; font-size: 12px; padding: 1px 6px; background: #42a5f522; border-radius: 3px; animation: pulse 1s infinite;">RUNNING</span>
          </div>
          <div style="color: #888; font-size: 11px;">${dur}s elapsed</div>
          <div style="color: #666; font-size: 10px; font-family: monospace; margin-top: 2px;">${status.execution_id || "..."}</div>
        </div>
        <div data-running-output data-scroll-key="running-out" style="min-width: 0; overflow-y: auto; max-height: 400px; font-family: monospace; font-size: 11px; line-height: 1.5;">${outHtml}</div>
        <div data-scroll-key="running-code" style="min-width: 0; overflow-y: auto; max-height: 400px; font-size: 11px;">${codeHtml}</div>
      </div>`;
    }

    // History rows
    for (const r of history) {
      const ok = r.status === "completed";
      const isStopped = r.status === "stopped";
      const isFailed = r.status === "failed";
      const isTimeout = r.status === "timeout";
      const borderColor = ok ? "#2d5a2d" : isFailed ? "#5a2d2d" : isStopped ? "#5a3a2d" : isTimeout ? "#5a2d2d" : "#5a4a2d";
      const statusColor = ok ? "#4caf50" : isFailed ? "#f85149" : isStopped ? "#ff9800" : isTimeout ? "#f85149" : "#d29922";

      let statusLabel = ok ? "OK" : r.status.toUpperCase();
      if (isStopped && r.stop_reason) {
        const reasonLabels = { "manual": "STOPPED", "arm_error": "ARM ERROR", "idle_timeout": "IDLE TIMEOUT", "max_duration": "MAX DURATION", "queue_cleared": "QUEUE CLEARED", "released": "RELEASED" };
        statusLabel = reasonLabels[r.stop_reason] || r.stop_reason.toUpperCase();
      }

      const holder = r.holder || "unknown";
      const clientHost = r.client_host || "";
      const dur = r.duration?.toFixed(1) || "0";
      const execId = r.execution_id || "";
      const expanded = _expandedRows.has(execId);

      let timeStr = "";
      if (r.started_at && r.started_at > 0) {
        const d = new Date(r.started_at * 1000);
        timeStr = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      }

      // Parse output lines (split by real newline, not literal \n)
      let outputLines = [];
      let errLines = [];
      if (r.stdout) outputLines = r.stdout.trim().split("\n").filter(l => !l.startsWith("[SDK]") && l.trim());
      if (r.stderr) errLines = r.stderr.trim().split("\n").filter(l => l.trim());

      // --- Collapsed: compact 1-line summary (Status | Output | Code ▼) ---
      if (!expanded) {
        // Output preview: last meaningful line
        let outPrev = '<span style="color: #666; font-style: italic;">—</span>';
        if (errLines.length > 0) {
          const lastErr = errLines[errLines.length - 1];
          outPrev = `<span style="color: #f85149;">${escHtml(lastErr.length > 80 ? lastErr.slice(0, 80) + "..." : lastErr)}</span>`;
        } else if (outputLines.length > 0) {
          const lastOut = outputLines[outputLines.length - 1];
          outPrev = `<span style="color: #8b949e;">${escHtml(lastOut.length > 80 ? lastOut.slice(0, 80) + "..." : lastOut)}</span>`;
        }

        // Code preview: first line or truncated
        let codePrev = '<span style="color: #666; font-style: italic;">—</span>';
        if (r.code) {
          const firstLines = r.code.split("\n").filter(l => l.trim()).slice(0, 2).join("; ");
          codePrev = `<span style="color: #c9d1d9; font-family: 'SF Mono', Monaco, monospace;">${escHtml(firstLines.length > 60 ? firstLines.slice(0, 60) + "..." : firstLines)}</span>`;
        }

        html += `<div onclick="toggleExecRow('${execId}')" style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; border: 1px solid ${borderColor}; border-radius: 6px; padding: 6px 8px; margin-bottom: 4px; background: #161b22; min-width: 0; cursor: pointer; font-size: 11px; align-items: center;" title="Click to expand">
          <div style="min-width: 0; display: flex; align-items: center; gap: 6px; flex-wrap: nowrap;">
            <span style="color: #58a6ff; font-size: 10px;">&#9660;</span>
            <span style="color: ${statusColor}; font-weight: 700; padding: 1px 5px; background: ${statusColor}22; border-radius: 3px; font-size: 11px;">${statusLabel}</span>
            <span style="color: #888;">${escHtml(holder)}</span>
            <span style="color: #666; font-size: 10px;">${dur}s</span>
            ${timeStr ? `<span style="color: #555; font-size: 10px;">${timeStr}</span>` : ""}
          </div>
          <div style="min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: monospace;">${outPrev}</div>
          <div style="min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${codePrev}</div>
        </div>`;
        continue;
      }

      // --- Expanded: full 3-column view (▲ Status | Output | Code) ---
      // Info column
      let infoCol = `<div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px; flex-wrap: wrap;">
        <span style="color: ${statusColor}; font-weight: 700; font-size: 12px; padding: 1px 6px; background: ${statusColor}22; border-radius: 3px;">${statusLabel}</span>
        <span style="color: #e6edf3; font-weight: 600; font-size: 12px;">${escHtml(holder)}</span>
      </div>`;
      if (r.error && !ok) {
        infoCol += `<div style="color: ${statusColor}; font-size: 10px; margin-bottom: 3px; padding: 2px 4px; background: ${statusColor}11; border-radius: 3px; word-break: break-word;">${escHtml(r.error)}</div>`;
      }
      infoCol += `<div style="color: #888; font-size: 11px;">${dur}s${timeStr ? " &middot; " + timeStr : ""}</div>`;
      if (clientHost) infoCol += `<div style="color: #555; font-size: 10px;">${escHtml(clientHost)}</div>`;
      infoCol += `<div style="color: #666; font-size: 10px; font-family: monospace; margin-top: 2px;">${execId}</div>`;

      // Output column
      let outCol = "";
      if (errLines.length > 0) {
        outCol += `<div style="color: #666; font-size: 10px; text-transform: uppercase; margin-bottom: 2px;">stderr:</div>`;
        for (const line of errLines) {
          outCol += `<div style="color: #f85149; white-space: pre-wrap; word-break: break-all;">${escHtml(line)}</div>`;
        }
      }
      if (outputLines.length > 0) {
        if (errLines.length > 0) outCol += `<div style="border-top: 1px solid #30363d; margin: 4px 0;"></div>`;
        outCol += `<div style="color: #666; font-size: 10px; text-transform: uppercase; margin-bottom: 2px;">stdout:</div>`;
        for (const line of outputLines) {
          outCol += `<div style="color: #8b949e; white-space: pre-wrap; word-break: break-all;">${escHtml(line)}</div>`;
        }
      }
      if (!outCol) outCol = '<span style="color: #666; font-style: italic; font-size: 11px;">No output</span>';

      // Code column
      let codeCol = '<span style="color: #666; font-style: italic; font-size: 11px;">No code captured</span>';
      if (r.code) {
        codeCol = `<pre style="margin:0; font-family: 'SF Mono', Monaco, monospace; font-size: 11px; line-height: 1.5; color: #c9d1d9; white-space: pre-wrap; word-break: break-all;">${highlightPy(r.code)}</pre>`;
      }

      html += `<div style="border: 1px solid ${borderColor}; border-radius: 6px; margin-bottom: 4px; background: #161b22; min-width: 0;">
        <div onclick="toggleExecRow('${execId}')" style="padding: 6px 8px; cursor: pointer; display: flex; align-items: center; gap: 8px; border-bottom: 1px solid ${borderColor};">
          <span style="color: #58a6ff; font-size: 10px;">&#9650;</span>
          <span style="color: ${statusColor}; font-weight: 700; font-size: 11px; padding: 1px 5px; background: ${statusColor}22; border-radius: 3px;">${statusLabel}</span>
          <span style="color: #888; font-size: 11px;">${escHtml(holder)}</span>
          <span style="color: #666; font-size: 10px;">${dur}s</span>
          ${timeStr ? `<span style="color: #555; font-size: 10px;">${timeStr}</span>` : ""}
          <span style="color: #555; font-size: 10px; font-family: monospace; margin-left: auto;">${execId}</span>
        </div>
        <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; padding: 8px;">
          <div style="min-width: 0; font-size: 12px;">${infoCol}</div>
          <div data-scroll-key="${execId}-out" style="min-width: 0; font-family: monospace; font-size: 11px; line-height: 1.5; overflow-y: auto; max-height: 500px;">${outCol}</div>
          <div data-scroll-key="${execId}-code" style="min-width: 0; font-size: 11px; overflow-y: auto; max-height: 500px;">${codeCol}</div>
        </div>
      </div>`;
    }

    // Save scroll positions of all scrollable elements before innerHTML wipe
    const scrollState = {};
    gridEl.querySelectorAll('[data-scroll-key]').forEach(el => {
      if (el.scrollTop > 0) scrollState[el.dataset.scrollKey] = el.scrollTop;
    });
    const prevGridScroll = gridEl.scrollTop;

    gridEl.innerHTML = html;

    // Restore grid container scroll
    gridEl.scrollTop = prevGridScroll;
    // Restore inner scrollable elements
    gridEl.querySelectorAll('[data-scroll-key]').forEach(el => {
      const saved = scrollState[el.dataset.scrollKey];
      if (saved) el.scrollTop = saved;
    });
    // Auto-scroll running execution output to bottom (follow live output)
    const runningOut = gridEl.querySelector('[data-running-output]');
    if (runningOut) runningOut.scrollTop = runningOut.scrollHeight;
  } catch (e) {
    console.error("Code history poll error:", e);
  }
}

// Combined service logs polling
async function pollServiceLogs(servicesData) {
  try {
    const el = document.getElementById("service-logs-combined");
    if (!el) return;

    // If no data passed, fetch from services (or skip if disabled)
    let data = servicesData;
    if (!data && serviceManagerEnabled) {
      const resp = await fetch("/services");
      data = await resp.json();
    }
    if (!data || !data.length) {
      el.innerHTML = '<span style="color: #666;">No services...</span>';
      return;
    }

    let allLines = [];
    for (const s of data) {
      try {
        const logsResp = await fetch(`/services/${s.key}/logs?lines=20`);
        const logsData = await logsResp.json();
        if (logsData.lines) {
          for (const line of logsData.lines.slice(-10)) {
            const escaped = line.replace(/</g, "&lt;");
            const lower = line.toLowerCase();
            let color = "#8b949e";
            if (lower.includes("error") || lower.includes("exception") || lower.includes("critical") ||
                lower.includes("failed") || lower.includes("traceback")) {
              color = "#f85149";
            } else if (lower.includes("warning") || lower.includes("warn")) {
              color = "#d29922";
            }
            allLines.push(`<div style="color: ${color};"><span style="color: #666;">[${s.key}]</span> ${escaped}</div>`);
          }
        }
      } catch (e) {}
    }

    if (allLines.length === 0) {
      el.innerHTML = '<span style="color: #666;">No service logs...</span>';
    } else {
      const wasAtBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 10;
      el.innerHTML = allLines.join("");
      if (wasAtBottom) el.scrollTop = el.scrollHeight;
    }
  } catch (e) {
    console.error("Service logs poll error:", e);
  }
}

// Server logs polling
async function pollServerLogs() {
  try {
    const resp = await fetch("/logs?limit=100");
    const data = await resp.json();
    const logsEl = document.getElementById("server-logs");
    if (!logsEl) return;

    const logs = data.logs || [];
    if (logs.length === 0) {
      logsEl.innerHTML = '<span style="color: #666;">No logs yet...</span>';
      return;
    }

    const html = logs.map(log => {
      const time = log.timestamp.split("T")[1].split(".")[0];
      let color = "#8b949e";
      if (log.level === "ERROR" || log.level === "CRITICAL") {
        color = "#f85149";
      } else if (log.level === "WARNING") {
        color = "#d29922";
      }
      return `<div style="color: ${color};">[${time}] ${log.name}: ${log.message}</div>`;
    }).join("");

    logsEl.innerHTML = html;
    logsEl.scrollTop = logsEl.scrollHeight;
  } catch (e) {
    console.error("Server logs poll error:", e);
  }
}

// Rewind logs polling
async function pollRewindLogs() {
  try {
    const resp = await fetch("/rewind/logs?limit=50");
    const data = await resp.json();
    const logsEl = document.getElementById("rewind-logs");
    if (!logsEl) return;

    const logs = data.logs || [];
    if (logs.length === 0) {
      logsEl.innerHTML = '<span style="color: #666;">No logs yet...</span>';
      return;
    }

    // Format logs with colors based on level
    const html = logs.map(log => {
      const time = log.timestamp.split("T")[1].split(".")[0];  // HH:MM:SS
      let color = "#8b949e";  // Default gray
      if (log.level === "ERROR" || log.level === "CRITICAL") {
        color = "#f85149";  // Red for errors
      } else if (log.level === "WARNING") {
        color = "#d29922";  // Orange for warnings
      } else if (log.level === "INFO") {
        color = "#8b949e";  // Gray for info
      }
      return `<div style="color: ${color};">[${time}] ${log.message}</div>`;
    }).join("");

    logsEl.innerHTML = html;
    // Auto-scroll to bottom
    logsEl.scrollTop = logsEl.scrollHeight;
  } catch (e) {
    console.error("Rewind logs poll error:", e);
  }
}

async function pollLease() {
  try {
    const [leaseResp, rewindResp, codeResp] = await Promise.all([
      fetch("/lease/status"),
      fetch("/rewind/status"),
      fetch("/code/status")
    ]);
    const data = await leaseResp.json();
    const rewindData = await rewindResp.json();
    const codeData = await codeResp.json();

    // Current holder
    const holderEl = document.getElementById("lease-holder");
    if (data.resetting) {
      holderEl.textContent = "Resetting...";
      holderEl.className = "lease-holder resetting";
    } else if (data.holder) {
      holderEl.textContent = data.holder;
      holderEl.className = "lease-holder";
    } else {
      holderEl.textContent = "(none)";
      holderEl.className = "lease-holder none";
    }

    // Remaining time
    const remEl = document.getElementById("lease-remaining");
    if (data.holder && data.remaining_s != null) {
      const m = Math.floor(data.remaining_s / 60);
      const s = Math.floor(data.remaining_s % 60);
      remEl.textContent = m > 0 ? m + "m " + s + "s" : s + "s";
    } else {
      remEl.textContent = "—";
    }

    // Status badge
    const badgeEl = document.getElementById("lease-status-badge");
    if (data.resetting) {
      badgeEl.textContent = "Resetting";
      badgeEl.className = "boundary-status warning";
    } else if (data.holder) {
      badgeEl.textContent = "Held";
      badgeEl.className = "boundary-status warning";
    } else {
      badgeEl.textContent = "Free";
      badgeEl.className = "boundary-status safe";
    }

    // Activity state: recovering arm > rewinding > resetting > executing code > idle
    const actEl = document.getElementById("robot-activity");
    if (rewindData.arm_recovering) {
      actEl.textContent = "Recovering Arm";
      actEl.className = "activity-badge recovering";
    } else if (rewindData.is_rewinding) {
      actEl.textContent = "Rewinding";
      actEl.className = "activity-badge rewinding";
    } else if (data.resetting) {
      actEl.textContent = "Resetting";
      actEl.className = "activity-badge resetting";
    } else if (codeData.is_running) {
      actEl.textContent = "Executing Code";
      actEl.className = "activity-badge executing";
    } else {
      actEl.textContent = "Idle";
      actEl.className = "activity-badge idle";
    }

    // Pause queue state
    queuePaused = !!data.paused;
    const pauseBtn = document.getElementById("btn-pause-queue");
    const pausedBadge = document.getElementById("queue-paused-badge");
    if (queuePaused) {
      pauseBtn.textContent = "Resume Queue";
      pauseBtn.style.background = "#4caf50";
      pauseBtn.style.color = "#fff";
      pausedBadge.style.display = "inline";
    } else {
      pauseBtn.textContent = "Pause Queue";
      pauseBtn.style.background = "#ff9800";
      pauseBtn.style.color = "#000";
      pausedBadge.style.display = "none";
    }

    // Queue
    const queueLen = data.queue_length || 0;
    document.getElementById("lease-queue-len").textContent = queueLen;
    document.getElementById("btn-clear-queue").style.display = (queueLen > 0 || data.holder) ? "" : "none";
    const listEl = document.getElementById("lease-queue-list");
    if (queueLen === 0) {
      listEl.innerHTML = '<li style="color: #666; font-style: italic;">Empty</li>';
    } else {
      listEl.innerHTML = data.queue.map(e =>
        `<li><span class="lease-queue-pos">#${e.position}</span><span class="lease-queue-name">${e.holder}</span></li>`
      ).join("");
    }
  } catch (e) {
    console.error("Lease poll error:", e);
  }
}

poll();
pollState();
pollRewind();
pollTrajectory();
pollRewindLogs();
pollServerLogs();
pollCodeLogs();
pollLease();
setInterval(poll, 2000);
setInterval(pollState, 200);
setInterval(pollRewind, 500);
setInterval(pollTrajectory, 500);
setInterval(pollRewindLogs, 1000);
setInterval(pollServerLogs, 2000);
setInterval(pollCodeLogs, 1000);
setInterval(pollLease, 1000);
</script></body></html>"""


def create_router(service_mgr: ServiceManager | None, arm_monitor=None):
    """Create the service routes with injected dependencies.

    Args:
        service_mgr: ServiceManager instance, or None if service management is disabled.
                     When None, only the dashboard route is available (without service controls).
        arm_monitor: Optional ArmMonitor to suppress/allow recovery on franka_server stop/start.
    """
    service_manager_enabled = service_mgr is not None

    @router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard():
        """Web dashboard for service management."""
        # Inject the service_manager_enabled flag into the HTML
        html = DASHBOARD_HTML.replace(
            "let serviceManagerEnabled = true;",
            f"let serviceManagerEnabled = {'true' if service_manager_enabled else 'false'};"
        )
        return html

    @router.get("/config", include_in_schema=False)
    async def get_config():
        """Get dashboard configuration (service manager status, etc.)."""
        return {"service_manager_enabled": service_manager_enabled}

    # Only add service management routes if service manager is enabled
    if service_mgr is not None:
        @router.get("", include_in_schema=False)
        async def list_services():
            """List all services with status, PID, uptime."""
            return service_mgr.get_status()

        @router.post("/unlock/lock", include_in_schema=False)
        async def lock_robot():
            """Lock the robot by stopping the unlock service.

            The unlock service runs with relock_on_exit=True and signal handlers,
            so stopping it will automatically: deactivate FCI, lock brakes, release token.
            """
            await service_mgr.stop_service("unlock")

            state = service_mgr._services.get("unlock")
            if state:
                state.logs.append("[lock: stopped unlock service, cleanup will lock robot]")

            return {"ok": True, "message": "Robot locked (unlock service stopped)"}

        @router.get("/{name}", include_in_schema=False)
        async def get_service(name: str):
            """Get status of a specific service."""
            result = service_mgr.get_status(name)
            if "error" in result:
                return {"ok": False, **result}
            return result

        @router.post("/{name}/start", include_in_schema=False)
        async def start_service(name: str):
            """Start a service."""
            result = await service_mgr.start_service(name)
            if name == "franka_server" and result.get("ok") and arm_monitor is not None:
                arm_monitor.allow_recovery()
            return result

        @router.post("/{name}/stop", include_in_schema=False)
        async def stop_service(name: str):
            """Stop a service."""
            if name == "franka_server" and arm_monitor is not None:
                arm_monitor.suppress_recovery()
            return await service_mgr.stop_service(name)

        @router.post("/{name}/restart", include_in_schema=False)
        async def restart_service(name: str):
            """Restart a service."""
            if name == "franka_server" and arm_monitor is not None:
                arm_monitor.suppress_recovery()
            result = await service_mgr.restart_service(name)
            if name == "franka_server" and result.get("ok") and arm_monitor is not None:
                arm_monitor.allow_recovery()
            return result

        @router.get("/{name}/logs", include_in_schema=False)
        async def get_logs(name: str, lines: int = Query(default=50, ge=1, le=1000)):
            """Get recent log output for a service."""
            return service_mgr.get_logs(name, lines=lines)

    return router
