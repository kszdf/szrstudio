# -*- coding: utf-8 -*-
"""i2v 动效对比版: 5 段 i2v 动效 + 5 句旁白逐幕对齐 → 完整成片。
用法: python _i2v_final.py --clips 幕1..幕5 --out out.mp4
旁白: 与 08f 讲解式注销三步一致(5句)。
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
FFMPEG = "ffmpeg"
W, H, FPS = 1080, 1920, 30

NARRATIONS = [
    "公司不经营了想注销，其实就三步。",
    "第一步，账结清，该补的税补掉，该报的报表报完。",
    "第二步，公示四十五天，公告债权债务，没人来找麻烦。",
    "第三步，注销登记，税务注销完再做工商注销。",
    "拿到注销通知书，公司才算真正注销完。",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", required=True, help="逗号分隔 5 段 i2v mp4")
    ap.add_argument("--voice", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    clips = [s.strip() for s in args.clips.split(",") if s.strip()]
    assert len(clips) == 5, f"需 5 段动效, 实际 {len(clips)}"

    from model_providers import ensure_env
    ensure_env()
    from qwen_tts import synth

    tmp = Path(tempfile.mkdtemp(prefix="i2v_final_"))
    # 1) 每句旁白 TTS
    print("[1/3] 配音 5 句 ...")
    voices = []
    for i, ntxt in enumerate(NARRATIONS):
        p = str(tmp / f"v{i}.wav")
        synth(ntxt, args.voice, p, speech_rate=0.90, pitch_rate=1.0, volume=50)
        voices.append(p)
        print(f"  句{i+1}: {ntxt[:20]}...")

    # 2) 每幕: 放大 i2v 到 1080x1920 + 接该句配音, 独立成段
    print("[2/3] 逐幕对齐合成 ...")
    segs = []
    for i, c in enumerate(clips):
        seg = tmp / f"seg{i}.mp4"
        # i2v 720x1280 -> 1080x1920 (cover 放大裁切)
        subprocess.run([FFMPEG, "-y", "-i", c, "-vf",
                        f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}",
                        "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "19",
                        str(tmp / f"v{i}_video.mp4")], capture_output=True)
        vdur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                     "-of", "default=noprint_wrappers=1:nokey=1",
                                     str(tmp / f"v{i}_video.mp4")],
                                    capture_output=True, text=True).stdout.strip())
        adur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                     "-of", "default=noprint_wrappers=1:nokey=1", voices[i]],
                                    capture_output=True, text=True).stdout.strip())
        # 视频时长不足则尾部补帧(慢放最后帧), 音频不足则尾部静音补齐
        dur = max(vdur, adur)
        subprocess.run([FFMPEG, "-y", "-i", str(tmp / f"v{i}_video.mp4"), "-i", voices[i],
                        "-filter_complex",
                        f"[0:v]tpad=stop_mode=clone:stop_duration={max(0,adur-vdur)}[v];"
                        f"[1:a]apad=pad_dur={max(0,vdur-adur)}[a]",
                        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "fast",
                        "-crf", "19", "-c:a", "aac", "-shortest", str(seg)], capture_output=True)
        segs.append(str(seg))
        print(f"  幕{i+1}: 视频{vdur:.1f}s 配音{adur:.1f}s -> {dur:.1f}s")

    # 3) concat
    print("[3/3] 拼接成片 ...")
    listf = tmp / "list.txt"
    with open(listf, "w", encoding="utf-8") as f:
        for s in segs:
            f.write(f"file '{s.replace(chr(92), '/')}'\n")
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
                    "-c", "copy", args.out], capture_output=True)
    print(f"成品: {args.out}")


if __name__ == "__main__":
    main()
