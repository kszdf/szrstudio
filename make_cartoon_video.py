#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_cartoon_video.py — 卡通图解动画 v1 样本

Q版卡通角色(代码绘制, 表情随讲解变化) + 卡通图标(钱袋/银行卡/炸弹/勾) + 气泡对话框
+ 弹入/抖动动画。内容仍是代码精确控制(不依赖AI生图 → 与讲解100%同步)。
"""
import argparse
import math
import os
import random
import subprocess
import sys
import tempfile
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
# Q版卡通角色（代码绘制, 表情可切换）
# ============================================================
def draw_boss(img, cx, cy, scale=1.0, emotion="normal", alpha=255):
    """Q版中年男老板: 圆头+小身体+西装领带。emotion: normal/confused/shocked/happy"""
    d = ImageDraw.Draw(img, "RGBA")
    s = scale
    # 头
    d.ellipse([cx - 130*s, cy - 170*s, cx + 130*s, cy + 90*s], fill=(255, 224, 189, alpha), outline=(120, 80, 40, alpha), width=6)
    # 头发
    d.arc([cx - 120*s, cy - 165*s, cx + 120*s, cy - 40*s], 200, 340, fill=(60, 50, 40, alpha), width=22)
    # 身体
    d.rounded_rectangle([cx - 150*s, cy + 90*s, cx + 150*s, cy + 320*s], radius=70,
                        fill=(70, 110, 200, alpha), outline=(40, 70, 140, alpha), width=6)
    # 领带
    d.polygon([(cx, cy + 105*s), (cx - 22*s, cy + 160*s), (cx + 22*s, cy + 160*s)],
              fill=(220, 60, 60, alpha))
    # 眼睛
    eye_off = 48*s
    if emotion in ("shocked", "confused"):
        d.ellipse([cx - eye_off - 18*s, cy - 60*s, cx - eye_off + 18*s, cy - 20*s], fill=(255,255,255,alpha))
        d.ellipse([cx + eye_off - 18*s, cy - 60*s, cx + eye_off + 18*s, cy - 20*s], fill=(255,255,255,alpha))
        d.ellipse([cx - eye_off - 8*s, cy - 48*s, cx - eye_off + 8*s, cy - 32*s], fill=(30,30,40,alpha))
        d.ellipse([cx + eye_off - 8*s, cy - 48*s, cx + eye_off + 8*s, cy - 32*s], fill=(30,30,40,alpha))
    else:
        d.ellipse([cx - eye_off - 16*s, cy - 55*s, cx - eye_off + 16*s, cy - 23*s], fill=(30,30,40,alpha))
        d.ellipse([cx + eye_off - 16*s, cy - 55*s, cx + eye_off + 16*s, cy - 23*s], fill=(30,30,40,alpha))
    # 嘴
    mx = cx + 0*s
    if emotion == "happy":
        d.arc([mx - 40*s, cy - 10*s, mx + 40*s, cy + 50*s], 20, 160, fill=(120, 60, 40, alpha), width=8)
    elif emotion == "shocked":
        d.ellipse([mx - 18*s, cy + 2*s, mx + 18*s, cy + 40*s], fill=(120, 60, 40, alpha))
    elif emotion == "confused":
        d.line([(mx - 35*s, cy + 30*s), (mx + 35*s, cy + 20*s)], fill=(120, 60, 40, alpha), width=8)
    else:
        d.line([(mx - 35*s, cy + 30*s), (mx + 35*s, cy + 30*s)], fill=(120, 60, 40, alpha), width=8)
    # 手(小圆)
    d.ellipse([cx - 190*s, cy + 120*s, cx - 150*s, cy + 160*s], fill=(255, 224, 189, alpha), outline=(120,80,40,alpha), width=4)
    d.ellipse([cx + 150*s, cy + 120*s, cx + 190*s, cy + 160*s], fill=(255, 224, 189, alpha), outline=(120,80,40,alpha), width=4)

# ============================================================
# 卡通图标（代码绘制）
# ============================================================
def draw_money_bag(img, cx, cy, s=1.0, alpha=255):
    d = ImageDraw.Draw(img, "RGBA")
    d.polygon([(cx - 90*s, cy - 60*s), (cx + 90*s, cy - 60*s), (cx + 60*s, cy + 70*s), (cx - 60*s, cy + 70*s)],
              fill=(232, 178, 60, alpha), outline=(160, 110, 30, alpha), width=6)
    d.line([(cx - 70*s, cy - 55*s), (cx + 70*s, cy - 55*s)], fill=(160, 110, 30, alpha), width=14)
    d.text((cx, cy + 8*s), "¥", font=font(int(70*s)), fill=(255, 250, 220, alpha), anchor="mm")

def draw_card(img, cx, cy, s=1.0, alpha=255):
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle([cx - 110*s, cy - 70*s, cx + 110*s, cy + 70*s], radius=22,
                        fill=(120, 170, 230, alpha), outline=(60, 100, 170, alpha), width=6)
    d.line([(cx - 80*s, cy - 20*s), (cx + 80*s, cy - 20*s)], fill=(255, 255, 255, alpha), width=10)
    d.line([(cx - 80*s, cy + 15*s), (cx + 40*s, cy + 15*s)], fill=(255, 255, 255, alpha), width=10)

def draw_bomb(img, cx, cy, s=1.0, alpha=255, wobble=0.0):
    d = ImageDraw.Draw(img, "RGBA")
    ox = int(wobble * 10)
    d.ellipse([cx - 70*s + ox, cy - 60*s, cx + 70*s + ox, cy + 80*s], fill=(40, 40, 48, alpha), outline=(20,20,26,alpha), width=6)
    d.line([(cx + ox, cy - 60*s), (cx + 20*s + ox, cy - 105*s)], fill=(40, 40, 48, alpha), width=12)
    d.arc([cx - 12*s + ox, cy - 130*s, cx + 30*s + ox, cy - 88*s], 0, 180, fill=(255, 170, 40, alpha), width=8)
    # 火花
    d.ellipse([cx + 26*s + ox, cy - 118*s, cx + 40*s + ox, cy - 102*s], fill=(255, 210, 60, alpha))
    d.ellipse([cx - 30*s + ox, cy - 126*s, cx - 16*s + ox, cy - 110*s], fill=(255, 120, 60, alpha))
    # 感叹号
    d.text((cx + ox, cy + 10*s), "!", font=font(int(90*s)), fill=(255, 90, 60, alpha), anchor="mm")

def draw_check(img, cx, cy, s=1.0, alpha=255):
    d = ImageDraw.Draw(img, "RGBA")
    d.ellipse([cx - 80*s, cy - 80*s, cx + 80*s, cy + 80*s], fill=(60, 200, 120, alpha), outline=(30, 140, 80, alpha), width=6)
    d.line([(cx - 45*s, cy), (cx - 12*s, cy + 38*s)], fill=(255, 255, 255, alpha), width=14)
    d.line([(cx - 12*s, cy + 38*s), (cx + 55*s, cy - 38*s)], fill=(255, 255, 255, alpha), width=14)

def draw_arrow(img, x1, y1, x2, y2, alpha=255, color=(255, 200, 60)):
    d = ImageDraw.Draw(img, "RGBA")
    d.line([(x1, y1), (x2, y2)], fill=color + (alpha,), width=10)
    ang = math.atan2(y2 - y1, x2 - x1)
    for da in (0.5, -0.5):
        d.polygon([(x2, y2),
                   (x2 - 46*math.cos(ang - da), y2 - 46*math.sin(ang - da)),
                   (x2 - 46*math.cos(ang + da), y2 - 46*math.sin(ang + da))],
                  fill=color + (alpha,))

def draw_bubble(img, x, y, w, h, text, f, tail="down"):
    """对话框气泡: 圆角矩形 + 尾巴"""
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle([x, y, x + w, y + h], radius=30, fill=(255, 255, 255, 235),
                        outline=(90, 140, 220), width=5)
    if tail == "down":
        d.polygon([(x + w // 2 - 30, y + h - 6), (x + w // 2 + 30, y + h - 6), (x + w // 2, y + h + 46)],
                  fill=(255, 255, 255, 235), outline=(90, 140, 220))
    lines = wrap(d, text, f, w - 40)
    yy = y + 24
    for ln in lines[:3]:
        d.text((x + w // 2, yy), ln, font=f, fill=(40, 50, 70), anchor="mm")
        yy += int(f.size * 1.25)

# ============================================================
# 场景分镜（公转私 卡通版, 6 句配音对应 6 个画面状态）
# ============================================================
def render_scene(img, t, idx, sents):
    """idx: 当前句索引(0-5), t: 幕内时间。每句一个画面状态。"""
    d = ImageDraw.Draw(img, "RGBA")
    # 背景: 明亮渐变(活泼)
    for y in range(0, H, 3):
        tt = y / H
        c = tuple(int(a + (b - a) * tt) for a, b in zip((250, 246, 238), (226, 236, 248)))
        d.line([(0, y), (W, y)], fill=c)
    # 顶部标题
    d.text((W // 2, 120), "公转私是高压线", font=font(64), fill=(230, 90, 60),
           anchor="mm", stroke_width=5, stroke_fill=(255, 255, 255))
    d.line([(W // 2 - 130, 168), (W // 2 + 130, 168)], fill=(230, 90, 60), width=4)

    # ---- 角色区(上部): Q版老板, 表情随句变化 ----
    emo = ["normal", "confused", "confused", "shocked", "shocked", "happy"][min(idx, 5)]
    # 弹入动画(前0.4s从下面弹上来)
    bounce = 1.0 if idx == 0 else 1.0
    cy = 430
    draw_boss(img, 540, cy, scale=1.15, emotion=emo)

    # ---- 流程图区(中部): 钱袋→个人卡→炸弹→勾 ----
    y_flow = 1050
    # 弹入进度: 每个图标在对应句出现(前0.5s scale 弹入)
    def pop_in(start_idx, local):
        return 1.0  # 简化: 直接显示

    draw_money_bag(img, 240, y_flow, s=1.0)
    draw_arrow(img, 330, y_flow, 400, y_flow, alpha=255)
    draw_card(img, 500, y_flow, s=1.0)
    # 炸弹: 抖动(惊吓)
    if idx >= 3:
        wob = 3 * math.sin(t * 25) if idx == 3 else 0
        draw_bomb(img, 700, y_flow, s=1.0, wobble=wob)
    else:
        draw_bomb(img, 700, y_flow, s=1.0, alpha=60)
    # 勾(最后)
    if idx >= 5:
        draw_check(img, 880, y_flow, s=0.9)
    else:
        draw_check(img, 880, y_flow, s=0.9, alpha=60)

    # 图标下方小标签
    d.text((240, y_flow + 120), "公司账", font=font(34), fill=(90, 100, 120), anchor="mm")
    d.text((500, y_flow + 120), "个人卡", font=font(34), fill=(90, 100, 120), anchor="mm")
    d.text((700, y_flow + 120), "资金混同", font=font(34), fill=(220, 70, 50), anchor="mm")
    d.text((880, y_flow + 120), "无限责任", font=font(34), fill=(220, 70, 50), anchor="mm")

    # ---- 对话框(讲解内容) ----
    texts = [
        "老板问：公司账上的钱，能转到个人卡吗？",
        "记住！这就是公转私，税务和银行盯得最紧。",
        "钱一进个人卡，公司和个人就分不清了。",
        "被认定资金混同，砰！有限责任变无限责任！",
        "挣多少，都得搭进去。",
        "合规：报销走流程，借款按期还，放心赚钱！",
    ]
    bubble = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw_bubble(bubble, 130, 620, 820, 200, texts[min(idx, 5)], font(42), tail="up")
    img.paste(bubble, (0, 0), bubble)   # RGB 图用 paste(带mask), 不能用 alpha_composite

    # ---- 底部字幕(卡拉OK) ----
    y_sub = 1580
    d.rounded_rectangle([50, y_sub - 45, W - 50, y_sub + 55], radius=24,
                        fill=(20, 28, 45, 210), outline=(230, 150, 60) + (90,), width=3)
    cur_sent = sents[min(idx, len(sents) - 1)]
    lines = wrap(d, cur_sent, font(42), W - 140)
    yy = y_sub + 5
    for ln in lines[:2]:
        d.text((W // 2, yy), ln, font=font(42), fill=(255, 219, 120), anchor="mm",
               stroke_width=4, stroke_fill=(0, 0, 0))
        yy += 50


def build_timeline(sents, voice):
    from model_providers import ensure_env
    ensure_env()
    from qwen_tts import synth
    tmp = Path(tempfile.mkdtemp(prefix="cart_"))
    tl = []
    cur = 0.0
    for i, s in enumerate(sents):
        p = str(tmp / f"t{i}.wav")
        synth(s, voice, p, speech_rate=0.95, pitch_rate=1.0, volume=50)
        dur = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                                    "-of", "default=noprint_wrappers=1:nokey=1", p],
                                   capture_output=True, text=True).stdout.strip())
        tl.append((p, cur, cur + dur))
        cur += dur + 0.35
    return tl, tmp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--voice", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    from qwen_tts import _split_sentences
    sents = _split_sentences(args.text)
    if len(sents) > 6:
        sents = sents[:6]
    if not sents:
        sys.exit("无句子")
    print(f"[1/3] 配音 {len(sents)} 句 ...")
    tl, tmp = build_timeline(sents, args.voice)

    out_dir = Path(tempfile.mkdtemp(prefix="cartf_"))
    n = int(tl[-1][2] * FPS)
    print(f"[2/3] 渲染 {n} 帧 ...")
    for fi in range(n):
        t = fi / FPS
        idx = 0
        for k, (_, s0, s1) in enumerate(tl):
            if t >= s0:
                idx = k
        img = Image.new("RGB", (W, H))
        render_scene(img, t, idx, sents)
        img.save(f"{out_dir}/f_{fi:05d}.png")
        if fi % 300 == 0:
            print(f"  渲染 {int(100 * fi / n)}%")
    print("  渲染 100%")

    print("[3/3] ffmpeg 合成 ...")
    listf = tmp / "concat.txt"
    with open(listf, "w", encoding="utf-8") as f:
        for p, _, _ in tl:
            f.write(f"file '{p.replace(chr(92), '/')}'\n")
    audio = str(tmp / "all.wav")
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
                    "-c", "copy", audio], capture_output=True, text=True)
    subprocess.run([FFMPEG, "-y", "-r", str(FPS), "-i", str(out_dir / "f_%05d.png"),
                    "-i", audio, "-c:v", "libx264", "-crf", "19", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", args.out], capture_output=True, text=True)
    print(f"成品: {args.out}")


if __name__ == "__main__":
    main()
