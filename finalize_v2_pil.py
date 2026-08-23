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

    char_idx = [0]
    ci = [0]   # 颜色索引（与 karaoke 的 char_idx 独立，映射 colors）
    for i, ln in enumerate(lines):
        widths = [_char_width(draw, ch) for ch in ln]
        tw = sum(widths)
        x = (W - tw) // 2
        y = y0 + i * line_h
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
    return sorted(frames_dir.glob("f_*.png"))


def burn_frames(events, frames, karaoke=None, style="minimal"):
    # 按 start 时间建立逐字高亮事件索引（与 parse_ass 事件同序同起止）
    kmap = {}
    if karaoke and isinstance(karaoke, dict):
        for ev in karaoke.get("events", []):
            kmap[round(float(ev.get("start", 0)), 2)] = ev
    print(f"  共 {len(frames)} 帧，{len(events)} 条字幕，字幕风格={style}")
    n = len(frames)
    for i, png in enumerate(frames):
        t = i / FPS
        # 找当前时间字幕
        cur = None
        kev = None
        for s, e, txt in events:
            if s <= t < e:
                cur = txt
                kev = kmap.get(round(s, 2))
                break
        if cur:
            img = Image.open(png).convert("RGB")
            draw_subtitle(img, cur, style=style, karaoke_event=kev, t=t)
            img.save(png, "PNG")
        # 每 50 帧回报一次进度（后端解析 [3] 烧字幕 N%）
        if i % 50 == 0 or i == n - 1:
            pct = int(100 * (i + 1) / n)
            print(f"[3] 烧字幕 {pct}%")


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

    TMP.mkdir(parents=True, exist_ok=True)

    # 每轮独立帧目录，避免删旧帧触发安全删除 shim、也杜绝残留帧串味
    frames_dir = TMP / f"frames_{uuid.uuid4().hex[:8]}"
    print(f"\n[1/4] 抽帧 ...")
    frames = extract_frames(video, frames_dir)

    print(f"\n[2/4] 解析字幕 ...")
    events = parse_ass(ass)
    print(f"  字幕事件: {len(events)} 条")
    if events:
        print(f"  时间范围: {events[0][0]:.2f}s - {events[-1][1]:.2f}s")

    print(f"\n[3/4] PIL 烧字幕 ...")
    burn_frames(events, frames, karaoke=karaoke, style=args.subtitle_style)

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
