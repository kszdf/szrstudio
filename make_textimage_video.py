#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A档 · 不出镜双声图文短视频生成器

功能:
  - 逐句 TTS（男=张老师克隆音 zhangc2 / 女=江老师克隆音 jiangnv3，均 cosyvoice-v3-plus）
  - 每句生成一张竖屏图文幻灯片（1080x1920），文字自动换行 + 描边，浅色渐变底
  - ffmpeg 按各句真实音频时长生成静帧视频片段，再拼接 + 混音
  - 底部品牌条「慧根堂 · 老张讲财税」
  - 不需要 HEYGEM 视频合成

用法:
  python make_textimage_video.py --dialogue verify_textimg_mini.txt --out output/video/textimg.mp4
  python make_textimage_video.py --dialogue x.txt --out x.mp4 --bg covers/bg_finance.png
  python make_textimage_video.py --dialogue x.txt --out x.mp4 --dry-tts
  python make_textimage_video.py --dialogue x.txt --out x.mp4 --female-voice longfei --female-model cosyvoice-v1  # 回退预设女声

依赖: Pillow, ffmpeg(全量), dashscope(真实TTS)
"""
import os
import sys
import argparse
import subprocess
import wave
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FFMPEG = r"D:\ffmpeg\ffmpeg-8.1.2-full_build\bin\ffmpeg.exe"

try:
    from qwen_tts import synth as _qwen_synth
except Exception as e:  # pragma: no cover
    print(f"[WARN] 无法导入 qwen_tts: {e}")
    _qwen_synth = None

MALE_VOICE = "cosyvoice-v3-plus-zhangc2-28a7c3541e1c45518a03046c11baeb1d"
MALE_MODEL = "cosyvoice-v3-plus"
FEMALE_VOICE = "cosyvoice-v3-plus-jiangnv3-991b204c1d564ac7a60f0cb9a8fd78bd"
FEMALE_MODEL = "cosyvoice-v3-plus"

W, H = 1080, 1920
FONT_PATH = os.path.join(HERE, "fonts", "simhei.ttf")
MAX_W = W - 200
TITLE_SIZE = 60
BODY_SIZE = 56
STROKE_W = 3


def _cjk_wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) <= max_w:
            cur += ch
        else:
            if cur:
                lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def _wrap_to_lines(draw, text, base_size, max_w, max_lines, min_size):
    from PIL import ImageFont
    size = base_size
    while size >= min_size:
        font = ImageFont.truetype(FONT_PATH, size)
        lines = _cjk_wrap(draw, text, font, max_w)
        if len(lines) <= max_lines:
            return lines, size
        size -= 2
    font = ImageFont.truetype(FONT_PATH, min_size)
    return _cjk_wrap(draw, text, font, max_w), min_size


def _draw_stroke(draw, xy, text, font, fill, stroke_w=STROKE_W, stroke_fill=(0, 0, 0)):
    draw.text(xy, text, font=font, fill=fill, stroke_width=stroke_w, stroke_fill=stroke_fill)


def parse_dialogue(path):
    segs = []
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("女"):
                role = "F"
                text = line[line.find("：") + 1:] if "：" in line else line[line.find(":") + 1:]
            elif line.startswith("男"):
                role = "M"
                text = line[line.find("：") + 1:] if "：" in line else line[line.find(":") + 1:]
            else:
                role, text = "M", line
            text = text.strip()
            if text:
                segs.append((role, text))
    return segs


def tts_one(text, role, out_wav, dry, female_voice, female_model, male_voice, male_model):
    voice = female_voice if role == "F" else male_voice
    model = female_model if role == "F" else male_model
    if dry or _qwen_synth is None:
        subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                        "-t", "2.4", "-c:a", "pcm_s16le", out_wav],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 2.4
    _qwen_synth(text, voice, out_wav, model=model, speech_rate=1.0, pitch_rate=1.0, volume=50)
    with wave.open(out_wav, "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def make_slide(text, idx, total, bg_image=None):
    from PIL import Image, ImageDraw, ImageFont
    if bg_image:
        img = Image.open(bg_image).convert("RGB").resize((W, H))
    else:
        # 浅色渐变底
        img = Image.new("RGB", (W, H), (235, 242, 250))
        px = img.load()
        for y in range(H):
            r = int(235 - 25 * y / H)
            g = int(242 - 20 * y / H)
            b = int(250 - 10 * y / H)
            for x in range(W):
                px[x, y] = (r, g, b)
    draw = ImageDraw.Draw(img, "RGBA")
    # 顶部序号条
    fnum = ImageFont.truetype(FONT_PATH, 40)
    _draw_stroke(draw, (60, 70), f"{idx}/{total}", fnum, (40, 70, 120), stroke_w=1)
    # 正文（自动换行，最多 6 行）
    fb = ImageFont.truetype(FONT_PATH, BODY_SIZE)
    lines, fs = _wrap_to_lines(draw, text, BODY_SIZE, MAX_W, max_lines=6, min_size=36)
    line_h = fs * 1.3
    y0 = H // 2 - (len(lines) * line_h) / 2
    for line in lines:
        lw = draw.textlength(line, font=fb)
        _draw_stroke(draw, ((W - lw) / 2, y0), line, fb, (30, 40, 60), stroke_w=STROKE_W)
        y0 += line_h
    # 品牌条
    fbr = ImageFont.truetype(FONT_PATH, 38)
    txt = "慧根堂 · 老张讲财税"
    _draw_stroke(draw, ((W - draw.textlength(txt, font=fbr)) / 2, H - 96), txt, fbr,
                 (255, 255, 255), stroke_w=2)
    return img


def make_video(dialogue, out_path, bg_image=None, dry=False, gap=0.0,
               female_voice=FEMALE_VOICE, female_model=FEMALE_MODEL,
               male_voice=MALE_VOICE, male_model=MALE_MODEL):
    segs = parse_dialogue(dialogue)
    if not segs:
        raise SystemExit("对话文件为空或解析失败")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmpdir = tempfile.mkdtemp(prefix="textimg_")

    clips = []
    for i, (role, text) in enumerate(segs):
        wav = os.path.join(tmpdir, f"audio_{i:03d}.wav")
        d = tts_one(text, role, wav, dry, female_voice, female_model, male_voice, male_model)
        slide = make_slide(text, i + 1, len(segs), bg_image=bg_image)
        spng = os.path.join(tmpdir, f"slide_{i:03d}.png")
        slide.save(spng)
        # 静帧片段，时长 = 音频时长
        clip = os.path.join(tmpdir, f"seg_{i:03d}.mp4")
        subprocess.run(
            [FFMPEG, "-y", "-loop", "1", "-i", spng, "-i", wav,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", f"{d:.3f}",
             "-c:a", "aac", "-b:a", "128k", "-r", "24", "-shortest", clip],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        clips.append(clip)

    listfile = os.path.join(tmpdir, "clips.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        for c in clips:
            f.write(f"file '{c.replace(chr(92), '/')}'\n")
    subprocess.run(
        [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", listfile,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", out_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.rmtree(tmpdir, ignore_errors=True)
    size_kb = os.path.getsize(out_path) // 1024
    print(f"成品: {out_path}  ({size_kb} KB)\n"
          f"   {W}x{H} 竖屏 | 双声图文 | 静帧按句时长 | 不出镜")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="A档·不出镜双声图文短视频生成")
    ap.add_argument("--dialogue", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bg", default=None, help="自定义背景图（默认浅色渐变）")
    ap.add_argument("--dry-tts", action="store_true")
    ap.add_argument("--gap", type=float, default=0.0)
    ap.add_argument("--female-voice", default=FEMALE_VOICE)
    ap.add_argument("--female-model", default=FEMALE_MODEL)
    ap.add_argument("--male-voice", default=MALE_VOICE)
    ap.add_argument("--male-model", default=MALE_MODEL)
    args = ap.parse_args()
    make_video(args.dialogue, args.out, bg_image=args.bg, dry=args.dry_tts, gap=args.gap,
               female_voice=args.female_voice, female_model=args.female_model,
               male_voice=args.male_voice, male_model=args.male_model)


if __name__ == "__main__":
    main()
