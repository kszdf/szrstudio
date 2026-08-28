#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_diagram_video.py — 动态图解动画（v0 样本）

把财税逻辑画成「流程图动画」：卡片/箭头/警示 随配音进度逐步亮起，
数字与法条由代码精确绘制（不依赖 AI 生图 → 内容和讲解永远对得上）。

用法:
  D:/heygem/py310/Scripts/python.exe make_diagram_video.py \
      --text "讲解稿" --voice <voice_id> --out out.mp4 --title 公转私是高压线

样本步骤模板在 DIAGRAM_STEPS（按讲解句数自动分配）。
"""
import argparse
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
FFMPEG = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"
FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")
W, H, FPS = 1080, 1920, 30

from PIL import Image, ImageDraw, ImageFont

_F = {}

def font(size):
    if size not in _F:
        _F[size] = ImageFont.truetype(str(BASE / "fonts/simhei.ttf"), size)
    return _F[size]

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

# ============================================================
# 图解分镜（公转私 → 资金混同 → 无限责任）
# step: text 文案 / kind: card|arrow|warn|good / color 主色
# ============================================================
DIAGRAM_STEPS = [
    {"text": "公司账户", "kind": "card", "color": (86, 156, 255), "y": 0.24},
    {"text": "转出", "kind": "arrow", "color": (255, 200, 60), "y": 0.335},
    {"text": "个人卡", "kind": "card", "color": (255, 156, 86), "y": 0.40},
    {"text": "被认定资金混同", "kind": "warn", "color": (244, 63, 63), "y": 0.545},
    {"text": "有限责任 → 无限责任", "kind": "warn", "color": (244, 63, 63), "y": 0.645},
    {"text": "合规：报销走流程 · 借款按期还", "kind": "good", "color": (16, 185, 129), "y": 0.80},
]

CARD_W, CARD_H = 760, 110

def draw_step(img, step, revealed):
    """revealed: 0=未到(暗) 1=当前(亮) 2=已完成(保留)"""
    d = ImageDraw.Draw(img)
    cx = W // 2
    y = int(H * step["y"])
    col = step["color"]
    if revealed == 0:
        col = (70, 78, 92)
    alpha = 255
    kind = step["kind"]
    if kind == "arrow":
        d.line([(cx - 14, y - 26), (cx + 14, y - 26), (cx, y + 26), (cx - 14, y - 26)],
               fill=col, width=8)
        return
    # 卡片 / 警示 / 合规
    if kind == "warn":
        fill, outline, wd = (col[0], col[1], col[2], 40), col, 5
    elif kind == "good":
        fill, outline, wd = (col[0], col[1], col[2], 40), col, 5
    else:
        fill, outline, wd = (26, 34, 50), col, 4
    d.rounded_rectangle([cx - CARD_W // 2, y - CARD_H // 2, cx + CARD_W // 2, y + CARD_H // 2],
                        radius=26, outline=outline, width=wd, fill=fill)
    # 文本（自动适配字号）
    size = 52
    f = font(size)
    while d.textlength(step["text"], font=f) > CARD_W - 60 and size > 34:
        size -= 4
        f = font(size)
    d.text((cx, y), step["text"], font=f, fill=(255, 255, 255) if revealed != 0 else (150, 152, 160),
           anchor="mm", stroke_width=4, stroke_fill=(0, 0, 0))
    # 当前步骤: 外圈脉冲
    if revealed == 1:
        r = 1.0 + 0.05 * math.sin(time.time() * 3)
        d.ellipse([cx - CARD_W // 2 - 10, y - CARD_H // 2 - 10, cx + CARD_W // 2 + 10, y + CARD_H // 2 + 10],
                  outline=col + (90,), width=4)


def build_timeline(sents, voice):
    """逐句 TTS → (wav_path, start, end) 时间轴。"""
    from model_providers import ensure_env
    ensure_env()
    from qwen_tts import synth
    tmp = Path(tempfile.mkdtemp(prefix="diag_"))
    tl = []
    cur = 0.0
    for i, s in enumerate(sents):
        p = str(tmp / f"t{i}.wav")
        synth(s, voice, p, speech_rate=0.94, pitch_rate=1.0, volume=50)
        dur = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                                    "-of", "default=noprint_wrappers=1:nokey=1", p],
                                   capture_output=True, text=True).stdout.strip())
        tl.append((p, cur, cur + dur))
        cur += dur + 0.35   # 句间停顿
    return tl, tmp


def render_frames(sents, tl, steps, out_dir):
    """逐帧渲染: 每句一个步骤(超过句数的步骤在结尾补齐)。"""
    n = int(tl[-1][2] * FPS)
    sub = "".join(sents)
    for fi in range(n):
        t = fi / FPS
        img = Image.new("RGB", (W, H), (16, 20, 30))
        d = ImageDraw.Draw(img)
        # 顶部标题
        d.text((W // 2, 130), "公转私是高压线", font=font(66), fill=(255, 219, 120),
               anchor="mm", stroke_width=4, stroke_fill=(0, 0, 0))
        d.line([(W // 2 - 140, 180), (W // 2 + 140, 180)], fill=(255, 219, 120), width=4)
        # 步骤(按时间)
        cur = 0
        for k, (p, s0, s1) in enumerate(tl):
            if t >= s0:
                cur = k
        for i, st in enumerate(steps):
            if i < cur:
                draw_step(img, st, 2)      # 已完成
            elif i == cur:
                draw_step(img, st, 1)      # 当前
            else:
                draw_step(img, st, 0)      # 未到
        # 底部字幕(卡拉OK: 当前句亮, 其余暗)
        y_sub = int(H * 0.90)
        f_sub = font(44)
        # 按句显示当前句
        cur_sent = sents[cur] if cur < len(sents) else ""
        d.rounded_rectangle([40, y_sub - 40, W - 40, y_sub + 52], radius=20,
                            fill=(8, 12, 22, 195), outline=(255, 219, 120) + (60,), width=2)
        lines = wrap(d, cur_sent, f_sub, W - 140)
        yy = y_sub + 6
        for ln in lines[:2]:
            d.text((W // 2, yy), ln, font=f_sub, fill=(255, 219, 120),
                   anchor="mm", stroke_width=3, stroke_fill=(0, 0, 0))
            yy += 46
        img.save(f"{out_dir}/f_{fi:05d}.png")
        if fi % 300 == 0:
            print(f"  渲染 {int(100 * fi / n)}%")
    print("  渲染 100%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--voice", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="公转私是高压线")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    from qwen_tts import _split_sentences
    sents = _split_sentences(args.text)
    if not sents:
        sys.exit("解析不到句子")
    print(f"[1/3] 配音 {len(sents)} 句 ...")
    tl, tmp = build_timeline(sents, args.voice)
    # 步骤与句子对齐: 句数>=6 用全部6步; 少于则截取前 N 步(每句配一步, 节奏清晰)
    steps = DIAGRAM_STEPS[: max(1, min(len(sents), len(DIAGRAM_STEPS)))]

    out_dir = Path(tempfile.mkdtemp(prefix="diagf_"))
    print(f"[2/3] 渲染 {int(tl[-1][2] * FPS)} 帧 ...")
    render_frames(sents, tl, steps, str(out_dir))

    print("[3/3] ffmpeg 合成 ...")
    listf = tmp / "concat.txt"
    with open(listf, "w", encoding="utf-8") as f:
        for p, _, _ in tl:
            f.write(f"file '{p.replace(chr(92), '/')}'\n")
    audio = str(tmp / "all.wav")
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
                    "-c", "copy", audio], capture_output=True, text=True)
    out = args.out
    subprocess.run([FFMPEG, "-y", "-r", str(FPS), "-i", str(out_dir / "f_%05d.png"),
                    "-i", audio, "-c:v", "libx264", "-crf", "19", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", out], capture_output=True, text=True)
    print(f"成品: {out}")


if __name__ == "__main__":
    main()
