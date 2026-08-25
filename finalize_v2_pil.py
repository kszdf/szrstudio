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
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 强制使用 full 版 ffmpeg（含 libx264），绕开系统 essentials 版缺编码器的坑
FFMPEG = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"

BASE = Path("D:/heygem_data/gpt_sovits")
INTRO = BASE / "covers/intro.mp4"
TMP = BASE / "_tmp_pil"
FRAMES = TMP / "frames"
FPS = 30

# 字幕样式（匹配 build_package.py 生成的 ass）
SUB_SIZE = 34              # 实际视频 720x1280 下的等效字号（从 42 调小，减少换行行数）
SUB_BORDER = 3             # 黑边宽度
SUB_MARGIN_BOTTOM = 46     # 底部边距（ass 80 in 1920 → 53.3）

# —— 字幕字体（多字体 fallback，根治 emoji/缺字乱码）——
# SimHei 不含 emoji 彩色字形，会把 ✅🔥💡 等渲染成方块(tofu)=视频字幕乱码；
# 因此 emoji 用 Segoe UI Emoji 渲染，生僻字用微软雅黑兜底，主字体仍是 SimHei。
def _load_font(path, size):
    p = Path(path)
    if p.exists():
        try:
            return ImageFont.truetype(str(p), size)
        except Exception:
            return None
    return None

F_MAIN = _load_font(str(BASE / "fonts/simhei.ttf"), SUB_SIZE)
F_EMOJI = _load_font("C:/Windows/Fonts/seguiemj.ttf", SUB_SIZE) or F_MAIN
F_FALLBACK = _load_font("C:/Windows/Fonts/msyh.ttc", SUB_SIZE) or F_MAIN
if F_MAIN is None:
    F_MAIN = ImageFont.load_default()


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        print("  [ERR] 命令失败:", " ".join(cmd[:6]), "...")
        print((r.stderr or "")[-800:])
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
        text = clean_subtitle_text(parts[9].replace(r"\N", "\n"))
        events.append((start, end, text))
    return events


import unicodedata

def _has_glyph(font, ch):
    """字符在该字体中是否有真实字形（bbox 为空=占位方块/tofu=会乱码）。"""
    try:
        return font.getmask(ch).getbbox() is not None
    except Exception:
        return False

def _is_emoji(ch):
    """判断是否为 emoji/符号字符（应交给 emoji 字体渲染，避免 SimHei 方块）。"""
    cp = ord(ch)
    if 0x1F000 <= cp <= 0x1FAFF:
        return True
    if 0x2600 <= cp <= 0x27BF:   # 杂项符号/dingbats：✅❌⚠️★☆
        return True
    if 0x2B00 <= cp <= 0x2BFF:   # 杂项符号和箭头
        return True
    if 0xFE00 <= cp <= 0xFE0F:   # 变体选择符（VS16 等）
        return True
    if cp == 0x200D:             # 零宽连字 ZWJ
        return True
    if unicodedata.category(ch) in ("So", "Cs"):
        return True
    return False

def _font_for(ch):
    """逐字符选字体：emoji→Segoe；中文/符号→SimHei；缺字→雅黑兜底。"""
    if _is_emoji(ch) and _has_glyph(F_EMOJI, ch):
        return F_EMOJI
    if _has_glyph(F_MAIN, ch):
        return F_MAIN
    for f in (F_FALLBACK, F_EMOJI):
        if _has_glyph(f, ch):
            return f
    return F_MAIN

def clean_subtitle_text(text):
    """清洗字幕文本：剥离 ASS 残留花括号标签、零宽/控制字符，避免被当字画上去乱码。"""
    # 去 {…} 标签（如 {\fnSimHei\fs64}、{\c&H...}）
    text = re.sub(r"\{[^}]*\}", "", text)
    # 去零宽/控制字符（BOM、零宽空格、软连字符等），保留正常换行与变体选择符
    text = "".join(
        ch for ch in text
        if ch == "\n" or (ord(ch) >= 0x20 and ord(ch) not in (0x200B, 0xFEFF, 0x00AD))
    )
    return text.strip()

def _char_width(draw, ch):
    return draw.textlength(ch, font=_font_for(ch))


def _wrap_text_to_width(draw, text, max_width):
    """按像素宽度自动换行，保持字符顺序，不破坏 karaoke 逐字同步。
    只在必要时拆分，已存在的 \n 换行会被保留。"""
    if not text:
        return text
    out_lines = []
    for raw_line in text.split("\n"):
        line_chars, line_w = [], 0.0
        for ch in raw_line:
            w = _char_width(draw, ch)
            # 单个字符过宽时强制换行兜底
            if line_w + w > max_width and line_chars:
                out_lines.append("".join(line_chars))
                line_chars, line_w = [ch], w
            else:
                line_chars.append(ch)
                line_w += w
        if line_chars:
            out_lines.append("".join(line_chars))
    return "\n".join(out_lines)


SUB_HILITE = (255, 212, 0)   # 逐字高亮色（金）：已读到/唱到的字高亮，未到的仍白
SUB_HMARGIN = 40             # 字幕左右安全边距（px）

# 关键词颜色标记（财税短视频：数字金额金色、风险词红色，其余白色）
_RISK_WORDS = ["稽查", "虚开", "补税", "补缴", "追缴", "罚款", "滞纳金", "坐牢",
               "刑责", "被查", "一查", "盯上", "查到", "补税", "风险"]
_DIGIT_RE = __import__("re").compile(r"[0-9]+(?:[.,][0-9]+)?[%万亿]?[元]?")


def _char_colors(text):
    """返回与 text 等长的颜色标记列表：'gold'=数字金额 / 'red'=风险词 / 'normal'=普通。"""
    n = len(text)
    colors = ["normal"] * n
    for m in _DIGIT_RE.finditer(text):
        for i in range(m.start(), m.end()):
            colors[i] = "gold"
    for w in _RISK_WORDS:
        start = 0
        while True:
            i = text.find(w, start)
            if i < 0:
                break
            for j in range(i, i + len(w)):
                colors[j] = "red"
            start = i + len(w)
    return colors


def draw_subtitle(img, text, style="minimal", karaoke_event=None, t=None):
    """在帧底部居中画字幕。
    - minimal：白字黑边（默认）
    - dynamic + karaoke_event：已读完的字符着金色高亮（卡拉OK式跟随配音）
    - bubble：半透明圆角气泡底衬，提升低反差场景可读性
    逐字符选字体，emoji 用 Segoe 渲染避免方块乱码。"""
    text = clean_subtitle_text(text)
    if not text:
        return
    colors = _char_colors(text)   # 关键词颜色标记（数字金/风险词红）
    draw = ImageDraw.Draw(img)
    W, H = img.size
    # 按实际帧宽度自动换行，防止长句溢出屏幕；karaoke 同步依赖字符顺序，换行不影响
    max_line_width = max(W - SUB_HMARGIN * 2, int(W * 0.86))
    text = _wrap_text_to_width(draw, text, max_line_width)
    lines = text.split("\n")
    line_h = SUB_SIZE + 8
    total_h = line_h * len(lines)
    y0 = H - SUB_MARGIN_BOTTOM - total_h
    if style == "dynamic" and not karaoke_event:
        print(f"  [WARN] dynamic 字幕缺少 karaoke sidecar，将回退为整句白字（不同步高亮）", file=sys.stderr)

    # 卡拉OK逐字状态：把 karaoke_event 的字符按朗读顺序摊平成判定序列
    kflat = []
    if karaoke_event and isinstance(karaoke_event, dict):
        for ln in karaoke_event.get("lines", []):
            for ch in ln:
                kflat.append(ch)

    # ── 逐行显示（数字人出镜专用，主流真人口播风格）──
    # 只显示"当前正在读的那一行"：读到第 2 行时第 1 行消失，读到第 3 行时第 2 行消失，
    # 永不累积堆叠。区别于滚动字幕的一次性多行堆叠。
    # 无 karaoke 时退化为"按时间均分逐行切换"（每行分配 (e-s)/行数 秒）。
    shown_start = 0
    shown_lines = len(lines)
    if karaoke_event and kflat and t is not None:
        # 找已读字符数（char 的 e 时间 <= 当前 t）
        read_n = 0
        for chd in kflat:
            if chd.get("e", 0) <= t:
                read_n += 1
            else:
                break
        # 该字符落在第几行（按 karaoke lines 的行边界）→ 只显示这一行
        row = 0
        acc = 0
        klines = karaoke_event.get("lines", []) or []
        for ri, ln in enumerate(klines):
            acc += len(ln)
            if read_n <= acc:
                row = ri
                break
        shown_start = row
        shown_lines = row + 1
    elif t is not None and karaoke_event and karaoke_event.get("start") is not None:
        ev_start = float(karaoke_event.get("start", 0))
        ev_end = float(karaoke_event.get("end", ev_start + 3))
        ev_dur = max(0.3, ev_end - ev_start)
        per_line = ev_dur / max(1, len(lines))
        row = min(len(lines) - 1, int((t - ev_start) / per_line))
        shown_start = row
        shown_lines = row + 1
    # 行数上限保护（>2 行时等比例上移，避免顶到数字人下巴/画面中部）
    if len(lines) > 2:
        y0 = max(120, y0 - (len(lines) - 2) * line_h // 2)

    char_idx = [0]
    ci = [0]   # 颜色索引（与 karaoke 的 char_idx 独立，映射 colors）
    for i, ln in enumerate(lines):
        if i < shown_start or i >= shown_lines:
            continue   # 只显示当前行（口播逐行风格）
        widths = [_char_width(draw, ch) for ch in ln]
        tw = sum(widths)
        x = (W - tw) // 2
        # 只显示当前行：固定在底部同一位置（行切换不跳动，口播字幕常见做法）
        y = H - SUB_MARGIN_BOTTOM - line_h
        # 半透明圆角底衬（默认开启，提升低反差场景可读性与高级感）
        pad = 14
        bx, by = x - pad, y - 10
        bw, bh = tw + pad * 2, line_h + 4
        try:
            draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=14,
                                   fill=(0, 0, 0, 120))
        except Exception:
            draw.rectangle([bx, by, bx + bw, by + bh], fill=(0, 0, 0, 120))
        cx = x
        for ch, w in zip(ln, widths):
            f = _font_for(ch)
            spoken = False
            if kflat and t is not None and char_idx[0] < len(kflat):
                spoken = t >= kflat[char_idx[0]].get("e", 0)
            char_idx[0] += 1
            kw = colors[ci[0]] if ci[0] < len(colors) else "normal"
            ci[0] += 1
            # 黑边（多次偏移）
            for dx in range(-SUB_BORDER, SUB_BORDER + 1):
                for dy in range(-SUB_BORDER, SUB_BORDER + 1):
                    if dx * dx + dy * dy <= SUB_BORDER * SUB_BORDER:
                        draw.text((cx + dx, y + dy), ch, font=f, fill=(0, 0, 0))
            # 填充色优先级：风险词红 > 数字金 > dynamic 已读金 > 白
            if kw == "red":
                fill = (255, 82, 82)
            elif kw == "gold":
                fill = (255, 200, 60)
            elif style == "dynamic" and spoken:
                fill = SUB_HILITE
            else:
                fill = (255, 255, 255)
            draw.text((cx, y), ch, font=f, fill=fill)
            cx += w


def extract_frames(video, frames_dir):
    # 每轮用独立临时帧目录：不删旧帧，既绕开安全删除 shim，也杜绝残留帧串味
    frames_dir.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-y", "-i", str(video), "-vf", f"fps={FPS}", str(frames_dir / "f_%05d.png")]
    run(cmd)
    frames = sorted(frames_dir.glob("f_*.png"))
    # 机制三：返回抽帧批次完成时间（烧帧后 mtime 更新 → 可识别已烧帧，续传跳过）
    import time as _time
    batch_mtime = max((p.stat().st_mtime for p in frames), default=_time.time())
    return frames, batch_mtime


def render_graphic_card(kind, title, data, pal=None):
    """数字人出镜时的智能图解卡（简洁深色风格，与数字人画面统一）。
    kind: 'number' 数据卡(大数字) / 'warn' 警示卡(红线词) / 'step' 流程卡(步骤) / 'scene' 场景卡(标题+说明)
    返回 RGBA 图（1080x1920）。"""
    from PIL import ImageDraw as _D
    if pal is None:
        pal = {"bg_top": (10, 16, 30), "bg_bot": (22, 30, 52), "accent": (220, 180, 60),
               "accent2": (80, 140, 220), "text": (235, 240, 248)}
    W, H = 1080, 1920
    img = Image.new("RGB", (W, H))
    d = _D.Draw(img)
    # 深色渐变背景
    for y in range(H):
        t = y / H
        c = tuple(int(a + (b - a) * t) for a, b in zip(pal["bg_top"], pal["bg_bot"]))
        d.line([(0, y), (W, y)], fill=c)
    # 顶部标题 + 品牌条
    f_t = ImageFont.truetype(str(BASE / "fonts/simhei.ttf"), 72)
    d.text((W // 2, 240), title[:12], font=f_t, fill=pal["text"], anchor="mm")
    d.rectangle([W // 2 - 120, 300, W // 2 + 120, 308], fill=pal["accent"])
    d.text((W // 2, 1760), "慧根堂财税 · 合规解读", font=ImageFont.truetype(str(BASE / "fonts/simhei.ttf"), 36),
           fill=(150, 158, 175), anchor="mm")
    if kind == "number":
        # 大数字居中
        big = str(data.get("num", ""))
        f_num = ImageFont.truetype(str(BASE / "fonts/simhei.ttf"), 200)
        d.text((W // 2, H // 2 - 120), big, font=f_num, fill=pal["accent"], anchor="mm")
        sub = str(data.get("sub", ""))[:14]
        d.text((W // 2, H // 2 + 120), sub, font=ImageFont.truetype(str(BASE / "fonts/simhei.ttf"), 52),
               fill=pal["text"], anchor="mm")
    elif kind == "warn":
        # 警示：红色警示块 + 关键词
        f_w = ImageFont.truetype(str(BASE / "fonts/simhei.ttf"), 64)
        d.rounded_rectangle([120, H // 2 - 160, W - 120, H // 2 + 160], radius=28,
                            fill=(60, 16, 20, 230), outline=(230, 80, 70), width=6)
        d.text((W // 2, H // 2 - 40), "⚠ 风险提示", font=ImageFont.truetype(str(BASE / "fonts/simhei.ttf"), 56),
               fill=(255, 120, 110), anchor="mm")
        d.text((W // 2, H // 2 + 70), str(data.get("kw", ""))[:18],
               font=f_w, fill=(255, 240, 240), anchor="mm")
    elif kind == "step":
        # 流程：纵向步骤
        steps = (data.get("steps") or [])[:4]
        y = H // 2 - 180
        f_s = ImageFont.truetype(str(BASE / "fonts/simhei.ttf"), 52)
        for i, st in enumerate(steps, 1):
            d.ellipse([200, y - 28, 264, y + 28], fill=pal["accent"])
            d.text((232, y), str(i), font=ImageFont.truetype(str(BASE / "fonts/simhei.ttf"), 40),
                   fill=(20, 26, 40), anchor="mm")
            d.text((320, y), str(st)[:16], font=f_s, fill=pal["text"], anchor="lm")
            y += 110
    else:  # scene
        f_b = ImageFont.truetype(str(BASE / "fonts/simhei.ttf"), 54)
        lines = _wrap_text_to_width(d, str(data.get("desc", ""))[:40], W - 240)
        yy = H // 2 - 80
        for ln in lines.split("\n")[:4]:
            d.text((W // 2, yy), ln, font=f_b, fill=pal["text"], anchor="mm")
            yy += 90
    return img


_MV4_MOD = None


def _get_motion_module():
    """加载 make_motion_video_v4 模块（模块级缓存，只加载一次——此前每帧 exec 导致烧字幕卡死）。"""
    global _MV4_MOD
    if _MV4_MOD is None:
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location("mv4", str(BASE / "make_motion_video_v4.py"))
        _MV4_MOD = _ilu.module_from_spec(spec)
        spec.loader.exec_module(_MV4_MOD)
    return _MV4_MOD


def _motion_graphic_frame(kind, title, data, local, scdur, idx):
    """数字人图解段：复用 make_motion_video_v4 的成熟图解渲染（真图表/AI生图/流程），
    替代此前的简化大字卡。返回 RGB 帧。"""
    try:
        mv = _get_motion_module()
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] motion 图解模块加载失败({e})，回退简化卡", file=sys.stderr)
        return render_graphic_card(kind, title, data)

    tone = data.get("tone", "neutral")
    pal = mv.get_palette(tone)
    sc = {"visual_type": kind, "title": title[:14], "tone": tone,
          "keywords": data.get("keywords") or []}
    try:
        if kind == "number":
            sc["highlight_num"] = str(data.get("num", ""))
            sc["num_sub"] = str(data.get("sub", ""))[:12]
            img = mv._render_number(sc, pal, 1.0, 1.0)
        elif kind == "table":
            sc["table"] = data.get("table") or {"head": [], "rows": []}
            img = mv._render_table(sc, pal, 1.0, 1.0)
        elif kind == "step":
            sc["steps"] = data.get("steps") or []
            img = mv._render_step(sc, pal, idx, 0.5, scdur, None)
        elif kind == "quote":
            sc["quote_text"] = str(data.get("quote", ""))[:14]
            img = mv._render_quote(sc, pal, 1.0, 1.0)
        elif kind == "scene":
            # AI 生图场景卡：用万相生图（带缓存）；失败回退渐变
            import os as _os
            try:
                _os.environ.setdefault("DASHSCOPE_API_KEY", "")
                from model_providers import ensure_env
                ensure_env()
                jpg = mv.wanx_image(str(data.get("prompt", title)), _os.getenv("DASHSCOPE_API_KEY"))
                base = mv.cover_resize(mv.Image.open(jpg).convert("RGB"), mv.W, mv.H)
            except Exception:  # noqa: BLE001
                base = mv.fallback_img(tone)
            img = mv._render_scene(sc, pal, idx, 0.5, scdur, base)
        else:
            img = render_graphic_card(kind, title, data)
        return img.convert("RGB")
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] motion 图解渲染失败({e})，回退简化卡", file=sys.stderr)
        return render_graphic_card(kind, title, data)


def _overlay_graphic_card(base, card, gcard, local, g_dur):
    """数字人图解浮层：数字人保持出镜，图表卡半透明叠加，位置/大小按人脸自适应。
    - 人脸在左→浮层靠右；在右→靠左；居中→放下方；人脸大→浮层大（人近放大）
    - 淡入淡出：段首 0.25s 淡入、段尾 0.25s 淡出，避免生硬闪现"""
    W, H = base.size
    face = gcard.get("face")
    if face and len(face) == 4:
        fx, fy, fw, fh = face
        cx = fx + fw / 2
        # 浮层尺寸随人脸大小自适应（人近=脸大=浮层大）
        ow = int(fw * 1.15)
        ow = max(340, min(ow, int(W * 0.46)))
        oh = int(ow * 1.15)
        if cx < W * 0.4:
            ox, oy = W - ow - 60, int(H * 0.38)
        elif cx > W * 0.6:
            ox, oy = 60, int(H * 0.38)
        else:
            ox, oy = (W - ow) // 2, int(H * 0.52)
    else:
        # 无人脸信息：固定底部浮层（右下角，避开字幕区）
        ow, oh = 460, 529
        ox, oy = W - ow - 40, H - oh - 320
    # 缩放图表卡
    card_r = card.convert("RGBA").resize((ow, oh), Image.LANCZOS)
    # 淡入淡出
    fade = 1.0
    if local < 0.25:
        fade = local / 0.25
    elif local > 1 - 0.25:
        fade = (1 - local) / 0.25
    fade = max(0.0, min(1.0, fade))
    # 半透明叠加（alpha 通道控制整体透明度）
    card_r.putalpha(card_r.split()[3].point(lambda a: int(a * fade * 0.82)))
    base_rgba = base.convert("RGBA")
    base_rgba.alpha_composite(card_r, (ox, oy))
    return base_rgba.convert("RGB")


def burn_frames(events, frames, karaoke=None, style="minimal", graphics=None, batch_mtime=None):
    # 按 start 时间建立逐字高亮事件索引（与 parse_ass 事件同序同起止）
    kmap = {}
    if karaoke and isinstance(karaoke, dict):
        for ev in karaoke.get("events", []):
            kmap[round(float(ev.get("start", 0)), 2)] = ev
    # 图解时间轴：graphics = [{"start":..,"end":..,"kind":..,"title":..,"data":{...}}, ...]
    gfx = graphics or []
    print(f"  共 {len(frames)} 帧，{len(events)} 条字幕，{len(gfx)} 段图解，字幕风格={style}")
    n = len(frames)
    # 机制三：帧级续传——烧过的帧 mtime 晚于抽帧批次时间 → 跳过（中断重跑不重烧）
    if batch_mtime is None:
        import time as _t
        batch_mtime = _t.time()
    skipped = 0
    for i, png in enumerate(frames):
        t = i / FPS
        # 帧级续传：已烧过的帧（mtime 更新）跳过
        try:
            if png.stat().st_mtime > batch_mtime:
                skipped += 1
                continue
        except OSError:
            pass
        # 找当前时间字幕
        cur = None
        kev = None
        for s, e, txt in events:
            if s <= t < e:
                cur = txt
                kev = kmap.get(round(s, 2))
                break
        # 找当前时间图解（若有则用图解卡替换数字人画面，实现"按内容穿插智能图解"）
        gcard = None
        for g in gfx:
            if g.get("start", -1) <= t < g.get("end", -1):
                gcard = g
                break
        if cur or gcard:
            img = Image.open(png).convert("RGB")
            if gcard:
                # 图解浮层：数字人保持出镜，motion 图表卡半透明叠加（自适应避让人脸）
                g_start = float(gcard.get("start", 0))
                g_end = float(gcard.get("end", g_start + 3))
                g_dur = max(0.5, g_end - g_start)
                local = min(1.0, max(0.0, (t - g_start) / g_dur))
                card = _motion_graphic_frame(gcard.get("kind", "scene"), gcard.get("title", ""),
                                             gcard.get("data") or {}, local, g_dur, i)
                img = _overlay_graphic_card(img, card, gcard, local, g_dur)
            draw_subtitle(img, cur, style=style, karaoke_event=kev, t=t)
            img.save(png, "PNG")
        # 每 50 帧回报一次进度（后端解析 [3] 烧字幕 N%）
        if i % 50 == 0 or i == n - 1:
            pct = int(100 * (i + 1) / n)
            print(f"[3] 烧字幕 {pct}%")
    if skipped:
        print(f"[3] 帧级续传：跳过已烧 {skipped}/{n} 帧")


def compose_video(frames, audio, out, with_audio=True):
    frames_dir = frames[0].parent if frames else FRAMES
    cmd = [
        FFMPEG, "-y",
        "-r", str(FPS), "-i", str(frames_dir / "f_%05d.png"),
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
        FFMPEG, "-y", "-i", str(intro), "-i", str(mid),
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
    ap.add_argument("--subtitle-style", default="minimal",
                    choices=["dynamic", "minimal", "bubble"],
                    help="字幕风格：dynamic=逐字高亮 / minimal=纯净白字 / bubble=气泡底衬")
    ap.add_argument("--karaoke", default=None, help="逐字高亮时间轴 sidecar JSON")
    ap.add_argument("--font", default=None, help="字幕主字体路径（默认 fonts/simhei.ttf）")
    ap.add_argument("--graphics", default=None,
                    help="智能图解时间轴 JSON：[{\"start\":..,\"end\":..,\"kind\":\"number|warn|step|scene\",\"title\":..,\"data\":{...}}]")
    args = ap.parse_args()

    # 字幕字体：--font 指定则覆盖默认黑体（路径不存在回退默认）
    global F_MAIN
    if args.font and Path(args.font).exists():
        F_MAIN = _load_font(args.font, SUB_SIZE)
    elif args.font:
        print(f"[WARN] 字体路径不存在，回退默认黑体: {args.font}")

    video = Path(args.video)
    ass = Path(args.ass)
    if not video.exists():
        sys.exit(f"视频不存在: {video}")
    if not ass.exists():
        sys.exit(f"字幕不存在: {ass}")

    karaoke = None
    if args.karaoke and Path(args.karaoke).exists():
        try:
            karaoke = json.loads(Path(args.karaoke).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [WARN] 逐字高亮 sidecar 解析失败，回退纯净字幕: {e}")

    graphics = []
    if args.graphics and Path(args.graphics).exists():
        try:
            graphics = json.loads(Path(args.graphics).read_text(encoding="utf-8"))
            print(f"  智能图解时间轴: {len(graphics)} 段")
        except Exception as e:
            print(f"  [WARN] 图解时间轴解析失败，忽略: {e}")

    TMP.mkdir(parents=True, exist_ok=True)

    # 每轮独立帧目录，避免删旧帧触发安全删除 shim、也杜绝残留帧串味
    frames_dir = TMP / f"frames_{uuid.uuid4().hex[:8]}"
    print(f"\n[1/4] 抽帧 ...")
    frames, batch_mtime = extract_frames(video, frames_dir)

    print(f"\n[2/4] 解析字幕 ...")
    events = parse_ass(ass)
    print(f"  字幕事件: {len(events)} 条")
    if events:
        print(f"  时间范围: {events[0][0]:.2f}s - {events[-1][1]:.2f}s")

    print(f"\n[3/4] PIL 烧字幕 ...")
    burn_frames(events, frames, karaoke=karaoke, style=args.subtitle_style,
                graphics=graphics, batch_mtime=batch_mtime)

    print(f"\n[4/4] 合成视频 ...")
    mid = TMP / "mid.mp4"
    compose_video(frames, Path(args.replace_audio) if args.replace_audio else None,
                  mid, with_audio=bool(args.replace_audio))

    if args.no_intro or not Path(args.intro).exists():
        shutil.move(str(mid), str(args.out))
    else:
        concat_intro(Path(args.intro), mid, Path(args.out))
        try:
            mid.unlink(missing_ok=True)
        except Exception:
            pass

    # 清理临时帧（失败不致命）
    try:
        shutil.rmtree(frames_dir, ignore_errors=True)
    except Exception:
        pass
    print(f"\n  成品: {args.out}")


if __name__ == "__main__":
    main()
