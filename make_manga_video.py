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

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

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
    """画面下部(角色区下方): 步骤流程卡(序号圆+卡片+箭头连接, 当前高亮/完成绿勾/未到灰)。
    2026-08-29 样式改版: 明亮卡片(白底+深字)+彩色序号圆, 解决"画面太暗/步骤卡不醒目"。"""
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
        col = (255, 180, 40) if state == 1 else ((30, 190, 120) if state == 2 else (150, 158, 170))
        cy = y + card_h // 2
        # 序号圆(彩色实心 + 白字)
        d.ellipse([140 - 46, cy - 46, 140 + 46, cy + 46], fill=col)
        d.ellipse([140 - 46, cy - 46, 140 + 46, cy + 46], outline=(255, 255, 255, 210), width=5)
        d.text((140, cy), str(i + 1), font=font(52), fill=(255, 255, 255), anchor="mm")
        # 卡片(白底 + 彩色描边 + 深色文字) + 柔和浅阴影
        d.rounded_rectangle([224, y + 8, W - 44, y + card_h], radius=22,
                            fill=(168, 178, 196, 140))
        d.rounded_rectangle([220, y + 2, W - 48, y + card_h - 6], radius=22,
                            outline=col, width=6 if state else 3,
                            fill=(255, 253, 248, 252) if state else (240, 243, 248, 240))
        if state == 1:
            d.rounded_rectangle([220, y + 2, W - 48, y + card_h - 6], radius=22,
                                outline=(255, 190, 60), width=6)
            d.rounded_rectangle([232, y + 14, W - 60, y + card_h - 18], radius=14,
                                outline=(255, 210, 110), width=2)
        # 文字(超宽自动缩字号防溢出)
        fsize = 46
        while fsize > 26 and d.textlength(st, font=font(fsize)) > (W - 48 - 256 - 60):
            fsize -= 2
        d.text((256, cy), st, font=font(fsize), fill=(30, 38, 52) if state else (115, 123, 136),
               anchor="lm", stroke_width=2, stroke_fill=(255, 255, 255))
        if state == 2:
            d.line([(W - 132, cy - 20), (W - 112, cy), (W - 78, cy - 38)],
                   fill=(30, 190, 120), width=11)
            d.line([(W - 132, cy - 20), (W - 112, cy), (W - 78, cy - 38)],
                   fill=(255, 255, 255), width=4)
        if i < n - 1:
            d.line([(140, cy + card_h // 2 - 4), (140, y + card_h + 10)],
                   fill=(150, 160, 175), width=8)
            d.polygon([(140 - 11, y + card_h + 6), (140 + 11, y + card_h + 6), (140, y + card_h + 20)],
                      fill=(150, 160, 175))
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
            # 2026-08-29 用户反馈: 画面太暗 → 全片轻微提亮(AI生图普遍偏暗)
            frame = ImageEnhance.Brightness(frame).enhance(1.08)
            if steps:
                # 讲解式: 角色区取上部58%(完整角色) + 提亮, 下部浅色渐变承接 + 步骤流程卡
                # 2026-08-29 用户反馈 1'3: 画面太暗/步骤卡样式不好 → 提亮画面 + 亮色卡片
                top = frame.crop((0, 0, W, int(H * 0.58)))
                # 角色图底部 140px 柔化过渡进浅色渐变, 避免硬边
                top = top.convert("RGBA")
                fade = Image.new("RGBA", (W, 140), (250, 246, 236, 0))
                df = ImageDraw.Draw(fade)
                for yy in range(140):
                    df.line([(0, yy), (W, yy)], fill=(250, 246, 236, int(255 * yy / 140)))
                top.alpha_composite(fade, (0, top.height - 140))
                top = top.convert("RGB")
                top = ImageEnhance.Brightness(top).enhance(1.10)
                top = ImageEnhance.Contrast(top).enhance(1.05)
                top = ImageEnhance.Color(top).enhance(1.06)
                grad_h = H - int(H * 0.58)
                grad = Image.new("RGB", (W, grad_h), (250, 246, 236))
                dg = ImageDraw.Draw(grad)
                for yy in range(grad.height):
                    tt = yy / grad.height
                    c = tuple(int(a + (b - a) * tt) for a, b in zip((250, 246, 236), (222, 236, 250)))
                    dg.line([(0, yy), (W, yy)], fill=c)
                canvas = Image.new("RGB", (W, H))
                canvas.paste(top, (0, 0))
                canvas.paste(grad, (0, int(H * 0.58)))
                frame = canvas
                frame = add_steps(frame, steps, min(i, len(steps) - 1))
            # 幕间淡入
            if fi < int(TRANS * FPS):
                a = fi / (TRANS * FPS)
                base = (240, 242, 248) if steps else (8, 10, 16)
                frame = Image.blend(Image.new("RGB", (W, H), base), frame, a)
            # 2026-08-29 用户要求: 漫剧去掉语音字幕(语音已讲解清楚, 字幕会遮挡下方步骤卡/画面内容)
            frame.save(frames_dir / f"f_{gidx:05d}.png")
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
