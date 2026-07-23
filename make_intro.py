#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
品牌片头生成器（一次性素材）
生成 3s 深蓝品牌卡视频：老张讲财税 + 淡入淡出。
输出: covers/intro.mp4
（与封面品牌名一致，只放"老张讲财税"，不加慧根堂）
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import subprocess

BASE = Path("D:/heygem_data/gpt_sovits")
FONT = str(BASE / "fonts/simhei.ttf")
FRAME = BASE / "covers/intro_frame.png"
OUT = BASE / "covers/intro.mp4"
W, H = 1080, 1920


def make_frame():
    img = Image.new("RGB", (W, H), (14, 42, 74))  # 深蓝 #0E2A4A
    d = ImageDraw.Draw(img, "RGBA")
    # 顶部金色细线
    d.rectangle([0, 120, W, 132], fill=(232, 199, 125, 255))
    # 主品牌名
    f1 = ImageFont.truetype(FONT, 150)
    d.text((W // 2, H // 2 - 30), "老张讲财税", font=f1, fill=(255, 255, 255), anchor="mm")
    # 副标（slogan，浅灰）
    f2 = ImageFont.truetype(FONT, 52)
    d.text((W // 2, H // 2 + 110), "财税风险 提前规避", font=f2, fill=(210, 220, 230, 255), anchor="mm")
    # 底部金色细线
    d.rectangle([0, H - 132, W, H - 120], fill=(232, 199, 125, 255))
    img.save(FRAME, "PNG")
    print(f"  帧已生成: {FRAME}")


def to_video():
    # 加静音音轨，保证 concat 时音频输入存在
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(FRAME),
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", "3", "-r", "30",
        "-vf", "fade=t=in:st=0:d=0.5,fade=t=out:st=2.5:d=0.5,format=yuv420p",
        "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
        "-shortest", str(OUT),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("  [ERR] 片头生成失败:")
        print(r.stderr[-800:])
    else:
        print(f"  片头已生成: {OUT}")


if __name__ == "__main__":
    make_frame()
    to_video()
