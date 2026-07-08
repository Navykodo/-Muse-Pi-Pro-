from __future__ import annotations

import subprocess
import time
from pathlib import Path

from config import (
    AFTER_WAKE_REPLY_DELAY_SECONDS,
    ENABLE_WAKE_REPLY,
    WAKE_REPLY_DEVICE,
    WAKE_REPLY_PLAYER,
    WAKE_REPLY_WAV,
)


def play_wake_reply() -> bool:
    """播放唤醒提示音“我在”。"""
    if not ENABLE_WAKE_REPLY:
        return True

    wav_path = Path(WAKE_REPLY_WAV).expanduser()
    if not wav_path.exists():
        print(f"[wake-reply] 提示音文件不存在: {wav_path}")
        return False

    player = WAKE_REPLY_PLAYER.strip().lower()
    if player == "aplay":
        cmd = ["aplay", "-q", "-D", WAKE_REPLY_DEVICE, str(wav_path)]
    else:
        cmd = [WAKE_REPLY_PLAYER, str(wav_path)]

    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if proc.returncode != 0:
        print(f"[wake-reply] 播放提示音失败，返回码: {proc.returncode}")
        return False

    if AFTER_WAKE_REPLY_DELAY_SECONDS > 0:
        time.sleep(AFTER_WAKE_REPLY_DELAY_SECONDS)
    return True
