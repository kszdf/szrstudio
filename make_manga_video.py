#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_manga_video.py — AI 漫剧成片管线 v1

分镜图(固定角色) + 旁白配音 → 每幕图片动效(Ken Burns 慢镜) + 底部字幕 + 淡入转场 → mp4。
内容由 LLM 分镜 + AI 生图 + 代码动效构成, 角色一致性用「固定角色描述」保证。

用法:
  python make_manga_video.py --shots "s1图,s2图,..." --narration "旁白1|旁白2|..." \
      --voice <voice_id> --out out.mp4 [--title 公转私是高压线]
"""
import argparse
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
FFMPEG = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"
FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")
W, H, FPS = 1080, 1920, 30
TRANS = 0.6      # 幕间淡入时长

from PIL import Image, ImageDraw, ImageFont

_F = {}
def font(size):
    if size not in _F:
        _F[size] = ImageFont.truetype(str(BASE / "fonts/simhei.ttf"), size)
    return _F[size]

def cover_resize(img, tw, th):
    iw, ih = img.size
    scale = max(tw / iw, th / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    return img.crop(((nw - tw) // 2, (nh - th) // 2, (nw - tw) // 2 + tw, (nh - th) // 2 + th))

def wrap(d, text, f, max_w):
    lines, cur = [], ""
    for ch in text:
        test = cur + ch
        if d.textlength(test, font=f) > max_w and cur:
            lines.append(cur); cur = ch
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def kenburns(shot_path, out_frames, secs):
    """每幕: 慢速缩放+平移(Ken Burns), 导出帧序列。"""
    im = cover_resize(Image.open(shot_path).convert("RGB"), W, H)
    n = int(secs * FPS)
    # 缩放 1.0 -> 1.10, 平移 0 -> 40px
    for i in range(n):
        p = i / n
        scale = 1.0 + 0.10 * p
        dx = int(40 * p)
        dy = int(25 * p)
        nw, nh = int(W * scale), int(H * scale)
        resized = im.resize((nw, nh), Image.LANCZOS)
        left = (nw - W) // 2 - dx
        top = (nh - H) // 2 - dy
        left = max(0, min(nw - W, left))
        top = max(0, min(nh - H, top))
        frame = resized.crop((left, top, left + W, top + H))
        out_frames.append(frame)


def add_subtitle(frame, text, show_ratio=1.0):
    """底部字幕: 暗底条 + 白字(卡拉OK: 念到的字亮黄, 未念暗灰)。"""
    img = frame.convert("RGBA")
    d = ImageDraw.Draw(img)
    f = font(46)
    max_w = W - 140
    lines = wrap(d, text, f, max_w)
    # 底条
    y0 = H - 190
    d.rounded_rectangle([40, y0, W - 40, H - 60], radius=22, fill=(8, 12, 22, 200))
    total_chars = max(1, len(text))
    shown = int(show_ratio * total_chars)
    yy = y0 + 34
    cum = 0
    for ln in lines:
        w = d.textlength(ln, font=f)
        x = (W - w) / 2
        # 逐字亮暗
        for ch in ln:
            done = cum < shown
            col = (255, 219, 120) if done else (150, 152, 160)
            d.text((x, yy), ch, font=f, fill=col, anchor="lm",
                   stroke_width=4, stroke_fill=(0, 0, 0))
            x += d.textlength(ch, font=f)
            cum += 1
        yy += 54
    return img.convert("RGB")


def add_steps(img, steps, cur_idx):
    """画面下部(角色区下方): 步骤流程卡(序号圆+文字+箭头连接, 当前高亮/完成绿勾/未到暗显)。
    2026-08-28 用户要求: 讲解式过程要在画面中展示, 不能只在底部字幕。"""
    img = img.convert("RGBA")
    d = ImageDraw.Draw(img)
    n = len(steps)
    area_y0, area_y1 = 1210, 1830
    card_h = int((area_y1 - area_y0) / max(1, n))
    y = area_y0
    for i, st in enumerate(steps):
        if i == cur_idx:
            state = 1
        elif i < cur_idx:
            state = 2
        else:
            state = 0
        col = (255, 200, 60) if state == 1 else ((16, 185, 129) if state == 2 else (82, 90, 104))
        cy = y + card_h // 2
        # 序号圆
        d.ellipse([150 - 32, cy - 32, 150 + 32, cy + 32],
                  fill=col if state else (58, 64, 76))
        d.text((150, cy), str(i + 1), font=font(40), fill=(255, 255, 255), anchor="mm")
        # 卡片
        d.rounded_rectangle([216, y + 5, W - 56, y + card_h - 5], radius=18,
                            outline=col, width=4 if state else 2,
                            fill=(20, 28, 45, 210) if state else (20, 24, 34, 150))
        d.text((242, cy), st, font=font(34), fill=(255, 255, 255) if state else (150, 152, 160),
               anchor="lm")
        if state == 2:
            d.line([(W - 118, cy - 16), (W - 102, cy), (W - 76, cy - 30)],
                   fill=(16, 185, 129), width=8)
        if i < n - 1:
            d.line([(150, cy + card_h // 2 - 4), (150, y + card_h + 6)],
                   fill=(110, 120, 135), width=7)
        y += card_h
    return img.convert("RGB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", required=True, help="逗号分隔分镜图路径")
    ap.add_argument("--narration", required=True, help="竖线|分隔的每幕旁白")
    ap.add_argument("--voice", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--steps", default="", help="讲解式步骤清单(逗号分隔): 画面下部逐步展示")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]

    shots = [s.strip() for s in args.shots.split(",") if s.strip()]
    narrs = [s.strip() for s in args.narration.split("|") if s.strip()]
    if len(narrs) <= 1:
        # 未用 | 分隔 → 自动按句切分(每幕一句)
        from qwen_tts import _split_sentences
        narrs = [s for s in _split_sentences(args.narration) if s.strip()]
    if len(shots) < len(narrs):
        print(f"[警告] 分镜 {len(shots)} < 旁白 {len(narrs)}, 按旁白循环分镜")
    if not shots or not narrs:
        sys.exit("需提供分镜与旁白")

    # 1) TTS 每幕旁白
    from model_providers import ensure_env
    ensure_env()
    from qwen_tts import synth
    print(f"[1/4] 配音 {len(narrs)} 句 ...")
    tmp = Path(tempfile.mkdtemp(prefix="manga_"))
    tl = []
    cur = 0.0
    for i, ntxt in enumerate(narrs):
        p = str(tmp / f"n{i}.wav")
        synth(ntxt, args.voice, p, speech_rate=0.95, pitch_rate=1.0, volume=50)
        dur = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                                    "-of", "default=noprint_wrappers=1:nokey=1", p],
                                   capture_output=True, text=True).stdout.strip())
        tl.append((p, cur, cur + dur))
        cur += dur + 0.3
    total_sec = tl[-1][2]

    # 2) 渲染每幕帧
    print(f"[2/4] 渲染 {int(total_sec * FPS)} 帧 ...")
    frames_dir = Path(tempfile.mkdtemp(prefix="mangaf_"))
    gidx = 0
    for i, ntxt in enumerate(narrs):
        shot = shots[i % len(shots)]
        s0, s1 = tl[i][1], tl[i][2]
        secs = s1 - s0
        frames = []
        kenburns(shot, frames, secs)
        n = len(frames)
        for fi, frame in enumerate(frames):
            ratio = fi / n
            if steps:
                # 讲解式: 角色区取上部62%, 下部深色承接 + 步骤流程卡
                top = frame.crop((0, 0, W, int(H * 0.62)))
                grad = Image.new("RGB", (W, H - int(H * 0.62)), (18, 24, 36))
                dg = ImageDraw.Draw(grad)
                for yy in range(grad.height):
                    tt = yy / grad.height
                    c = tuple(int(a + (b - a) * tt) for a, b in zip((18, 24, 36), (10, 14, 22)))
                    dg.line([(0, yy), (W, yy)], fill=c)
                canvas = Image.new("RGB", (W, H))
                canvas.paste(top, (0, 0))
                canvas.paste(grad, (0, int(H * 0.62)))
                frame = canvas
                frame = add_steps(frame, steps, min(i, len(steps) - 1))
            # 幕间淡入
            if fi < int(TRANS * FPS):
                a = fi / (TRANS * FPS)
                frame = Image.blend(Image.new("RGB", (W, H), (8, 10, 16)), frame, a)
            framed = add_subtitle(frame, ntxt, ratio)
            framed.save(frames_dir / f"f_{gidx:05d}.png")
            gidx += 1
        print(f"  幕{i+1}: {secs:.1f}s ({n}帧)")
    print("  渲染完成")

    # 3) 合成: 帧序列 + 音频
    print("[3/4] ffmpeg 合成 ...")
    listf = tmp / "concat.txt"
    with open(listf, "w", encoding="utf-8") as f:
        for p, _, _ in tl:
            f.write(f"file '{p.replace(chr(92), '/')}'\n")
    audio = str(tmp / "all.wav")
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
                    "-c", "copy", audio], capture_output=True, text=True)
    subprocess.run([FFMPEG, "-y", "-framerate", str(FPS), "-i", str(frames_dir / "f_%05d.png"),
                    "-i", audio, "-c:v", "libx264", "-crf", "19", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", args.out], capture_output=True, text=True)
    print(f"成品: {args.out}")


if __name__ == "__main__":
    main()
