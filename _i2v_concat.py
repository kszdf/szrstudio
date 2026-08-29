# -*- coding: utf-8 -*-
"""拼接 i2v 分幕动效 → 完整漫剧对比版。
5 段 720x1280 动效 + 原讲解式配音 → 1080x1920 成片。
用法: python _i2v_concat.py <幕1..幕5.mp4...> <voice.wav> <out.mp4>
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
W, H, FPS = 1080, 1920, 30


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", nargs="+", help="5 段 i2v mp4")
    ap.add_argument("--voice", default="", help="配音 wav(可选)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    tmp = Path(tempfile.mkdtemp(prefix="i2v_concat_"))
    scaled = []
    for i, c in enumerate(args.clips):
        p = tmp / f"s{i}.mp4"
        # 放大到 1080x1920 (cover 裁切) + 统一 30fps + 静音原声(保留占位)
        subprocess.run([FFMPEG, "-y", "-i", c, "-vf",
                        f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}",
                        "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "19", str(p)],
                       capture_output=True)
        scaled.append(str(p))
        print(f"  幕{i+1}: {c} -> {p.name}")

    # concat
    listf = tmp / "list.txt"
    with open(listf, "w", encoding="utf-8") as f:
        for p in scaled:
            f.write(f"file '{p.replace(chr(92), '/')}'\n")
    concat = tmp / "concat.mp4"
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
                    "-c", "copy", str(concat)], capture_output=True)
    print("  拼接完成")

    if args.voice and Path(args.voice).exists():
        subprocess.run([FFMPEG, "-y", "-i", str(concat), "-i", args.voice,
                        "-c:v", "copy", "-c:a", "aac", "-shortest", args.out],
                       capture_output=True)
        print(f"  合成(带配音): {args.out}")
    else:
        subprocess.run([FFMPEG, "-y", "-i", str(concat), "-c", "copy", args.out],
                       capture_output=True)
        print(f"  合成(无配音): {args.out}")


if __name__ == "__main__":
    main()
