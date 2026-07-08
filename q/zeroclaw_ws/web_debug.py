from __future__ import annotations

import json
import queue
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from c6_audio import C6DaemonClient
from c6_sensevoice_stream_asr import C6StreamSource, drain_audio_queue, listen_one_utterance
from config import (
    C6_STREAM_FIFO_PATH,
    ZEROCLAW_WEB_EVENT_LIMIT,
    ZEROCLAW_WEB_HOST,
    ZEROCLAW_WEB_PORT,
)
from hardware_client import stop_speaking
from sherpa_ws_asr import SherpaOfflineWebSocketASR
from session_log import SessionLogger
from wake_context import parse_and_save_wake_context
from wake_reply import play_wake_reply
from zeroclaw_client import ZeroClawClient


PHASE_LABELS = {
    "starting": "启动中",
    "idle": "空闲",
    "voice_listening": "语音输入中",
    "asr": "语音识别中",
    "working": "ZeroClaw 工作中",
    "speaking": "播报中",
    "error": "异常",
    "stopped": "已停止",
}


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ZeroClaw Web Debug</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f5f7;
      --panel: #ffffff;
      --text: #1f2328;
      --muted: #667085;
      --line: #d0d7de;
      --accent: #0969da;
      --danger: #cf222e;
      --ok: #1a7f37;
      --warn: #9a6700;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      height: 100vh;
      overflow: hidden;
    }
    .shell {
      width: 100%;
      margin: 0;
      height: 100vh;
      padding: 10px 14px 14px;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      gap: 10px;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 42px;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
    }
    .topbar {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      flex-wrap: wrap;
    }
    .status-meta {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      min-width: 0;
      font-size: 12px;
    }
    .label { color: var(--muted); }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      padding: 3px 9px;
      border: 1px solid var(--line);
      border-radius: 999px;
      font-weight: 600;
      background: #f6f8fa;
    }
    .pill.ok { color: var(--ok); border-color: #8ddb8c; background: #dafbe1; }
    .pill.busy { color: var(--warn); border-color: #d4a72c; background: #fff8c5; }
    .pill.err { color: var(--danger); border-color: #ffb3ba; background: #ffebe9; }
    textarea {
      width: 100%;
      min-height: 76px;
      max-height: 180px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      color: var(--text);
      background: #fff;
      font: inherit;
    }
    textarea:disabled {
      background: #f6f8fa;
      color: #8c959f;
    }
    .actions {
      display: flex;
      gap: 8px;
      margin-top: 10px;
      align-items: center;
      flex-wrap: wrap;
    }
    button {
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--text);
      padding: 0 12px;
      font-weight: 600;
      cursor: pointer;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    button.ghost {
      background: transparent;
    }
    button:disabled {
      cursor: not-allowed;
      opacity: .55;
    }
    .hint {
      color: var(--muted);
      font-size: 12px;
    }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    .timeline {
      overflow: auto;
      background: transparent;
      padding: 12px 6px 18px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }
    .turn {
      display: grid;
      gap: 8px;
    }
    .message-row {
      display: flex;
      align-items: flex-start;
      gap: 8px;
    }
    .message-row.user {
      justify-content: flex-end;
    }
    .message-row.assistant {
      justify-content: flex-start;
    }
    .avatar {
      width: 28px;
      height: 28px;
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: none;
      background: #24292f;
      color: #fff;
      font-size: 12px;
      font-weight: 700;
    }
    .assistant-stack {
      display: grid;
      gap: 6px;
      flex: 1;
      min-width: 0;
      width: 100%;
    }
    .bubble {
      max-width: 100%;
      border-radius: 8px;
      padding: 10px 12px;
      white-space: pre-wrap;
      word-break: break-word;
      box-shadow: 0 1px 0 rgba(31, 35, 40, .04);
    }
    .bubble.user {
      background: #ddf4ff;
      border: 1px solid #80ccff;
    }
    .bubble.assistant {
      background: #fff;
      border: 1px solid var(--line);
    }
    .bubble-meta {
      margin-bottom: 4px;
      color: var(--muted);
      font-size: 12px;
    }
    .system-line {
      align-self: center;
      max-width: min(760px, 92%);
      color: var(--muted);
      font-size: 12px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(255,255,255,.72);
      padding: 4px 10px;
    }
    .trace {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f6f8fa;
      color: var(--muted);
      font-size: 12px;
      min-width: 0;
    }
    .trace[hidden] {
      display: none;
    }
    .trace summary {
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      min-height: 30px;
      padding: 5px 9px;
      list-style: none;
    }
    .trace summary::-webkit-details-marker { display: none; }
    .trace summary::before {
      content: ">";
      color: var(--muted);
      transform: rotate(0deg);
      transition: transform .12s ease;
    }
    .trace[open] summary::before {
      transform: rotate(90deg);
    }
    .trace-title {
      font-weight: 650;
      color: var(--text);
    }
    .trace-preview {
      flex: 1;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .trace-list {
      border-top: 1px solid var(--line);
      padding: 6px;
      display: grid;
      gap: 5px;
      min-width: 0;
    }
    .log-entry {
      align-self: stretch;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--muted);
      min-width: 0;
    }
    .log-entry[open] {
      background: #fff;
    }
    .log-entry summary {
      min-height: 34px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 7px 10px;
      font-size: 12px;
      list-style: none;
    }
    .log-entry summary::-webkit-details-marker { display: none; }
    .log-entry summary::before {
      content: ">";
      margin-right: 4px;
      color: var(--muted);
      transform: rotate(0deg);
      transition: transform .12s ease;
    }
    .log-entry[open] summary::before {
      transform: rotate(90deg);
    }
    .log-kind {
      font-weight: 650;
      color: var(--text);
    }
    .log-preview {
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      min-width: 0;
    }
    .log-time {
      flex: none;
      color: var(--muted);
    }
    .log-body {
      border-top: 1px solid var(--line);
      margin: 0;
      padding: 9px 10px;
      color: var(--text);
      max-height: 260px;
      overflow-y: auto;
      overflow-x: hidden;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    .log-entry.tool_call .log-kind { color: #8250df; }
    .log-entry.tool_result .log-kind { color: #0969da; }
    .log-entry.error .log-kind { color: var(--danger); }
    .composer {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 10px;
      display: grid;
      gap: 8px;
    }
    .composer-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: end;
    }
    .composer-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: space-between;
      min-height: 24px;
    }
    .composer-buttons {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .log-path {
      flex: 1;
      min-width: 0;
      max-width: none;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      text-align: right;
    }
    @media (max-width: 820px) {
      body { height: 100dvh; }
      .shell { height: 100dvh; padding: 8px; }
      header { align-items: flex-start; flex-direction: column; gap: 8px; }
      .composer-row { grid-template-columns: 1fr; }
      .bubble { max-width: 100%; }
      .assistant-stack { width: 100%; }
      .log-path { max-width: 90vw; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <h1>ZeroClaw Web Debug</h1>
      <div class="topbar">
        <span id="phasePill" class="pill">连接中</span>
        <span class="status-meta">输入源 <span id="source">-</span></span>
        <span class="status-meta">播报 <span id="tts">-</span></span>
      </div>
    </header>
    <section class="timeline" id="events"></section>
    <section class="composer">
      <div class="composer-row">
        <textarea id="input" placeholder="空闲时在这里输入要发送给 ZeroClaw 的调试指令。Ctrl+Enter 发送。"></textarea>
        <div class="composer-buttons">
          <button id="sendBtn" class="primary">发送</button>
          <button id="stopTtsBtn" class="ghost">停止播报</button>
        </div>
      </div>
      <div class="composer-actions">
        <span id="sendState" class="hint"></span>
        <span id="logPath" class="hint mono log-path">-</span>
      </div>
    </section>
  </main>
  <script>
    const input = document.getElementById('input');
    const sendBtn = document.getElementById('sendBtn');
    const stopTtsBtn = document.getElementById('stopTtsBtn');
    const sendState = document.getElementById('sendState');
    const eventsEl = document.getElementById('events');
    let lastEventId = 0;
    let currentTurn = null;

    function setText(id, text) {
      document.getElementById(id).textContent = text || '-';
    }

    function phaseClass(status) {
      if (status.phase === 'error') return 'pill err';
      if (status.input_enabled) return 'pill ok';
      return 'pill busy';
    }

    async function refreshStatus() {
      try {
        const res = await fetch('/api/status');
        const status = await res.json();
        setText('source', status.active_source || '-');
        setText('tts', status.tts_speaking ? '播报中' : '空闲');
        setText('logPath', status.current_log_path ? '日志 ' + status.current_log_path : '');
        const pill = document.getElementById('phasePill');
        pill.className = phaseClass(status);
        pill.textContent = status.input_enabled ? '可输入' : status.phase_label;
        input.disabled = !status.input_enabled;
        sendBtn.disabled = !status.input_enabled;
      } catch (err) {
        document.getElementById('phasePill').className = 'pill err';
        document.getElementById('phasePill').textContent = '连接失败';
      }
    }

    function eventTitle(ev) {
      if (ev.kind === 'tool_call') return '工具调用';
      if (ev.kind === 'tool_result') return '工具结果';
      if (ev.kind === 'log' || ev.kind === 'turn_log') return '日志文件';
      if (ev.kind === 'message_send') return '发送';
      if (ev.kind === 'state') return '状态';
      if (ev.kind === 'wake') return '唤醒';
      if (ev.kind === 'asr') return '语音识别';
      if (ev.kind === 'tts') return '语音播报';
      if (ev.kind === 'turn') return '本轮耗时';
      if (ev.kind === 'error') return '错误';
      return ev.kind;
    }

    function compact(text, limit = 150) {
      const value = (text || '').replace(/\\s+/g, ' ').trim();
      if (value.length <= limit) return value;
      return value.slice(0, limit) + '...';
    }

    function logBodyText(ev) {
      if (ev.kind === 'tool_call') {
        return compact(ev.message, 260);
      }
      if (ev.kind === 'tool_result') {
        return compact(ev.message, 360);
      }
      return ev.message || '';
    }

    function renderBubble(ev, role, title) {
      const row = document.createElement('div');
      row.className = 'message-row ' + role;
      if (role === 'assistant') {
        const avatar = document.createElement('div');
        avatar.className = 'avatar';
        avatar.textContent = 'Z';
        row.appendChild(avatar);
      }
      const item = document.createElement('div');
      item.className = 'bubble ' + role;
      const meta = document.createElement('div');
      meta.className = 'bubble-meta';
      meta.textContent = title + ' · ' + ev.ts;
      const body = document.createElement('div');
      body.textContent = ev.message || '';
      item.appendChild(meta);
      item.appendChild(body);
      row.appendChild(item);
      return row;
    }

    function renderSystemLine(ev) {
      const item = document.createElement('div');
      item.className = 'system-line';
      item.textContent = eventTitle(ev) + ' · ' + compact(ev.message, 110);
      return item;
    }

    function renderLogEntry(ev) {
      const item = document.createElement('details');
      item.className = 'log-entry ' + ev.kind;
      if (ev.kind === 'error') item.open = true;
      const summary = document.createElement('summary');
      const kind = document.createElement('span');
      kind.className = 'log-kind';
      kind.textContent = eventTitle(ev);
      const preview = document.createElement('span');
      preview.className = 'log-preview';
      preview.textContent = compact(ev.message);
      const time = document.createElement('span');
      time.className = 'log-time';
      time.textContent = '#' + ev.id + ' · ' + ev.ts;
      const body = document.createElement('pre');
      body.className = 'log-body mono';
      body.textContent = logBodyText(ev);
      summary.appendChild(kind);
      summary.appendChild(preview);
      summary.appendChild(time);
      item.appendChild(summary);
      item.appendChild(body);
      return item;
    }

    function createTurn(ev) {
      const turn = document.createElement('div');
      turn.className = 'turn';
      turn.appendChild(renderBubble(ev, 'user', '用户'));

      const assistantRow = document.createElement('div');
      assistantRow.className = 'message-row assistant';
      assistantRow.hidden = true;
      const avatar = document.createElement('div');
      avatar.className = 'avatar';
      avatar.textContent = 'Z';
      const stack = document.createElement('div');
      stack.className = 'assistant-stack';

      const trace = document.createElement('details');
      trace.className = 'trace';
      trace.hidden = true;
      const summary = document.createElement('summary');
      const title = document.createElement('span');
      title.className = 'trace-title';
      title.textContent = '过程日志';
      const preview = document.createElement('span');
      preview.className = 'trace-preview';
      preview.textContent = '';
      const count = document.createElement('span');
      count.className = 'log-time';
      count.textContent = '0 条';
      const list = document.createElement('div');
      list.className = 'trace-list';
      summary.appendChild(title);
      summary.appendChild(preview);
      summary.appendChild(count);
      trace.appendChild(summary);
      trace.appendChild(list);
      stack.appendChild(trace);

      assistantRow.appendChild(avatar);
      assistantRow.appendChild(stack);
      turn.appendChild(assistantRow);
      eventsEl.appendChild(turn);

      currentTurn = {
        turn,
        assistantRow,
        stack,
        trace,
        traceList: list,
        tracePreview: preview,
        traceCount: count,
        logCount: 0,
      };
    }

    function appendTrace(ev) {
      if (!currentTurn) {
        eventsEl.appendChild(renderSystemLine(ev));
        return;
      }
      currentTurn.assistantRow.hidden = false;
      currentTurn.trace.hidden = false;
      currentTurn.logCount += 1;
      currentTurn.traceCount.textContent = currentTurn.logCount + ' 条';
      currentTurn.tracePreview.textContent = compact(ev.message, 120);
      const entry = renderLogEntry(ev);
      if (ev.kind === 'error') {
        currentTurn.trace.open = true;
      }
      currentTurn.traceList.appendChild(entry);
    }

    function appendAssistant(ev) {
      if (!currentTurn) {
        const turn = document.createElement('div');
        turn.className = 'turn';
        turn.appendChild(renderBubble(ev, 'assistant', 'ZeroClaw'));
        eventsEl.appendChild(turn);
        return;
      }
      currentTurn.assistantRow.hidden = false;
      const bubble = document.createElement('div');
      bubble.className = 'bubble assistant';
      const meta = document.createElement('div');
      meta.className = 'bubble-meta';
      meta.textContent = 'ZeroClaw · ' + ev.ts;
      const body = document.createElement('div');
      body.textContent = ev.message || '';
      bubble.appendChild(meta);
      bubble.appendChild(body);
      currentTurn.stack.appendChild(bubble);
    }

    function handleEvent(ev) {
      if (ev.kind === 'user') {
        createTurn(ev);
        return;
      }
      if (ev.kind === 'done') {
        appendAssistant(ev);
        return;
      }
      if (['state', 'connected', 'connect_start', 'closed'].includes(ev.kind) && !currentTurn) {
        eventsEl.appendChild(renderSystemLine(ev));
        return;
      }
      appendTrace(ev);
    }

    async function refreshEvents() {
      try {
        const res = await fetch('/api/events?since=' + lastEventId);
        const data = await res.json();
        for (const ev of data.events || []) {
          lastEventId = Math.max(lastEventId, ev.id);
          handleEvent(ev);
        }
        while (eventsEl.children.length > 120) {
          eventsEl.removeChild(eventsEl.firstChild);
        }
        if (data.events && data.events.length) {
          eventsEl.scrollTop = eventsEl.scrollHeight;
        }
      } catch (err) {
      }
    }

    async function sendText() {
      const text = input.value.trim();
      if (!text) return;
      sendBtn.disabled = true;
      sendState.textContent = '发送中...';
      try {
        const res = await fetch('/api/send', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({text})
        });
        const data = await res.json();
        if (!data.ok) {
          sendState.textContent = data.message || '发送失败';
        } else {
          input.value = '';
          sendState.textContent = '已接收';
        }
      } catch (err) {
        sendState.textContent = '网络错误';
      } finally {
        setTimeout(() => { sendState.textContent = ''; }, 2500);
        refreshStatus();
      }
    }

    async function stopTts() {
      try {
        await fetch('/api/stop_tts', {method: 'POST'});
        refreshStatus();
      } catch (err) {
      }
    }

    sendBtn.addEventListener('click', sendText);
    stopTtsBtn.addEventListener('click', stopTts);
    input.addEventListener('keydown', (ev) => {
      if (ev.ctrlKey && ev.key === 'Enter') {
        ev.preventDefault();
        sendText();
      }
    });

    refreshStatus();
    refreshEvents();
    setInterval(refreshStatus, 1500);
    setInterval(refreshEvents, 1500);
  </script>
</body>
</html>
"""


class WebDebugRuntime:
    def __init__(self, session_logger: SessionLogger | None = None) -> None:
        self.events: deque[dict[str, Any]] = deque(maxlen=max(50, ZEROCLAW_WEB_EVENT_LIMIT))
        self.events_lock = threading.Lock()
        self.next_event_id = 1
        self.state_lock = threading.Lock()
        self.work_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.phase = "starting"
        self.ready = False
        self.active_source = ""
        self.last_user = ""
        self.last_response = ""
        self.session_logger = session_logger
        self.current_log_path = str(session_logger.path) if session_logger is not None else ""
        self.client: ZeroClawClient | None = None
        self.c6: C6DaemonClient | None = None
        self.asr_ws: SherpaOfflineWebSocketASR | None = None
        self.thread: threading.Thread | None = None

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def add_event(self, kind: str, message: str, data: Any = None) -> None:
        with self.events_lock:
            event = {
                "id": self.next_event_id,
                "ts": self._now(),
                "kind": kind,
                "message": message,
                "data": data,
            }
            self.next_event_id += 1
            self.events.append(event)
        print(f"[web-debug] {kind}: {_compact_text(message, 260)}")
        if self.session_logger is not None and kind not in {
            "user",
            "message_send",
            "tool_call",
            "tool_result",
            "done",
            "tts",
            "log",
            "turn",
        }:
            self.session_logger.log(kind, message, data)

    def _set_phase(self, phase: str, source: str = "", detail: str = "") -> None:
        with self.state_lock:
            self.phase = phase
            self.active_source = source
        label = PHASE_LABELS.get(phase, phase)
        suffix = f" source={source}" if source else ""
        if detail:
            suffix += f" {detail}"
        self.add_event("state", f"{label}{suffix}")

    def status(self) -> dict[str, Any]:
        with self.state_lock:
            phase = self.phase
            tts_speaking = phase == "speaking"
            effective_phase = "speaking" if phase == "idle" and tts_speaking else phase
            input_enabled = bool(
                self.ready
                and effective_phase == "idle"
                and not self.work_lock.locked()
            )
            return {
                "ready": self.ready,
                "phase": effective_phase,
                "phase_label": PHASE_LABELS.get(effective_phase, effective_phase),
                "input_enabled": input_enabled,
                "active_source": self.active_source,
                "tts_speaking": tts_speaking,
                "last_user": self.last_user,
                "last_response": self.last_response,
                "current_log_path": self.current_log_path,
            }

    def get_events_since(self, since: int) -> list[dict[str, Any]]:
        with self.events_lock:
            return [event for event in self.events if int(event["id"]) > since]

    def start_background(self) -> None:
        self.thread = threading.Thread(target=self._bootstrap_and_run_voice, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self._set_phase("stopped")
        if self.c6 is not None:
            try:
                self.c6.stop()
            except Exception as exc:  # noqa: BLE001
                self.add_event("error", f"C6 stop failed: {exc!r}")
        if self.asr_ws is not None:
            try:
                self.asr_ws.stop_server()
            except Exception as exc:  # noqa: BLE001
                self.add_event("error", f"ASR stop failed: {exc!r}")
        if self.client is not None:
            try:
                self.client.close()
            except Exception as exc:  # noqa: BLE001
                self.add_event("error", f"ZeroClaw close failed: {exc!r}")
        if self.session_logger is not None:
            self.session_logger.close()

    def submit_ui_text(self, text: str) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "code": "empty_text", "message": "输入不能为空"}
        if not self.ready:
            return {"ok": False, "code": "not_ready", "message": "系统还在启动"}
        if not self.work_lock.acquire(blocking=False):
            return {"ok": False, "code": "busy", "message": "当前有任务正在执行"}

        thread = threading.Thread(target=self._run_text_turn_locked, args=("ui", text), daemon=True)
        thread.start()
        return {"ok": True, "message": "已接收"}

    def stop_tts(self) -> dict[str, Any]:
        stopped = stop_speaking()
        self.add_event("tts", f"停止播报: {stopped}")
        return {"ok": True, "stopped": stopped}

    def _on_zeroclaw_event(self, kind: str, data: Any) -> None:
        if kind == "turn_log" and isinstance(data, dict):
            self.current_log_path = str(data.get("path") or "")
            self.add_event("log", self.current_log_path, data)
            return
        if kind == "tool_call" and isinstance(data, dict):
            name = str(data.get("name") or "<unknown>")
            args = _compact_text(str(data.get("args") or ""), 180)
            self.add_event("tool_call", f"{name} {args}".strip(), {"name": name, "args": args})
            return
        if kind == "tool_result" and isinstance(data, dict):
            name = str(data.get("name") or "<unknown>")
            output = _compact_text(str(data.get("output") or ""), 260)
            self.add_event("tool_result", f"{name} => {output}", {"name": name, "output": output})
            return
        if kind == "done" and isinstance(data, dict):
            self.add_event("done", str(data.get("full_response") or ""), data)
            with self.state_lock:
                source = self.active_source
            self._set_phase("speaking", source)
            return
        if kind == "speak_text_result":
            self.add_event("tts", _compact_json(data), data)
            return
        if kind == "turn_end" and isinstance(data, dict):
            self.add_event("turn", _format_turn_elapsed(data), data)
            return
        self.add_event(kind, _compact_json(data), data)

    def _bootstrap_and_run_voice(self) -> None:
        audio_queue: queue.Queue[bytes] = queue.Queue()
        try:
            self._set_phase("starting", detail="ZeroClaw")
            self.client = ZeroClawClient(
                debug=True,
                event_callback=self._on_zeroclaw_event,
                speak_wait=True,
                session_logger=self.session_logger,
            )
            self.client.connect()

            self._set_phase("starting", detail="ASR")
            self.asr_ws = SherpaOfflineWebSocketASR()
            self.asr_ws.start_server_if_needed()

            self._set_phase("starting", detail="C6")
            self.c6 = C6DaemonClient()
            self.c6.start()
            print("正在启动 C6 后端，等待设备初始化/资源更新...")
            self.c6.wait_until_ready()
            print("C6 后端已就绪。")

            with self.state_lock:
                self.ready = True
            self._set_phase("idle")
            self._voice_loop(audio_queue)
        except Exception as exc:  # noqa: BLE001
            if self.stop_event.is_set():
                with self.state_lock:
                    self.ready = False
                    self.phase = "stopped"
                self.add_event("state", "已停止")
                return
            with self.state_lock:
                self.ready = False
                self.phase = "error"
            self.add_event("error", repr(exc))

    def _voice_loop(self, audio_queue: queue.Queue[bytes]) -> None:
        assert self.c6 is not None
        while not self.stop_event.is_set():
            self.add_event("voice", "等待 C6 唤醒词")
            wake_event = self.c6.wait_for_wake()
            if self.stop_event.is_set():
                return

            if not self.work_lock.acquire(blocking=False):
                self.add_event("voice_ignored", "当前有 UI 或 ZeroClaw 任务，忽略本次语音唤醒", {"wake_event": wake_event})
                self.c6.cancel_wake()
                continue

            try:
                self._handle_voice_turn_locked(wake_event, audio_queue)
            finally:
                if self.work_lock.locked():
                    self.work_lock.release()
                if not self.stop_event.is_set():
                    self._set_phase("idle")

    def _handle_voice_turn_locked(self, wake_event: str, audio_queue: queue.Queue[bytes]) -> None:
        assert self.c6 is not None
        assert self.asr_ws is not None
        self._set_phase("voice_listening", "voice")
        self.add_event("wake", wake_event)
        wake_context = parse_and_save_wake_context(wake_event)
        if wake_context is not None:
            self.add_event("wake_context", _compact_json(wake_context), wake_context)

        drain_audio_queue(audio_queue)
        with C6StreamSource(self.c6, C6_STREAM_FIFO_PATH, audio_queue):
            play_wake_reply()
            self.add_event("voice", "请说出指令")
            pcm = listen_one_utterance(audio_queue)

        if not pcm:
            self.add_event("voice", "未录到有效语音")
            return

        self._set_phase("asr", "voice")
        started = time.perf_counter()
        text = self.asr_ws.transcribe_pcm16(pcm).strip()
        elapsed = time.perf_counter() - started
        self.add_event("asr", f"{text} ({elapsed:.3f}s)")
        if not text:
            return

        self._process_turn_locked("voice", text)

    def _run_text_turn_locked(self, source: str, text: str) -> None:
        try:
            self._process_turn_locked(source, text)
        except Exception as exc:  # noqa: BLE001
            self.add_event("error", repr(exc))
        finally:
            if self.work_lock.locked():
                self.work_lock.release()
            if not self.stop_event.is_set():
                self._set_phase("idle")

    def _process_turn_locked(self, source: str, text: str) -> None:
        if self.client is None:
            raise RuntimeError("ZeroClaw client not ready")
        with self.state_lock:
            self.last_user = text
            self.last_response = ""
        self._set_phase("working", source)
        self.add_event("user", text)
        response = self.client.send_message(text)
        with self.state_lock:
            self.last_response = response


def _compact_json(data: Any) -> str:
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(data)


def _compact_text(text: str, limit: int = 300) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _format_turn_elapsed(data: dict[str, Any]) -> str:
    response = data.get("response_elapsed_seconds")
    tts = data.get("tts_elapsed_seconds")
    total = data.get("total_elapsed_seconds", data.get("elapsed_seconds"))

    parts: list[str] = []
    if isinstance(total, (int, float)):
        parts.append(f"总耗时 {total:.3f}s")
    if isinstance(response, (int, float)):
        parts.append(f"ZeroClaw {response:.3f}s")
    if isinstance(tts, (int, float)):
        parts.append(f"TTS {tts:.3f}s")
    if not parts:
        parts.append(_compact_json(data))
    return " / ".join(parts)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def make_handler(runtime: WebDebugRuntime) -> type[BaseHTTPRequestHandler]:
    class WebDebugHandler(BaseHTTPRequestHandler):
        server_version = "ZeroClawWebDebug/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            request_line = str(args[0]) if args else ""
            if "GET /api/status" in request_line or "GET /api/events" in request_line:
                return
            print(f"[web-debug] {self.client_address[0]} - {fmt % args}")

        def _read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            raw = self.rfile.read(length) if length > 0 else b"{}"
            if not raw:
                return {}
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            return data

        def _send_json(self, status: int, data: Any) -> None:
            body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self) -> None:
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send_json(200, {"ok": True})

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html()
                return
            if parsed.path == "/api/status":
                self._send_json(200, runtime.status())
                return
            if parsed.path == "/api/events":
                qs = parse_qs(parsed.query)
                try:
                    since = int((qs.get("since") or ["0"])[0])
                except ValueError:
                    since = 0
                self._send_json(200, {"ok": True, "events": runtime.get_events_since(since)})
                return
            self._send_json(404, {"ok": False, "message": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                data = self._read_json()
            except Exception as exc:  # noqa: BLE001
                self._send_json(400, {"ok": False, "message": f"invalid json: {exc}"})
                return

            if parsed.path == "/api/send":
                result = runtime.submit_ui_text(str(data.get("text") or ""))
                self._send_json(202 if result.get("ok") else 409, result)
                return
            if parsed.path == "/api/stop_tts":
                self._send_json(200, runtime.stop_tts())
                return
            self._send_json(404, {"ok": False, "message": "not found"})

    return WebDebugHandler


def _lan_urls(host: str, port: int) -> list[str]:
    urls = [f"http://127.0.0.1:{port}/"]
    if host not in {"0.0.0.0", "::"}:
        urls.append(f"http://{host}:{port}/")
        return list(dict.fromkeys(urls))

    candidates: set[str] = set()
    try:
        hostname = socket.gethostname()
        for item in socket.getaddrinfo(hostname, port, socket.AF_INET, socket.SOCK_STREAM):
            candidates.add(item[4][0])
    except OSError:
        pass

    for ip in sorted(candidates):
        if not ip.startswith("127."):
            urls.append(f"http://{ip}:{port}/")
    return list(dict.fromkeys(urls))


def run_web_debug_server(host: str = ZEROCLAW_WEB_HOST, port: int = ZEROCLAW_WEB_PORT) -> None:
    session_logger = SessionLogger.create("web")
    runtime = WebDebugRuntime(session_logger=session_logger)
    handler = make_handler(runtime)
    server = ReusableThreadingHTTPServer((host, port), handler)

    runtime.start_background()
    print("[web-debug] Web UI 已启动:")
    if session_logger is not None:
        session_logger.log("system", "web mode start", {"host": host, "port": port, "log_path": str(session_logger.path)})
    for url in _lan_urls(host, port):
        print(f"[web-debug]   {url}")
        if session_logger is not None:
            session_logger.log("system", "web ui url", {"url": url})
    print("[web-debug] 空闲时可用 C6 语音唤醒或 Web 输入；工作中会拒绝新输入。")

    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\n[web-debug] 收到 Ctrl+C，准备退出...")
    finally:
        runtime.stop()
        server.server_close()
