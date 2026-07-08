#!/usr/bin/env python3
"""Batch benchmark local sherpa SenseVoice ASR through zeroclaw_ws client.

Input manifest formats supported:
  1. TSV/pipe: /abs/path.wav<TAB>reference text
  2. TSV/pipe: utt_id<TAB>/abs/path.wav<TAB>reference text
  3. AISHELL transcript: utt_id reference text
     Use --wav-root to resolve utt_id.wav recursively.
  4. MAGICDATA transcript: UtteranceID<TAB>SpeakerID<TAB>Transcription
     Use --wav-root to resolve UtteranceID under speaker folders.

Example:
  cd <project-root>
  zeroclaw_ws/.venv/bin/python asr/asr_benchmark.py \
    --manifest /path/to/manifest.tsv \
    --sample-size 300 \
    --output ASR_BENCH_300.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
ZEROCLAW_WS_DIR = ROOT / "zeroclaw_ws"
sys.path.insert(0, str(ZEROCLAW_WS_DIR))

from sherpa_ws_asr import SherpaOfflineWebSocketASR  # noqa: E402


@dataclass
class Item:
    utt_id: str
    wav_path: Path
    ref: str


def normalize_text(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[，。！？、；：,.!?;:\"'“”‘’（）()\[\]{}<>《》\-—_]", "", text)
    return text


def edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(
                min(
                    prev[j] + 1,
                    cur[j - 1] + 1,
                    prev[j - 1] + (0 if ca == cb else 1),
                )
            )
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float | None:
    ref_n = normalize_text(ref)
    hyp_n = normalize_text(hyp)
    if not ref_n:
        return None
    return edit_distance(ref_n, hyp_n) / len(ref_n)


def find_wav(root: Path, utt_id: str) -> Path | None:
    direct = root / f"{utt_id}.wav"
    if direct.exists():
        return direct
    matches = list(root.rglob(f"{utt_id}.wav"))
    return matches[0] if matches else None


def parse_manifest(path: Path, wav_root: Path | None = None) -> list[Item]:
    items: list[Item] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts: list[str]
        if "\t" in line:
            parts = [p.strip() for p in line.split("\t") if p.strip()]
            if len(parts) >= 3 and parts[0].lower() == "utteranceid":
                continue
        elif "|" in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
        else:
            # AISHELL style: utt_id text...
            split = line.split(maxsplit=1)
            if len(split) != 2:
                raise ValueError(f"无法解析 manifest 第 {line_no} 行: {line}")
            utt_id, ref = split
            if wav_root is None:
                raise ValueError("AISHELL 样式 manifest 需要提供 --wav-root")
            wav = find_wav(wav_root, utt_id)
            if wav is None:
                continue
            items.append(Item(utt_id=utt_id, wav_path=wav, ref=ref))
            continue

        if len(parts) == 2:
            wav_path = Path(parts[0]).expanduser()
            utt_id = wav_path.stem
            ref = parts[1]
        elif len(parts) >= 3:
            maybe_wav = Path(parts[1]).expanduser()
            if parts[0].lower().endswith(".wav") and not ("/" in parts[1] or parts[1].lower().endswith(".wav")):
                # MAGICDATA: wav filename, speaker id, transcript.
                if wav_root is None:
                    raise ValueError("MAGICDATA 样式 manifest 需要提供 --wav-root")
                utt_id = Path(parts[0]).stem
                wav = wav_root / parts[1] / parts[0]
                if not wav.exists():
                    wav = find_wav(wav_root, utt_id)
                if wav is None:
                    continue
                items.append(Item(utt_id=utt_id, wav_path=wav, ref="\t".join(parts[2:])))
                continue
            utt_id = parts[0]
            wav_path = maybe_wav
            ref = "\t".join(parts[2:])
        else:
            raise ValueError(f"无法解析 manifest 第 {line_no} 行: {line}")

        if not wav_path.is_absolute() and wav_root is not None:
            wav_path = wav_root / wav_path
        if wav_path.exists():
            items.append(Item(utt_id=utt_id, wav_path=wav_path, ref=ref))
    return items


def read_wav_pcm16(path: Path) -> tuple[bytes, int, float]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frames_count = wav.getnframes()
        frames = wav.readframes(frames_count)
    if channels != 1:
        raise ValueError(f"只支持单声道 wav: {path} channels={channels}")
    if sample_width != 2:
        raise ValueError(f"只支持 16-bit PCM wav: {path} sample_width={sample_width}")
    return frames, sample_rate, frames_count / float(sample_rate)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * p
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch ASR benchmark")
    parser.add_argument("--manifest", required=True, help="manifest/transcript path")
    parser.add_argument("--wav-root", default="", help="wav root for relative paths or AISHELL transcript")
    parser.add_argument("--sample-size", type=int, default=300, help="number of utterances to sample")
    parser.add_argument("--seed", type=int, default=20260706)
    parser.add_argument("--output", default="ASR_BENCH_RESULTS.jsonl")
    parser.add_argument("--quiet", action="store_true", help="do not print every row to stdout")
    parser.add_argument("--progress-every", type=int, default=25, help="print progress every N rows when --quiet is used")
    parser.add_argument("--sleep-between", type=float, default=0.0, help="seconds to wait between utterances")
    args = parser.parse_args()

    manifest = Path(args.manifest).expanduser()
    wav_root = Path(args.wav_root).expanduser() if args.wav_root else None
    output = Path(args.output).expanduser()

    items = parse_manifest(manifest, wav_root=wav_root)
    if not items:
        raise SystemExit("manifest 中没有可用音频")

    random.seed(args.seed)
    if args.sample_size > 0 and len(items) > args.sample_size:
        items = random.sample(items, args.sample_size)

    asr = SherpaOfflineWebSocketASR()
    asr.start_server_if_needed()

    output.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    with output.open("w", encoding="utf-8") as fp:
        for index, item in enumerate(items, start=1):
            try:
                pcm, sample_rate, audio_sec = read_wav_pcm16(item.wav_path)
                started = time.perf_counter()
                hyp = asr.transcribe_pcm16(pcm, sample_rate=sample_rate)
                elapsed = time.perf_counter() - started
                row = {
                    "index": index,
                    "utt_id": item.utt_id,
                    "wav": str(item.wav_path),
                    "audio_sec": round(audio_sec, 3),
                    "elapsed_sec": round(elapsed, 3),
                    "rtf": round(elapsed / audio_sec, 4) if audio_sec > 0 else None,
                    "ref": item.ref,
                    "hyp": hyp,
                    "cer": cer(item.ref, hyp),
                    "ok": True,
                }
            except Exception as exc:  # noqa: BLE001
                row = {
                    "index": index,
                    "utt_id": item.utt_id,
                    "wav": str(item.wav_path),
                    "ref": item.ref,
                    "ok": False,
                    "error": repr(exc),
                }
            rows.append(row)
            fp.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            fp.flush()
            if args.quiet:
                if args.progress_every > 0 and (index == 1 or index % args.progress_every == 0 or index == len(items)):
                    print(f"PROGRESS {index}/{len(items)} ok={row.get('ok')}", flush=True)
            else:
                print(json.dumps(row, ensure_ascii=False, default=str), flush=True)
            if args.sleep_between > 0 and index < len(items):
                time.sleep(args.sleep_between)

    valid = [r for r in rows if r.get("ok")]
    elapsed_values = [float(r["elapsed_sec"]) for r in valid]
    rtf_values = [float(r["rtf"]) for r in valid if r.get("rtf") is not None]
    cer_values = [float(r["cer"]) for r in valid if r.get("cer") is not None]
    summary = {
        "manifest": str(manifest),
        "wav_root": str(wav_root) if wav_root else "",
        "sample_count": len(rows),
        "success_count": len(valid),
        "failed_count": len(rows) - len(valid),
        "avg_elapsed_sec": round(statistics.mean(elapsed_values), 4) if elapsed_values else None,
        "p50_elapsed_sec": round(percentile(elapsed_values, 0.50), 4) if elapsed_values else None,
        "p95_elapsed_sec": round(percentile(elapsed_values, 0.95), 4) if elapsed_values else None,
        "avg_rtf": round(statistics.mean(rtf_values), 4) if rtf_values else None,
        "p95_rtf": round(percentile(rtf_values, 0.95), 4) if rtf_values else None,
        "avg_cer": round(statistics.mean(cer_values), 4) if cer_values else None,
        "output": str(output),
    }
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False, default=str))
    with output.with_suffix(output.suffix + ".summary.json").open("w", encoding="utf-8") as fp:
        json.dump(summary, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
