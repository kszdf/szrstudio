# -*- coding: utf-8 -*-
"""白板式(手绘动画)渲染验证 v1 — 概念图解 + 线条逐笔画出 + 配音。

思路: 内容 → 白板布局(标题/文本框/箭头/流程图) → 手绘抖动线条(rough.js风格) →
      逐笔生长动画(线条从起点画到终点) → 配音对齐 → mp4。
本版只做「注销三步」固定布局验证, 跑通后接入管线(内容→LLM布局→本渲染器)。

用法: python make_whiteboard_video.py --out out.mp4 [--voice voice_id]
"""
import argparse
import math
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
FFMPEG = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"
FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")
W, H, FPS = 1080, 1920, 30

BG = (252, 250, 244)       # 白板米白底
INK = (45, 48, 55)         # 手绘墨色
RED = (190, 40, 40)
BLUE = (30, 80, 170)
GREEN = (30, 130, 90)
ORANGE = (210, 110, 30)
PURPLE = (110, 60, 170)

# 内容智能配色: 按语义给元素上色, 让白板内容有色感
COLOR_THEME = {
    "title":   (30, 60, 140),     # 主标题深蓝
    "step1":   (190, 40, 40),     # 步骤1红
    "step2":   (30, 130, 90),     # 步骤2绿
    "step3":   (210, 110, 30),    # 步骤3橙
    "step4":   (110, 60, 170),    # 步骤4紫
    "warn":    (190, 40, 40),     # 警示红
    "num":     (190, 40, 40),     # 关键数字红
    "body":    (45, 48, 55),      # 正文墨色
    "sub":     (120, 124, 130),   # 辅助灰
    "arrow":   (90, 100, 120),    # 箭头灰蓝
}

_F = {}
def font(size, style="hei"):
    """多字体: hei=黑体(正文/数字) kai=楷体(副文) xing=行楷(标题, 手写感强)。"""
    key = (size, style)
    if key not in _F:
        name = {"hei": "simhei.ttf", "kai": "kai.ttf", "xing": "xingkai.ttf"}[style]
        _F[key] = ImageFont.truetype(str(BASE / "fonts" / name), size)
    return _F[key]


# ============ 手绘抖动线条(rough.js 风格) ============
def rough_line(d, p1, p2, width=4, color=INK, seed=None):
    """两点间画一条带随机抖动的"手绘"直线。"""
    rng = random.Random(seed)
    x1, y1 = p1; x2, y2 = p2
    n = max(3, int(math.hypot(x2 - x1, y2 - y1) / 14))
    pts = []
    for i in range(n + 1):
        t = i / n
        x = x1 + (x2 - x1) * t
        y = y1 + (y2 - y1) * t
        if 0 < i < n:
            x += rng.uniform(-3, 3)
            y += rng.uniform(-3, 3)
        pts.append((x, y))
    d.line(pts, fill=color, width=width, joint="curve")


def rough_rect(d, box, width=4, color=INK, seed=None):
    """手绘抖动矩形(两遍描边更像手绘)。"""
    x0, y0, x1, y1 = box
    rough_line(d, (x0, y0), (x1, y0), width, color, seed)
    rough_line(d, (x1, y0), (x1, y1), width, color, seed)
    rough_line(d, (x1, y1), (x0, y1), width, color, seed)
    rough_line(d, (x0, y1), (x0, y0), width, color, seed)


def rough_circle(d, cx, cy, r, width=4, color=INK, seed=None):
    """手绘抖动圆(多边形近似 + 抖动)。"""
    rng = random.Random(seed)
    pts = []
    for i in range(25):
        a = i / 25 * 2 * math.pi
        rr = r + rng.uniform(-2.5, 2.5)
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d.line(pts + [pts[0]], fill=color, width=width)


def rough_arrow(d, p1, p2, width=4, color=INK, seed=None):
    """手绘箭头(线 + 箭头头)。"""
    rough_line(d, p1, p2, width, color, seed)
    x1, y1 = p1; x2, y2 = p2
    ang = math.atan2(y2 - y1, x2 - x1)
    L = 26
    for da in (0.5, -0.5):
        tx = x2 - L * math.cos(ang + da)
        ty = y2 - L * math.sin(ang + da)
        rough_line(d, (x2, y2), (tx, ty), width, color, (seed or 1) + int(da * 10))


# ============ 手绘图标(rough 风格, 内容语义化) ============
def draw_icon(d, kind, cx, cy, size=90, color=INK, seed=21):
    """在 (cx,cy) 画一个手绘风格小图标。kind: ledger/calendar/stamp/coin/check/warn/calculator/flag"""
    r = size / 2
    s = random.Random(seed)
    col = color
    if kind == "ledger":        # 账本: 竖矩形 + 中缝 + 横线
        rough_rect(d, (cx - r * 0.7, cy - r * 0.85, cx + r * 0.7, cy + r * 0.85), 4, col, seed)
        rough_line(d, (cx, cy - r * 0.85), (cx, cy + r * 0.85), 3, col, seed + 1)
        for k in range(3):
            yy = cy - r * 0.4 + k * r * 0.42
            rough_line(d, (cx + r * 0.12, yy), (cx + r * 0.55, yy), 3, col, seed + 2 + k)
    elif kind == "calendar":    # 日历: 矩形 + 顶部两小环 + 横线 + 圈出日期
        rough_rect(d, (cx - r * 0.8, cy - r * 0.75, cx + r * 0.8, cy + r * 0.8), 4, col, seed)
        rough_line(d, (cx - r * 0.8, cy - r * 0.25), (cx + r * 0.8, cy - r * 0.25), 3, col, seed + 1)
        for dx in (-r * 0.45, r * 0.45):
            rough_line(d, (cx + dx, cy - r * 0.75), (cx + dx, cy - r * 1.0), 3, col, seed + 2)
        rough_circle(d, cx + r * 0.3, cy + r * 0.25, r * 0.28, 3, col, seed + 3)
    elif kind == "stamp":       # 印章: 圆 + 中心字
        rough_circle(d, cx, cy, r * 0.85, 5, col, seed)
        rough_circle(d, cx, cy, r * 0.55, 3, col, seed + 1)
        f = font(int(size * 0.5), "kai")
        d.text((cx, cy), "章", font=f, fill=col, anchor="mm")
    elif kind == "coin":        # 钱袋/硬币: 圆 + ¥
        rough_circle(d, cx, cy, r * 0.85, 5, col, seed)
        rough_circle(d, cx, cy, r * 0.55, 3, col, seed + 1)
        f = font(int(size * 0.5), "hei")
        d.text((cx, cy), "¥", font=f, fill=col, anchor="mm")
    elif kind == "check":       # 对勾
        rough_line(d, (cx - r * 0.6, cy + r * 0.1), (cx - r * 0.15, cy + r * 0.55), 6, col, seed)
        rough_line(d, (cx - r * 0.15, cy + r * 0.55), (cx + r * 0.7, cy - r * 0.6), 6, col, seed + 1)
    elif kind == "warn":        # 感叹号三角
        pts = [(cx, cy - r * 0.9), (cx + r * 0.7, cy + r * 0.8), (cx - r * 0.7, cy + r * 0.8)]
        for i in range(3):
            a, b = pts[i], pts[(i + 1) % 3]
            rough_line(d, a, b, 4, col, seed + i)
        f = font(int(size * 0.55), "hei")
        d.text((cx, cy + r * 0.15), "!", font=f, fill=col, anchor="mm")
    elif kind == "calculator":  # 计算器
        rough_rect(d, (cx - r * 0.75, cy - r * 0.85, cx + r * 0.75, cy + r * 0.85), 4, col, seed)
        rough_rect(d, (cx - r * 0.55, cy - r * 0.6, cx + r * 0.55, cy - r * 0.2), 3, col, seed + 1)
        for k in range(6):
            gx = cx - r * 0.45 + (k % 3) * r * 0.45
            gy = cy + r * 0.02 + (k // 3) * r * 0.42
            rough_circle(d, gx, gy, r * 0.09, 2, col, seed + 2 + k)
    elif kind == "flag":        # 旗帜/目标
        rough_line(d, (cx - r * 0.8, cy + r * 0.85), (cx - r * 0.8, cy - r * 0.85), 4, col, seed)
        rough_line(d, (cx - r * 0.8, cy - r * 0.8), (cx + r * 0.8, cy - r * 0.2), 4, col, seed + 1)
        rough_line(d, (cx + r * 0.8, cy - r * 0.2), (cx - r * 0.8, cy + r * 0.3), 4, col, seed + 2)
    else:                       # 默认: 小圆点
        rough_circle(d, cx, cy, r * 0.5, 4, col, seed)


# ============ 白板内容(注销三步, 智能配色) ============
# 每个元素: (类型, 参数, 标签, 块内时长比例)
def build_blocks(layout=None):
    """layout: LLM 生成的布局 dict(缺省用注销三步示例)。
    返回: [{"narration": 该块旁白句, "els": [元素...]}, ...]
    元素按「语音块」分组: 标题块 → 每个要点一块 → 警示块。
    画面与语音同步: 每个块在其旁白句的语音时长内画出。"""
    cx = W // 2
    if layout is None:
        layout = {"title": "公司注销三步走", "warn": "缺一步都注销不了", "items": [
            {"main": "账结清", "sub": "补税 / 报报表", "num": "第 1 步", "icon": "ledger"},
            {"main": "公示 45 天", "sub": "公告债权债务", "num": "第 2 步", "icon": "calendar"},
            {"main": "注销登记", "sub": "先税务后工商", "num": "第 3 步", "icon": "stamp"},
        ]}
    title = layout.get("title", "财税知识")
    items = layout.get("items", [])[:4]
    n = len(items)

    roles = ["step1", "step2", "step3", "step4"]
    ys = []
    if n == 1:
        ys = [900]
    elif n == 2:
        ys = [680, 1180]
    elif n == 3:
        ys = [560, 900, 1240]
    else:
        ys = [480, 800, 1120, 1440]

    blocks = []

    # 块0: 标题(行楷手写体 + 深蓝 + 下划线), 旁白=标题
    blocks.append({"narration": title, "els": [
        ("text", {"xy": (cx, 250), "text": title, "size": 76, "role": "title",
                  "style": "xing", "underline": True}, "title", 0.65),
        ("line", {"p1": (cx - 260, 330), "p2": (cx + 260, 330)}, "title_ul", 0.35),
    ]})

    # 每个要点一块: 序号圆+图标+主文字+副文字, 旁白=该要点句
    # 元素时长均分整块(总和≈1.0), 让元素在整句语音时间内逐个画出(与语音同步)
    for i, item in enumerate(items):
        tag = f"s{i}"
        x, y = cx, ys[i]
        role = roles[i % 4]
        col = COLOR_THEME[role]
        els = [
            ("circle", {"cx": x - 210, "cy": y, "r": 42, "color": col}, tag + "_c", 0.28),
            ("text", {"xy": (x - 210, y), "text": item.get("num", str(i + 1)),
                      "size": 46, "role": role}, tag + "_n", 0.10),
        ]
        icon = item.get("icon", "")
        if icon:
            els.append(("icon", {"kind": icon, "cx": x - 330, "cy": y + 60, "size": 70, "color": col},
                        tag + "_icon", 0.18))
        els.append(("text", {"xy": (x + 20, y + 45), "text": item.get("main", ""),
                             "size": 66, "role": role, "style": "kai"}, tag + "_m", 0.28))
        if item.get("sub"):
            els.append(("text", {"xy": (x + 20, y + 118), "text": item["sub"],
                                 "size": 34, "role": "sub", "style": "kai"}, tag + "_s", 0.16))
        # 旁白句: 该要点 + 说明
        narration = item.get("main", "")
        if item.get("sub"):
            narration += "，" + item["sub"]
        blocks.append({"narration": narration, "els": els})

    # 箭头: 并入前一块尾部(与下一要点一起出现)
    # 警示块(红 + 手绘框), 旁白=警示
    if layout.get("warn"):
        blocks.append({"narration": layout["warn"], "els": [
            ("rect", {"box": (cx - 300, 1500, cx + 300, 1650), "color": COLOR_THEME["warn"]},
             "warn_box", 0.55),
            ("text", {"xy": (cx, 1575), "text": layout["warn"], "size": 42,
                      "role": "warn", "style": "kai"}, "warn_txt", 0.45),
        ]})

    # 品牌(常显, 并入标题块, 全程可见)
    blocks[0]["els"].append(("text", {"xy": (cx, 1780), "text": "昆山老张讲财税", "size": 36,
                                      "role": "sub", "style": "kai"}, "brand", 0.02))
    return blocks


def draw_block(frame, els, prog, narration="", sub_ratio=1.0):
    """按块内进度 prog(0-1) 画白板帧 + 底部逐字卡拉OK字幕。
    元素已到达的完整显示, 进行中的按生长比例; 字幕念到的字亮黄。"""
    img = frame.convert("RGBA") if frame.mode == "RGB" else frame
    d = ImageDraw.Draw(img)
    # 顺序绘制
    acc = 0.0
    for e in els:
        kind = e[0]
        dur = e[3] if len(e) > 3 else 0.02
        start = acc
        end = acc + dur
        acc = end
        if prog < start:
            break
        p_local = min(1.0, max(0.0, (prog - start) / max(dur, 1e-6)))
        if kind == "line":
            p1, p2 = e[1]["p1"], e[1]["p2"]
            x = p1[0] + (p2[0] - p1[0]) * p_local
            y = p1[1] + (p2[1] - p1[1]) * p_local
            rough_line(d, p1, (x, y), seed=e[1].get("seed", 7))
        elif kind == "rect":
            x0, y0, x1, y1 = e[1]["box"]
            per = p_local * 4
            edges = [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                     ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
            for ei, (a, b) in enumerate(edges):
                if per >= ei + 1:
                    rough_line(d, a, b, seed=e[1].get("seed", 5) + ei)
                elif per > ei:
                    t = per - ei
                    mid = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                    rough_line(d, a, mid, seed=e[1].get("seed", 5) + ei)
        elif kind == "circle":
            rng = random.Random(e[1].get("seed", 11))
            col = e[1].get("color", INK)
            pts = []
            nseg = 25
            for i in range(int(nseg * p_local) + 1):
                a = i / nseg * 2 * math.pi
                rr = e[1]["r"] + rng.uniform(-2.5, 2.5)
                pts.append((e[1]["cx"] + rr * math.cos(a), e[1]["cy"] + rr * math.sin(a)))
            if len(pts) > 1:
                d.line(pts, fill=col, width=4)
        elif kind == "arrow":
            x1, y1 = e[1]["p1"]; x2, y2 = e[1]["p2"]
            x = x1 + (x2 - x1) * p_local
            y = y1 + (y2 - y1) * p_local
            rough_line(d, e[1]["p1"], (x, y), seed=e[1].get("seed", 13))
            if p_local > 0.85:
                ang = math.atan2(y2 - y1, x2 - x1)
                L = 26
                for da in (0.5, -0.5):
                    tx = x2 - L * math.cos(ang + da)
                    ty = y2 - L * math.sin(ang + da)
                    rough_line(d, (x2, y2), (tx, ty), 4, seed=(e[1].get("seed", 13) + 1))
        elif kind == "text":
            alpha = int(255 * min(1.0, p_local * 3))
            if alpha <= 0:
                continue
            xy = e[1]["xy"]
            role = e[1].get("role", "body")
            color = e[1].get("color") or COLOR_THEME.get(role, INK)
            style = e[1].get("style", "kai" if role in ("sub",) else "xing" if role == "title" else "hei")
            f = font(e[1].get("size", 40), style)
            txt = e[1]["text"]
            lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dl = ImageDraw.Draw(lay)
            if e[1].get("underline"):
                tw = dl.textlength(txt, font=f)
                rough_line(dl, (xy[0] - tw / 2, xy[1] + f.size * 0.55),
                           (xy[0] + tw / 2, xy[1] + f.size * 0.55), 3,
                           COLOR_THEME.get("warn", RED), seed=e[1].get("seed", 3))
            dl.text(xy, txt, font=f, fill=color + (alpha,), anchor="mm")
            img = Image.alpha_composite(img, lay)
            d = ImageDraw.Draw(img)
        elif kind == "icon":
            alpha = int(255 * min(1.0, p_local * 4))
            if alpha <= 0:
                continue
            lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dl = ImageDraw.Draw(lay)
            draw_icon(dl, e[1]["kind"], e[1]["cx"], e[1]["cy"], e[1].get("size", 70),
                      e[1].get("color", INK), seed=e[1].get("seed", 21))
            lay = lay.point(lambda p: p * alpha // 255)
            img = Image.alpha_composite(img, lay)
            d = ImageDraw.Draw(img)

    # 底部逐字卡拉OK字幕(念到的字亮黄, 未念暗灰) — 语音同步
    if narration:
        img = add_karaoke(img, narration, sub_ratio)
    return img.convert("RGB")


def add_karaoke(img, text, ratio):
    """底部字幕: 暗底条 + 逐字卡拉OK(念到的字亮黄 255,219,120, 未念暗灰 150)。"""
    img = img.convert("RGBA")
    d = ImageDraw.Draw(img)
    f = font(44, "hei")
    max_w = W - 160
    lines, cur = [], ""
    for ch in text:
        if d.textlength(cur + ch, font=f) > max_w and cur:
            lines.append(cur); cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    total = max(1, len(text))
    shown = int(ratio * total)
    y0 = H - 240
    d.rounded_rectangle([60, y0, W - 60, H - 90], radius=24, fill=(8, 12, 22, 205))
    yy = y0 + 44
    cum = 0
    for ln in lines:
        w = d.textlength(ln, font=f)
        x = (W - w) / 2
        for ch in ln:
            done = cum < shown
            col = (255, 219, 120) if done else (150, 152, 160)
            d.text((x, yy), ch, font=f, fill=col, anchor="lm",
                   stroke_width=4, stroke_fill=(0, 0, 0))
            x += d.textlength(ch, font=f)
            cum += 1
        yy += 56
    return img.convert("RGB")



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--voice", default="")
    ap.add_argument("--layout", default="", help="白板布局JSON字符串或文件路径(缺省用注销三步示例)")
    ap.add_argument("--duration", type=float, default=0, help="总时长(秒, 0=按语音自动)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    layout = None
    if args.layout:
        import json as _json
        try:
            lp = Path(args.layout)
            raw = lp.read_text(encoding="utf-8") if lp.exists() else args.layout
            if raw.startswith("\ufeff"):   # 兼容 PowerShell Out-File 的 BOM
                raw = raw[1:]
            layout = _json.loads(raw)
        except Exception as e:  # noqa: BLE001
            print(f"[警告] 布局解析失败, 用示例: {e}")
    blocks = build_blocks(layout)

    # ---- 1) 每块旁白单独 TTS, 测时长 → 语音驱动时间轴 ----
    print(f"[1/3] 配音 {len(blocks)} 句并测时长 ...")
    tmp = Path(tempfile.mkdtemp(prefix="wb_"))
    tl = []          # (wav, start, end)
    cur = 0.0
    from model_providers import ensure_env
    ensure_env()
    from qwen_tts import synth
    for i, b in enumerate(blocks):
        ntxt = b["narration"]
        wav = str(tmp / f"n{i}.wav")
        synth(ntxt, args.voice, wav, speech_rate=0.90, pitch_rate=1.0, volume=50)
        dur = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                                    "-of", "default=noprint_wrappers=1:nokey=1", wav],
                                   capture_output=True, text=True).stdout.strip())
        tl.append((wav, cur, cur + dur))
        cur += dur + 0.25
        print(f"  块{i+1}: {ntxt[:16]}... {dur:.1f}s")

    total_sec = tl[-1][2] if tl else (args.duration or 18.0)
    if args.duration and args.duration > total_sec:
        total_sec = args.duration   # 允许更长, 尾部定格

    # ---- 2) 渲染: 按语音时间轴驱动每个块的元素 ----
    print(f"[2/3] 渲染 {int(total_sec * FPS)} 帧(语音驱动同步) ...")
    frames_dir = tmp / "f"
    frames_dir.mkdir()
    n = int(total_sec * FPS)
    for fi in range(n):
        t = fi / FPS
        # 找当前块
        bi = 0
        for i, (_, s0, s1) in enumerate(tl):
            if t >= s0:
                bi = i
            if t < s1:
                break
        _, s0, s1 = tl[bi]
        # 块内进度: 元素在语音前半段画完, 后半段定格看清
        block_prog = min(1.0, max(0.0, (t - s0) / max(s1 - s0, 0.1)))
        draw_prog = min(1.0, block_prog / 0.7)   # 70% 时间画完
        sub_ratio = min(1.0, block_prog)          # 字幕逐字跟语音
        # 保留前面块已画内容(累积画布)
        frame = Image.new("RGB", (W, H), BG)
        for j in range(bi + 1):
            frame = draw_block(frame, blocks[j]["els"], 1.0 if j < bi else draw_prog,
                               narration=blocks[j]["narration"],
                               sub_ratio=1.0 if j < bi else sub_ratio)
        frame.save(frames_dir / f"f_{fi:05d}.png")
    print("  渲染完成")

    # ---- 3) 合成: 拼接各块语音 + 帧序列 ----
    print("[3/3] 合成 ...")
    listf = tmp / "list.txt"
    with open(listf, "w", encoding="utf-8") as f:
        for wav, _, _ in tl:
            f.write(f"file '{wav.replace(chr(92), '/')}'\n")
    audio = str(tmp / "all.wav")
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
                    "-c", "copy", audio], capture_output=True, text=True)
    if args.voice:
        subprocess.run([FFMPEG, "-y", "-framerate", str(FPS), "-i", str(frames_dir / "f_%05d.png"),
                        "-i", audio, "-vf", "format=yuv420p", "-profile:v", "high",
                        "-c:v", "libx264", "-crf", "19", "-c:a", "aac", "-shortest", args.out],
                       capture_output=True, text=True)
    else:
        subprocess.run([FFMPEG, "-y", "-framerate", str(FPS), "-i", str(frames_dir / "f_%05d.png"),
                        "-vf", "format=yuv420p", "-profile:v", "high",
                        "-c:v", "libx264", "-crf", "19", "-an", args.out],
                       capture_output=True, text=True)
    print(f"成品: {args.out}")


if __name__ == "__main__":
    main()
