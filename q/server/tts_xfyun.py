"""讯飞开放平台在线流式语音合成，供 Hardware API 调用。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import signal
import subprocess
import threading
import time
from email.utils import formatdate
from urllib.parse import urlencode, urlparse

import websocket

from config import (
    TTS_STOP_TIMEOUT_SECONDS,
    XFYUN_TTS_API_KEY,
    XFYUN_TTS_API_SECRET,
    XFYUN_TTS_APPID,
    XFYUN_TTS_AUE,
    XFYUN_TTS_AUF,
    XFYUN_TTS_DEBUG,
    XFYUN_TTS_DEVICE,
    XFYUN_TTS_ENABLED,
    XFYUN_TTS_PITCH,
    XFYUN_TTS_PLAYER,
    XFYUN_TTS_SPEED,
    XFYUN_TTS_URL,
    XFYUN_TTS_VCN,
    XFYUN_TTS_VOLUME,
)


_TTS_LOCK = threading.Lock()
_TTS_ACTIVE = threading.Event()
_TTS_STOP_EVENT = threading.Event()
_TTS_STATE_LOCK = threading.Lock()
_CURRENT_PLAYER = None
_CURRENT_WS = None


def _terminate_player(player, timeout: float) -> bool:  # noqa: ANN001
    if player is None:
        return True

    try:
        if player.poll() is not None:
            return True

        try:
            os.killpg(player.pid, signal.SIGTERM)
        except Exception:
            try:
                player.terminate()
            except Exception:
                pass

        try:
            player.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            pass

        try:
            os.killpg(player.pid, signal.SIGKILL)
        except Exception:
            try:
                player.kill()
            except Exception:
                pass

        try:
            player.wait(timeout=timeout)
        except Exception:
            pass

        return player.poll() is not None
    except Exception:
        return False


def is_speaking() -> bool:
    return _TTS_ACTIVE.is_set()


def wait_until_idle(timeout: float | None = None) -> bool:
    deadline = None if timeout is None else time.time() + timeout
    while _TTS_ACTIVE.is_set():
        if deadline is not None and time.time() >= deadline:
            return False
        time.sleep(0.05)
    return True


def stop_speaking(timeout: float = TTS_STOP_TIMEOUT_SECONDS) -> bool:
    if not _TTS_ACTIVE.is_set():
        return False

    _TTS_STOP_EVENT.set()

    with _TTS_STATE_LOCK:
        player = _CURRENT_PLAYER
        ws = _CURRENT_WS

    if ws is not None:
        try:
            ws.close()
        except Exception:
            pass

    player_stopped = _terminate_player(player, timeout)
    idle = wait_until_idle(timeout=timeout)
    return player_stopped and idle


def _build_auth_url() -> str:
    parsed = urlparse(XFYUN_TTS_URL)
    host = parsed.netloc
    path = parsed.path or "/v2/tts"
    date = formatdate(timeval=None, localtime=False, usegmt=True)

    signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
    signature_sha = hmac.new(
        XFYUN_TTS_API_SECRET.encode("utf-8"),
        signature_origin.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    signature = base64.b64encode(signature_sha).decode("utf-8")

    authorization_origin = (
        f'api_key="{XFYUN_TTS_API_KEY}", '
        f'algorithm="hmac-sha256", '
        f'headers="host date request-line", '
        f'signature="{signature}"'
    )
    authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")
    query = urlencode({"authorization": authorization, "date": date, "host": host})
    return f"{XFYUN_TTS_URL}?{query}"


def _build_payload(text: str) -> str:
    text_b64 = base64.b64encode(text.encode("utf-8")).decode("utf-8")
    return json.dumps(
        {
            "common": {"app_id": XFYUN_TTS_APPID},
            "business": {
                "aue": XFYUN_TTS_AUE,
                "auf": XFYUN_TTS_AUF,
                "vcn": XFYUN_TTS_VCN,
                "tte": "UTF8",
                "speed": XFYUN_TTS_SPEED,
                "volume": XFYUN_TTS_VOLUME,
                "pitch": XFYUN_TTS_PITCH,
            },
            "data": {"status": 2, "text": text_b64},
        },
        ensure_ascii=False,
    )


def _player_command() -> list[str]:
    player = XFYUN_TTS_PLAYER.strip().lower()
    if player == "aplay":
        cmd = ["aplay", "-q"]
        if XFYUN_TTS_DEVICE.strip():
            cmd.extend(["-D", XFYUN_TTS_DEVICE.strip()])
        cmd.extend(["-f", "S16_LE", "-r", "16000", "-c", "1"])
        return cmd
    if player == "ffplay":
        if XFYUN_TTS_AUE == "raw":
            return [
                "ffplay",
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "quiet",
                "-f",
                "s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-",
            ]
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-"]
    if player == "mpg123":
        return ["mpg123", "-q", "-"]
    return ["mpv", "--no-terminal", "--really-quiet", "-"]


def _player_stderr(player) -> str:  # noqa: ANN001
    if player is None or player.stderr is None:
        return ""
    try:
        data = player.stderr.read()
    except Exception:
        return ""
    if not data:
        return ""
    return data.decode("utf-8", errors="replace").strip()


def speak(text: str) -> dict:
    global _CURRENT_PLAYER, _CURRENT_WS

    started = time.perf_counter()
    text = (text or "").strip()
    if not text:
        return {"spoken": False, "reason": "empty_text", "duration_ms": 0}
    if not XFYUN_TTS_ENABLED:
        return {"spoken": False, "reason": "tts_disabled", "duration_ms": 0}
    if not (XFYUN_TTS_APPID and XFYUN_TTS_API_KEY and XFYUN_TTS_API_SECRET):
        return {"spoken": False, "reason": "missing_xfyun_credentials", "duration_ms": 0}

    _TTS_STOP_EVENT.clear()
    _TTS_ACTIVE.set()
    with _TTS_LOCK:
        player = None
        ws = None
        completed_normally = False
        try:
            if _TTS_STOP_EVENT.is_set():
                return {"spoken": False, "reason": "stopped_before_start"}

            cmd = _player_command()
            if XFYUN_TTS_DEBUG:
                print("[xfyun_tts] 播放器命令:", " ".join(cmd))

            player = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            ws = websocket.create_connection(_build_auth_url(), timeout=10)
            with _TTS_STATE_LOCK:
                _CURRENT_PLAYER = player
                _CURRENT_WS = ws

            ws.send(_build_payload(text))

            while True:
                if _TTS_STOP_EVENT.is_set():
                    return {"spoken": False, "reason": "stopped"}

                raw = ws.recv()
                if _TTS_STOP_EVENT.is_set():
                    return {"spoken": False, "reason": "stopped"}

                data = json.loads(raw)
                code = data.get("code", 0)
                if code != 0:
                    return {"spoken": False, "reason": "xfyun_error", "xfyun": data}

                audio_b64 = data.get("data", {}).get("audio", "")
                if audio_b64 and player.stdin:
                    if player.poll() is not None:
                        return {
                            "spoken": False,
                            "reason": "player_exited",
                            "stderr": _player_stderr(player),
                        }
                    try:
                        player.stdin.write(base64.b64decode(audio_b64))
                        player.stdin.flush()
                    except BrokenPipeError:
                        return {
                            "spoken": False,
                            "reason": "broken_pipe",
                            "stderr": _player_stderr(player),
                        }

                if data.get("data", {}).get("status") == 2:
                    break

            completed_normally = True
            return {
                "spoken": True,
                "text_length": len(text),
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
        except FileNotFoundError:
            return {"spoken": False, "reason": f"player_not_found:{XFYUN_TTS_PLAYER}"}
        except Exception as exc:  # noqa: BLE001
            if _TTS_STOP_EVENT.is_set():
                return {"spoken": False, "reason": "stopped"}
            return {"spoken": False, "reason": "exception", "error": repr(exc)}
        finally:
            with _TTS_STATE_LOCK:
                if _CURRENT_WS is ws:
                    _CURRENT_WS = None
                if _CURRENT_PLAYER is player:
                    _CURRENT_PLAYER = None

            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

            if player is not None:
                try:
                    if player.stdin:
                        player.stdin.close()
                except Exception:
                    pass

                if completed_normally and not _TTS_STOP_EVENT.is_set():
                    # 正常收完讯飞音频后，让 aplay 自然播放完 stdin 缓冲。
                    # 之前这里直接 terminate，会导致短句/长句都可能被截断或无声。
                    try:
                        player.wait(timeout=max(TTS_STOP_TIMEOUT_SECONDS, 10.0))
                    except subprocess.TimeoutExpired:
                        _terminate_player(player, timeout=1.0)
                else:
                    _terminate_player(player, timeout=1.0)
            _TTS_ACTIVE.clear()
            _TTS_STOP_EVENT.clear()


def speak_async(text: str) -> None:
    threading.Thread(target=speak, args=(text,), daemon=True).start()
