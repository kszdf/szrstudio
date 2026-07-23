#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
后处理合成器 v2 (PIL 烧字幕版)
绕开 ffmpeg/libass 中文渲染不可靠的坑，直接用 PIL 在每帧画字幕。
流程:
  1) ffmpeg 抽视频帧为 PNG（image2 muxer，稳定）
  2) 解析 ASS 取 Dialogue 事件（时间+文本）
  3) PIL 在每帧画白字黑边字幕
  4) ffmpeg 把帧序列+音频合成视频
  5) (可选) 拼接品牌片头
用法:
  python finalize_v2_pil.py --video <数字人视频> --ass <字幕> --out <成品>
  --replace-audio <wav>  (测试/补漏用)
  --no-intro             (不加片头)
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path("D:/heygem_data/gpt_sovits")
INTRO = BASE / "covers/intro.mp4"
TMP = BASE / "_tmp_pil"
FRAMES = TMP / "frames"
FONT = str(BASE / "fonts/simhei.ttf")
FPS = 30

# 字幕样式（匹配 build_package.py 生成的 ass）
SUB_SIZE = 42              # 实际视频 720x1280 下的等效字号（ass 64 in 1920 → 42.7）
SUB_BORDER = 4             # 黑边宽度
SUB_MARGIN_BOTTOM = 53     # 底部边距（ass 80 in 1920 → 53.3）


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("  [ERR] 命令失败:", " ".join(cmd[:6]), "...")
        print(r.stderr[-800:])
        sys.exit(1)


def parse_ass(ass_path):
    """解析 ass Dialogue 事件 -> [(start, end, text), ...]
    ass Dialogue 格式: Dialogue: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
    Name 和 Effect 可能为空，用 split(",", 9) 切前 9 个逗号，剩余是 Text（可含逗号）。
    """
    events = []
    for line in Path(ass_path).read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line[len("Dialogue:"):].split(",", 9)
        if len(parts) < 10:
            continue
        start, end = float(parts[1]), float(parts[2])
        text = parts[9].replace(r"\N", "\n").strip()
        events.append((start, end, text))
    return events


def draw_subtitle(img, text):
    """在帧底部居中画白字黑边"""
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT, SUB_SIZE)
    lines = text.split("\n")
    line_h = SUB_SIZE + 8
    total_h = line_h * len(lines)
    W, H = img.size
    y0 = H - SUB_MARGIN_BOTTOM - total_h
    for i, ln in enumerate(lines):
        bbox = draw.textbbox((0, 0), ln, font=font)
        tw = bbox[2] - bbox[0]
        x = (W - tw) // 2
        y = y0 + i * line_h
        # 黑边（多次偏移）
        for dx in range(-SUB_BORDER, SUB_BORDER + 1):
            for dy in range(-SUB_BORDER, SUB_BORDER + 1):
                if dx * dx + dy * dy <= SUB_BORDER * SUB_BORDER:
                    draw.text((x + dx, y + dy), ln, font=font, fill=(0, 0, 0))
        # 白字
        draw.text((x, y), ln, font=font, fill=(255, 255, 255))


def extract_frames(video):
    FRAMES.mkdir(parents=True, exist_ok=True)
    # 清空
    for f in FRAMES.glob("f_*.png"):
        f.unlink()
    cmd = ["ffmpeg", "-y", "-i", str(video), "-vf", f"fps={FPS}", str(FRAMES / "f_%05d.png")]
    run(cmd)
    return sorted(FRAMES.glob("f_*.png"))


def burn_frames(events, frames):
    print(f"  共 {len(frames)} 帧，{len(events)} 条字幕")
    for i, png in enumerate(frames):
        t = i / FPS
        # 找当前时间字幕
        cur = None
        for s, e, txt in events:
            if s <= t < e:
                cur = txt
                break
        if not cur:
            continue
        img = Image.open(png).convert("RGB")
        draw_subtitle(img, cur)
        img.save(png, "PNG")


def compose_video(frames, audio, out, with_audio=True):
    cmd = [
        "ffmpeg", "-y",
        "-r", str(FPS), "-i", str(FRAMES / "f_%05d.png"),
    ]
    if with_audio and audio:
        cmd += ["-i", str(audio)]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if with_audio and audio:
        cmd += ["-c:a", "aac", "-ar", "44100", "-shortest"]
    cmd.append(str(out))
    run(cmd)
    print(f"  合成完成: {out}")


def concat_intro(intro, mid, out):
    fc = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[v0];"
        "[1:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[v1];"
        "[0:a]aresample=44100[a0];[1:a]aresample=44100[a1];"
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
    )
    cmd = [
        "ffmpeg", "-y", "-i", str(intro), "-i", str(mid),
        "-filter_complex", fc,
        "-map", "[v]", "-map", "[a]", "-pix_fmt", "yuv420p", str(out),
    ]
    run(cmd)
    print(f"  片头拼接完成: {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--ass", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--intro", default=str(INTRO))
    ap.add_argument("--no-intro", action="store_true")
    ap.add_argument("--replace-audio", default=None)
    args = ap.parse_args()

    video = Path(args.video)
    ass = Path(args.ass)
    if not video.exists():
        sys.exit(f"视频不存在: {video}")
    if not ass.exists():
        sys.exit(f"字幕不存在: {ass}")

    TMP.mkdir(parents=True, exist_ok=True)

    print(f"\n[1/4] 抽帧 ...")
    frames = extract_frames(video)

    print(f"\n[2/4] 解析字幕 ...")
    events = parse_ass(ass)
    print(f"  字幕事件: {len(events)} 条")
    if events:
        print(f"  时间范围: {events[0][0]:.2f}s - {events[-1][1]:.2f}s")

    print(f"\n[3/4] PIL 烧字幕 ...")
    burn_frames(events, frames)

    print(f"\n[4/4] 合成视频 ...")
    mid = TMP / "mid.mp4"
    compose_video(frames, Path(args.replace_audio) if args.replace_audio else None,
                  mid, with_audio=bool(args.replace_audio))

    if args.no_intro or not Path(args.intro).exists():
        shutil.move(str(mid), str(args.out))
    else:
        concat_intro(Path(args.intro), mid, Path(args.out))
        mid.unlink(missing_ok=True)
    print(f"\n  成品: {args.out}")


if __name__ == "__main__":
    main()
