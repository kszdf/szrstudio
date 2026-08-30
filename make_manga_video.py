#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_manga_video.py — AI 漫剧成片管线 v2

两种动效模式:
  [默认] Ken Burns 代码慢镜(低成本): 分镜图缩放平移 + 步骤卡/信息层
  [--i2v] AI 图生视频动效(惊艳): 每幕分镜图 → wanx 图生视频(真动效) → 税务官方风信息层叠加 → 配音对齐
内容由 LLM 分镜 + AI 生图 + 动效构成, 角色一致性用「固定角色描述」保证。

用法:
  python make_manga_video.py --shots "s1图,s2图,..." --narration "旁白1|旁白2|..." \
      --voice <voice_id> --out out.mp4 [--title 公转私是高压线]
  # i2v 模式(每幕约0.24元/秒, 5幕约6元):
  python make_manga_video.py --shots ... --narration ... --voice ... --out ... --i2v
      [--tags "标签1,标签2,..."] [--cards "信息卡1,信息卡2,..."] [--nums "关键1,关键2,..."]
"""
import argparse
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
FFMPEG = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"
FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")
W, H, FPS = 1080, 1920, 30
TRANS = 0.6      # 幕间淡入时长
I2V_SECS = 5     # 每幕图生视频时长(秒)
I2V_COST_PER_SEC = 0.24  # 元/秒

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


# ============ 税务官方风信息层 (i2v 模式) ============
# 参考税务官方AI动画(《关公说税》《喵小鱼》): 顶部知识点标签 + 中部信息卡(关键数字放大) + 底部品牌条
BLUE = (23, 78, 166)
BLUE_L = (59, 130, 246)
RED = (220, 38, 38)
AMBER = (217, 119, 6)
WHITE = (255, 255, 255)
DARK = (30, 41, 59)


def add_info(frame, tag, card, num):
    """叠加信息层: 顶部标签条 + 中部信息卡(关键数字放大) + 底部品牌条。"""
    # 输入可能是 720x1280 (i2v 原始), 先 cover 放大到 1080x1920 再叠加信息层
    if frame.size != (W, H):
        frame = frame.resize((W, H), Image.LANCZOS)
    img = frame.convert("RGBA")
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    # ---- 顶部: 渐变蓝条 + 知识点标签 ----
    for yy in range(130):
        a = int(235 * (1 - yy / 130))
        d.line([(0, yy), (W, yy)], fill=(16, 56, 120, a))
    d.rounded_rectangle([44, 34, 300, 104], radius=16, fill=(255, 255, 255, 235))
    d.rounded_rectangle([44, 34, 300, 104], radius=16, outline=AMBER, width=3)
    d.text((64, 69), "⚠", font=font(48), fill=AMBER, anchor="lm")
    d.text((124, 69), tag, font=font(42), fill=BLUE, anchor="lm")

    # ---- 中部: 信息卡(白底圆角 + 主文字 + 关键数字放大) ----
    card_y0, card_y1 = 1210, 1650
    d.rounded_rectangle([70, card_y0, W - 70, card_y1], radius=26,
                        fill=(255, 255, 255, 246))
    d.rounded_rectangle([70, card_y0, W - 70, card_y1], radius=26,
                        outline=BLUE, width=5)
    d.rounded_rectangle([70, card_y0 + 22, 94, card_y1 - 22], radius=12, fill=BLUE)
    f = font(44)
    lines, cur = [], ""
    for ch in card:
        if d.textlength(cur + ch, font=f) > W - 260 and cur:
            lines.append(cur); cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    yy = card_y0 + 78
    for ln in lines[:3]:
        d.text((150, yy), ln, font=f, fill=DARK, anchor="lm")
        yy += 62
    if num:
        d.rounded_rectangle([150, card_y1 - 150, W - 150, card_y1 - 54], radius=16,
                            fill=(239, 246, 255, 255))
        d.rounded_rectangle([150, card_y1 - 150, W - 150, card_y1 - 54], radius=16,
                            outline=BLUE_L, width=2)
        d.text((180, card_y1 - 102), "关键点", font=font(36), fill=BLUE_L, anchor="lm")
        d.text((360, card_y1 - 102), num, font=font(52), fill=RED, anchor="lm")

    # ---- 底部: 品牌署名条 ----
    d.rounded_rectangle([0, H - 150, W, H], radius=0, fill=(16, 56, 120, 245))
    d.line([(0, H - 150), (W, H - 150)], fill=(255, 200, 60, 255), width=4)
    d.text((W / 2, H - 75), "昆山老张讲财税", font=font(44), fill=WHITE, anchor="mm")

    img = Image.alpha_composite(img, ov)
    return img.convert("RGB")


def _extract_num(text):
    """从旁白中提取关键数字/短语(如'45天'、'三步'、'2万'), 无则返回空串。"""
    m = re.search(r"([一二三四五六七八九十百0-9]+(?:天|个月|年|元|万|步|%|％))", text)
    if m:
        return m.group(1)
    return ""


def i2v_clip(shot_path, tmp, idx, prompt=""):
    """单幕图生视频: 分镜图 → wanx2.1-i2v-turbo → 5s 动效 mp4。
    返回 mp4 路径; 失败返回 None(由上层回退 Ken Burns)。"""
    from model_providers import ensure_env
    ensure_env()
    from dashscope import VideoSynthesis
    prompt = prompt or ("画面缓慢推近镜头，人物轻微点头，自然呼吸，背景微动，电影质感，动作平滑自然")
    t0 = time.time()
    try:
        rsp = VideoSynthesis.call(model="wanx2.1-i2v-turbo", prompt=prompt,
                                  img_url=shot_path, size="720*1280",
                                  duration=I2V_SECS,
                                  api_key=os.environ.get("DASHSCOPE_API_KEY"))
    except Exception as e:  # noqa: BLE001
        print(f"  幕{idx+1} i2v 调用异常: {e}")
        return None
    if rsp.status_code != 200:
        print(f"  幕{idx+1} i2v 提交失败: HTTP {rsp.status_code} {getattr(rsp, 'message', '')}")
        return None
    task_id = rsp.output.task_id
    print(f"  幕{idx+1} i2v 任务 {task_id} 轮询中 ...")
    url = None
    for _ in range(90):
        time.sleep(10)
        q = VideoSynthesis.fetch(task=task_id, api_key=os.environ.get("DASHSCOPE_API_KEY"))
        st = q.output.task_status
        if st == "SUCCEEDED":
            url = q.output.video_url
            break
        if st in ("FAILED", "CANCELED", "UNKNOWN"):
            print(f"  幕{idx+1} i2v 任务失败: {getattr(q.output, 'message', '')}")
            return None
    if not url:
        print(f"  幕{idx+1} i2v 超时未完成")
        return None
    import urllib.request
    dest = tmp / f"i2v_{idx}.mp4"
    urllib.request.urlretrieve(url, dest)
    print(f"  幕{idx+1} i2v 完成 ({time.time()-t0:.0f}s)")
    return str(dest)


def build_i2v(shots, narrs, steps, title, voice, out, tmp, tags=None, cards=None, nums=None):
    """i2v 模式主流程: 每幕图生视频动效 → 信息层叠加 → 配音对齐 → 拼接成片。"""
    from model_providers import ensure_env
    ensure_env()
    from qwen_tts import synth

    n = len(narrs)
    cost = I2V_SECS * I2V_COST_PER_SEC * n
    print(f"[i2v] 模式: 图生视频动效, {n} 幕 x {I2V_SECS}s, 预计费用 ≈ {cost:.1f} 元")

    # 信息层字段按幕数补齐(LLM 可能只输出部分或空串, 避免越界)
    def _pad(lst, i, default):
        return lst[i] if lst and i < len(lst) and lst[i].strip() else default

    tags = [_pad(tags, i, (steps[i] if i < len(steps) and steps[i] else (title or "财税科普")))
            for i in range(n)]
    cards = [_pad(cards, i, narrs[i]) for i in range(n)]
    nums = [_pad(nums, i, _extract_num(narrs[i])) for i in range(n)]

    # 1) 每幕: i2v 动效 + 信息层叠加
    print(f"[1/3] 生成 {n} 幕图生视频动效(每幕约2分钟) ...")
    segs = []
    for i, shot in enumerate(shots[:n] if len(shots) >= n else shots * n):
        clip = i2v_clip(shot, tmp, i)
        if not clip:
            # 回退: 该幕用 Ken Burns 静态动效, 不阻断成片
            print(f"  幕{i+1} i2v 失败, 回退 Ken Burns 静态动效")
            frames = []
            # 用配音时长近似(无配音时用 I2V_SECS)
            secs = I2V_SECS
            kenburns(shot, frames, secs)
            fdir = tmp / f"kb{i}"
            fdir.mkdir(exist_ok=True)
            for fi, fr in enumerate(frames):
                fr = add_info(fr, tags[i], cards[i], nums[i])
                fr.save(fdir / f"f_{fi:04d}.png")
            seg = tmp / f"seg{i}.mp4"
            subprocess.run([FFMPEG, "-y", "-framerate", str(FPS), "-i", str(fdir / "f_%04d.png"),
                            "-vf", f"scale={W}:{H},fps={FPS},format=yuv420p",
                            "-profile:v", "high", "-level", "4.0",
                            "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-an", str(seg)],
                           capture_output=True, text=True)
            segs.append(str(seg))
            continue
        # i2v 动效逐帧叠加信息层
        fdir = tmp / f"fi{i}"
        fdir.mkdir(exist_ok=True)
        subprocess.run([FFMPEG, "-y", "-i", clip, "-vf", "fps=30",
                        str(fdir / "f_%04d.png")], capture_output=True, text=True)
        frames = sorted(fdir.glob("f_*.png"))
        print(f"  幕{i+1}: 叠加信息层 {len(frames)} 帧 ...")
        for fp in frames:
            im = add_info(Image.open(fp), tags[i], cards[i], nums[i])
            im.save(fp)
        seg = tmp / f"seg{i}.mp4"
        subprocess.run([FFMPEG, "-y", "-framerate", "30", "-i", str(fdir / "f_%04d.png"),
                        "-vf", f"scale={W}:{H},fps={FPS},format=yuv420p",
                        "-profile:v", "high", "-level", "4.0",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-an", str(seg)],
                       capture_output=True, text=True)
        segs.append(str(seg))

    # 2) 配音对齐: 每幕旁白 TTS, 画面时长为主, 配音不足处静音补齐
    print("[2/3] 配音对齐 ...")
    voiced = []
    for i, s in enumerate(segs):
        wav = tmp / f"v{i}.wav"
        synth(narrs[i], voice, str(wav), speech_rate=0.90, pitch_rate=1.0, volume=50)
        segv = tmp / f"segv{i}.mp4"
        subprocess.run([FFMPEG, "-y", "-i", s, "-i", str(wav),
                        "-filter_complex", "[1:a]apad,aresample=44100,aformat=channel_layouts=stereo[a]",
                        "-map", "0:v", "-map", "[a]",
                        "-c:v", "copy", "-c:a", "aac", "-shortest", str(segv)],
                       capture_output=True, text=True)
        voiced.append(str(segv))

    # 3) 拼接
    print("[3/3] 拼接成片 ...")
    listf = tmp / "list.txt"
    with open(listf, "w", encoding="utf-8") as f:
        for s in voiced:
            f.write(f"file '{s.replace(chr(92), '/')}'\n")
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
                    "-c", "copy", out], capture_output=True, text=True)
    print(f"成品: {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", required=True, help="逗号分隔分镜图路径")
    ap.add_argument("--narration", required=True, help="竖线|分隔的每幕旁白")
    ap.add_argument("--voice", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--steps", default="", help="讲解式步骤清单(逗号分隔): 画面下部逐步展示")
    ap.add_argument("--i2v", action="store_true", help="AI 图生视频动效模式(每幕约0.24元/秒, 惊艳)")
    ap.add_argument("--tags", default="", help="i2v模式: 每幕顶部知识点标签(逗号分隔, 缺省自动)")
    ap.add_argument("--cards", default="", help="i2v模式: 每幕信息卡主文字(逗号分隔, 缺省用旁白)")
    ap.add_argument("--nums", default="", help="i2v模式: 每幕关键数字(逗号分隔, 缺省自动提取)")
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

    # i2v 模式: 图生视频动效 + 税务官方风信息层
    if args.i2v:
        tags = [s.strip() for s in args.tags.split(",") if s.strip()]
        cards = [s.strip() for s in args.cards.split(",") if s.strip()]
        nums = [s.strip() for s in args.nums.split(",") if s.strip()]
        build_i2v(shots, narrs, steps, args.title, args.voice, args.out,
                  Path(tempfile.mkdtemp(prefix="manga_i2v_")),
                  tags or None, cards or None, nums or None)
        return

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
