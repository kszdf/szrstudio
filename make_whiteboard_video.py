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


# ============ 白板内容(注销三步, 智能配色) ============
# 每个元素: (类型, 参数, 标签, 时长比例)
def build_elements(layout=None):
    """layout: LLM 生成的布局 dict(缺省用注销三步示例):
    {"title": "标题", "items": [{"main": "要点", "sub": "说明", "num": "关键数字"}]}
    步骤自动轮流配色, 关键数字红色高亮, 标题行楷手写体。"""
    els = []
    cx = W // 2
    if layout is None:
        layout = {"title": "公司注销三步走", "items": [
            {"main": "账结清", "sub": "补税 / 报报表", "num": "第 1 步"},
            {"main": "公示 45 天", "sub": "公告债权债务", "num": "第 2 步"},
            {"main": "注销登记", "sub": "先税务后工商", "num": "第 3 步"},
        ]}
    title = layout.get("title", "财税知识")
    items = layout.get("items", [])[:4]
    n = len(items)

    # 1 标题(行楷手写体 + 深蓝 + 下划线)
    els.append(("text", {"xy": (cx, 250), "text": title, "size": 76, "role": "title",
                         "style": "xing", "underline": True}, "title", 0.10))
    els.append(("line", {"p1": (cx - 260, 330), "p2": (cx + 260, 330)}, "title_ul", 0.14))

    # 2 步骤卡片: 自动轮流配色(step1红/step2绿/step3橙/step4紫)
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
    for i, item in enumerate(items):
        tag = f"s{i}"
        x, y = cx, ys[i]
        role = roles[i % 4]
        col = COLOR_THEME[role]
        # 序号圆(语义色)
        els.append(("circle", {"cx": x - 210, "cy": y, "r": 42, "color": col}, tag + "_c", 0.02))
        els.append(("text", {"xy": (x - 210, y), "text": item.get("num", str(i + 1)),
                             "size": 46, "role": role}, tag + "_n", 0.02))
        # 主文字(语义色 + 楷体)
        els.append(("text", {"xy": (x + 20, y + 45), "text": item.get("main", ""),
                             "size": 66, "role": role, "style": "kai"}, tag + "_m", 0.02))
        # 副文字(灰)
        if item.get("sub"):
            els.append(("text", {"xy": (x + 20, y + 118), "text": item["sub"],
                                 "size": 34, "role": "sub", "style": "kai"}, tag + "_s", 0.02))
    # 3 箭头连接
    for i in range(n - 1):
        y0 = ys[i]
        els.append(("arrow", {"p1": (cx, y0 + 110), "p2": (cx, ys[i + 1] - 110)}, f"ar{i}", 0.03))
    # 4 底部警示(红 + 手绘框)
    if layout.get("warn"):
        els.append(("rect", {"box": (cx - 300, 1500, cx + 300, 1650), "color": COLOR_THEME["warn"]},
                    "warn_box", 0.02))
        els.append(("text", {"xy": (cx, 1575), "text": layout["warn"], "size": 42,
                             "role": "warn", "style": "kai"}, "warn_txt", 0.02))
    # 5 品牌
    els.append(("text", {"xy": (cx, 1780), "text": "昆山老张讲财税", "size": 36,
                         "role": "sub", "style": "kai"}, "brand", 0.02))
    return els


def draw_frame(els, prog):
    """按进度 prog(0-1) 画白板帧: 已到达的元素完整显示, 进行中的元素按生长比例。"""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # 顺序绘制, 进度达到才画
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
            # 按顺序画四条边, 局部进度
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
            # 弧线生长(语义色)
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
            # 文字: 淡入 + 轻微上滑(手写出现感); 按语义配色 + 手写体
            alpha = int(255 * min(1.0, p_local * 3))
            if alpha <= 0:
                continue
            xy = e[1]["xy"]
            role = e[1].get("role", "body")
            color = e[1].get("color") or COLOR_THEME.get(role, INK)
            style = e[1].get("style", "kai" if role in ("sub",) else "xing" if role == "title" else "hei")
            f = font(e[1].get("size", 40), style)
            txt = e[1]["text"]
            # 先画在临时透明层实现淡入
            lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dl = ImageDraw.Draw(lay)
            # 文字下加手绘下划线(高亮关键)
            if e[1].get("underline"):
                tw = dl.textlength(txt, font=f)
                rough_line(dl, (xy[0] - tw / 2, xy[1] + f.size * 0.55),
                           (xy[0] + tw / 2, xy[1] + f.size * 0.55), 3,
                           COLOR_THEME.get("warn", RED), seed=e[1].get("seed", 3))
            dl.text(xy, txt, font=f, fill=color + (alpha,), anchor="mm")
            img = Image.alpha_composite(img.convert("RGBA"), lay).convert("RGB")
            d = ImageDraw.Draw(img)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--voice", default="")
    ap.add_argument("--layout", default="", help="白板布局JSON字符串或文件路径(缺省用注销三步示例)")
    ap.add_argument("--duration", type=float, default=18.0, help="成片时长(秒)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    layout = None
    if args.layout:
        import json as _json
        try:
            lp = Path(args.layout)
            raw = lp.read_text(encoding="utf-8") if lp.exists() else args.layout
            layout = _json.loads(raw)
        except Exception as e:  # noqa: BLE001
            print(f"[警告] 布局解析失败, 用示例: {e}")
    els = build_elements(layout)
    total_dur = sum(e[3] if len(e) > 3 else 0.02 for e in els)

    # 渲染: 全程时长内, 前 90% 画完所有元素, 后 10% 定格
    print(f"[1/2] 渲染 {int(args.duration * FPS)} 帧 ...")
    tmp = Path(tempfile.mkdtemp(prefix="wb_"))
    n = int(args.duration * FPS)
    draw_end = int(n * 0.9)
    for fi in range(n):
        prog = min(1.0, fi / draw_end) if draw_end > 0 else 1.0
        frame = draw_frame(els, prog)
        frame.save(tmp / f"f_{fi:04d}.png")
    print("  渲染完成")

    # 配音(可选): 无则静音; 旁白从布局自动生成
    print("[2/2] 合成 ...")
    if args.voice:
        from model_providers import ensure_env
        ensure_env()
        from qwen_tts import synth
        wav = str(tmp / "v.wav")
        if layout:
            parts = [layout.get("title", "")]
            for item in layout.get("items", []):
                s = item.get("main", "")
                if item.get("sub"):
                    s += "，" + item["sub"]
                parts.append(s)
            if layout.get("warn"):
                parts.append(layout["warn"])
            text = "。".join(parts) + "。"
        else:
            text = ("公司不经营了想注销，其实就三步。第一步账结清，该补的税补掉。"
                    "第二步公示四十五天，公告债权债务。第三步注销登记，先税务后工商。"
                    "拿到注销通知书，公司才算真正注销完。")
        synth(text, args.voice, wav, speech_rate=0.90, pitch_rate=1.0, volume=50)
        subprocess.run([FFMPEG, "-y", "-framerate", str(FPS), "-i", str(tmp / "f_%04d.png"),
                        "-i", wav, "-vf", "format=yuv420p", "-profile:v", "high",
                        "-c:v", "libx264", "-crf", "19", "-c:a", "aac", "-shortest", args.out],
                       capture_output=True, text=True)
    else:
        subprocess.run([FFMPEG, "-y", "-framerate", str(FPS), "-i", str(tmp / "f_%04d.png"),
                        "-vf", "format=yuv420p", "-profile:v", "high",
                        "-c:v", "libx264", "-crf", "19", "-an", args.out],
                       capture_output=True, text=True)
    print(f"成品: {args.out}")


if __name__ == "__main__":
    main()
