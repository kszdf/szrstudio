#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_motion_video.py — 幕后音模式图解视频生成器（v1 MVP）

链路: 定稿逐字稿 + 千问音频
      -> DeepSeek 拆分镜(每句选图卡模板+提取关键信息, 失败自动回退规则分镜)
      -> PIL 逐帧渲染 1080x1920 动画图卡(淡入/滑入/数字滚动/逐条打勾)
      -> 底部烧白字黑边字幕(与口播同步)
      -> ffmpeg 合成成品 mp4

与 make_avatar_video.py(数字人出镜) 并列, 不依赖 HEYGEM, 不排队, 纯本机渲染。

用法:
  D:/heygem/py310/Scripts/python.exe make_motion_video.py \
      --script qwen_out/批量5_暂估成本/03_逐字稿定稿.md \
      --audio  qwen_out/批量5_暂估成本/04_音频.wav \
      --out    output/motion_暂估成本.mp4 --title 暂估成本
  --no-llm   跳过 DeepSeek, 用内置规则分镜(断网/调试用)
  --preview N 只渲染前 N 帧再合成(快速看风格, 音频会截断)
"""
import argparse
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
FFMPEG = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"
TMP = BASE / "_tmp_motion"
FONT = str(BASE / "fonts/simhei.ttf")

W, H = 1080, 1920
FPS = 30
TRANS = 0.35          # 场景淡入时长(秒)

# —— 深色专业配色 ——
BG_TOP = (15, 23, 42)
BG_BOT = (30, 41, 59)
CARD = (30, 41, 59)
ACCENT = (245, 158, 11)     # 琥珀金: 强调/数字/勾
GREEN = (34, 197, 94)
RED = (239, 68, 68)
WHITE = (248, 250, 252)
MUTED = (148, 163, 184)
LINE = (51, 65, 85)

TEMPLATES = ["bigtext", "number", "compare", "checklist", "statement"]


# ============================== 字体 ==============================
_F = {}
def font(size):
    if size not in _F:
        _F[size] = ImageFont.truetype(FONT, size)
    return _F[size]


# ============================== 文本处理 ==============================
def clean_script(text):
    """剥离三段标记(=== 开头 ===), 得纯口播正文。"""
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("==="):
            continue
        lines.append(s)
    return "".join(lines)


def split_sentences(text):
    """按句末标点切大句; 大句内按逗号细分, 过短合并(与 build_package 同规则)。"""
    big = re.split(r"(?<=[。！？])", text)
    result = []
    for b in big:
        b = b.strip()
        if not b:
            continue
        subs = re.split(r"(?<=，)", b)
        buf = ""
        for s in subs:
            s = s.strip()
            if not s:
                continue
            if len(s) < 5 and buf:
                buf += s
            else:
                if buf:
                    result.append(buf)
                buf = s
        if buf:
            result.append(buf)
    return result


def wrap(seg, n=12):
    if len(seg) <= n:
        return [seg]
    return [seg[i:i + n] for i in range(0, len(seg), n)]


# ============================== 分镜(LLM + 规则回退) ==============================
SB_PROMPT = """你是财税口播短视频的分镜导演。下面是一段口播稿按序号切好的句子。
为每个句子选一个最合适的视觉图卡模板, 并从该句原文中提取关键信息(数字/条款/对比项必须原样取, 严禁改写)。

可用模板与字段:
- bigtext: 大字冲击卡, 适合开头钩子/警告/结论。fields: {"text": "≤10字"}
- number: 数字冲击卡, 句中有具体数字/期限/金额时用。fields: {"number": "原样数字如 500万 或 5月31日", "label": "≤8字标签", "sub": "≤14字说明"}
- compare: 对比卡, 句中有两种做法/两种后果对比时用。fields: {"left_title": "≤6字(危险做法)", "left_sub": "≤12字后果", "right_title": "≤6字(正确做法)", "right_sub": "≤12字后果"}
- checklist: 清单卡, 句中列举多个条件/材料/步骤时用。fields: {"title": "≤8字", "items": ["≤10字", "..."] (3-4项)}
- statement: 引用/结论卡, 陈述事实、期限、法条时用。fields: {"text": "≤20字原文摘录"}

输出: 严格输出 JSON 数组, 每句一个元素, 不要任何解释或代码块标记:
[{"idx": 0, "template": "bigtext", "fields": {"text": "xxx"}}, ...]

句子列表:
"""


def llm_storyboard(sentences):
    sys.path.insert(0, str(BASE))
    from model_providers import ensure_env, get_text_config, deepseek_chat
    ensure_env()
    cfg = get_text_config()
    listing = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences))
    prompt = SB_PROMPT + listing
    raw = deepseek_chat(prompt, model=cfg["model"], key=cfg["key"],
                        base_url=cfg["base_url"], timeout=90)
    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.M).strip()
    data = json.loads(raw)
    out = {}
    for item in data:
        idx = int(item.get("idx", -1))
        tpl = item.get("template", "")
        if 0 <= idx < len(sentences) and tpl in TEMPLATES:
            out[idx] = {"template": tpl, "fields": item.get("fields") or {}}
    # 补齐 LLM 漏掉的句子
    for i in range(len(sentences)):
        if i not in out:
            out[i] = rule_storyboard_one(i, sentences[i], len(sentences))
    return [out[i] for i in range(len(sentences))]


def rule_storyboard_one(idx, sent, total):
    """规则回退: 无 LLM 也能出一个过得去的分镜。"""
    nums = re.findall(r"\d+(?:\.\d+)?万?", sent)
    if idx == 0:
        return {"template": "bigtext", "fields": {"text": sent[:10]}}
    if idx == total - 1:
        return {"template": "bigtext", "fields": {"text": sent[:10]}}
    if len(re.split(r"[、，]", sent)) >= 4:
        items = [x.strip() for x in re.split(r"[、，]", sent) if x.strip()][:4]
        return {"template": "checklist", "fields": {"title": "关键要点", "items": items}}
    if nums:
        return {"template": "number",
                "fields": {"number": nums[0], "label": "关键数字",
                           "sub": sent[:14]}}
    if re.search(r"否则|不然|一旦|就是", sent):
        return {"template": "statement", "fields": {"text": sent[:20]}}
    return {"template": "statement", "fields": {"text": sent[:20]}}


# ============================== 渲染基元 ==============================
def ease(p):
    return 1 - (1 - max(0.0, min(1.0, p))) ** 3


def make_bg():
    """深蓝黑垂直渐变背景 + 顶部品牌区(静态, 每帧 copy)。"""
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    for y in range(H):
        t = y / H
        c = tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOT))
        d.line([(0, y), (W, y)], fill=c)
    # 顶部品牌
    d.text((W // 2, 92), "老张讲财税", font=font(46),
           fill=MUTED, anchor="mm")
    d.line([(W // 2 - 180, 150), (W // 2 + 180, 150)], fill=ACCENT, width=4)
    # 底部装饰线
    d.line([(80, H - 260), (W - 80, H - 260)], fill=LINE, width=2)
    return bg


def draw_subtitle(img, text):
    d = ImageDraw.Draw(img)
    lines = wrap(text, 12)
    size = 46
    line_h = size + 12
    y0 = H - 150 - line_h * len(lines)
    for i, ln in enumerate(lines):
        w = d.textlength(ln, font=font(size))
        x = (W - w) // 2
        y = y0 + i * line_h
        for dx in (-3, 0, 3):
            for dy in (-3, 0, 3):
                if dx or dy:
                    d.text((x + dx, y + dy), ln, font=font(size), fill=(0, 0, 0))
        d.text((x, y), ln, font=font(size), fill=WHITE)


def rounded(draw, box, radius, fill, outline=None, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill,
                           outline=outline, width=width)


# ============================== 五种图卡模板 ==============================
def tpl_bigtext(img, f, p):
    """大字冲击卡: 上滑+淡入, 下划线展开。"""
    d = ImageDraw.Draw(img)
    text = str(f.get("text", ""))[:12]
    size = 150 if len(text) <= 6 else (120 if len(text) <= 9 else 96)
    dy = int((1 - ease(p)) * 80)
    alpha = ease(p)
    cy = 880 + dy
    col = tuple(int(c * alpha + BG_TOP[i] * (1 - alpha)) for i, c in enumerate(WHITE))
    d.text((W // 2, cy), text, font=font(size), fill=col, anchor="mm")
    # 琥珀下划线展开
    lw = int(ease(p) * 420)
    if lw > 0:
        d.line([(W // 2 - lw // 2, cy + size // 2 + 36),
                (W // 2 + lw // 2, cy + size // 2 + 36)], fill=ACCENT, width=10)


def tpl_number(img, f, p):
    """数字卡: 数字滚动 count-up。"""
    d = ImageDraw.Draw(img)
    pe = ease(p)
    num = str(f.get("number", ""))
    label = str(f.get("label", ""))
    sub = str(f.get("sub", ""))
    d.text((W // 2, 620), label, font=font(52), fill=MUTED, anchor="mm")
    # 数字部分 count-up(仅纯数值可行)
    m = re.match(r"^(\d+(?:\.\d+)?)(.*)$", num)
    shown = num
    if m:
        target = float(m.group(1))
        suffix = m.group(2)
        isint = "." not in m.group(1)
        cur = target * pe
        shown = (f"{int(cur)}" if isint else f"{cur:.1f}") + suffix
    size = 150 if len(shown) <= 6 else 110
    d.text((W // 2, 860), shown, font=font(size), fill=ACCENT, anchor="mm")
    d.line([(W // 2 - 160, 1020), (W // 2 + 160, 1020)], fill=LINE, width=3)
    if sub:
        a = max(0.0, (p - 0.45) / 0.55)
        if a > 0:
            col = tuple(int(c * ease(a)) for c in MUTED)
            d.text((W // 2, 1100), sub[:16], font=font(44), fill=col, anchor="mm")


def tpl_compare(img, f, p):
    """对比卡: 上(红 危险) 下(绿 安全) 两块滑入。"""
    d = ImageDraw.Draw(img)
    p1 = ease(min(1.0, p / 0.6))
    p2 = ease(max(0.0, (p - 0.35) / 0.65))
    dx1 = int((1 - p1) * 220)
    dx2 = int((1 - p2) * 220)
    box1 = (100 - dx1, 560, W - 100 - dx1, 860)
    box2 = (100 + dx2, 920, W - 100 + dx2, 1220)
    rounded(d, box1, 28, (60, 26, 26), outline=RED, width=3)
    rounded(d, box2, 28, (16, 52, 36), outline=GREEN, width=3)
    d.text((140 - dx1, 610), "✗ " + str(f.get("left_title", "错误"))[:8],
           font=font(58), fill=RED, anchor="lm")
    d.text((140 - dx1, 700), str(f.get("left_sub", ""))[:14],
           font=font(42), fill=(252, 165, 165), anchor="lm")
    d.text((140 + dx2, 970), "✓ " + str(f.get("right_title", "正确"))[:8],
           font=font(58), fill=GREEN, anchor="lm")
    d.text((140 + dx2, 1060), str(f.get("right_sub", ""))[:14],
           font=font(42), fill=(134, 239, 172), anchor="lm")


def tpl_checklist(img, f, p):
    """清单卡: 逐条滑入打勾。"""
    d = ImageDraw.Draw(img)
    title = str(f.get("title", "关键要点"))
    d.text((W // 2, 560), title, font=font(64), fill=WHITE, anchor="mm")
    d.line([(W // 2 - 150, 630), (W // 2 + 150, 630)], fill=ACCENT, width=6)
    items = [str(x)[:10] for x in (f.get("items") or [])][:4]
    if not items:
        items = ["要点一", "要点二", "要点三"]
    step = 1.0 / max(len(items), 1)
    for i, it in enumerate(items):
        pi = ease((p - i * step * 0.7) / (step * 0.7 + 0.0001))
        if pi <= 0:
            continue
        dx = int((1 - pi) * 120)
        y = 760 + i * 150
        col = tuple(int(c * pi) for c in WHITE)
        d.ellipse((150 + dx, y - 26, 202 + dx, y + 26), outline=ACCENT, width=5)
        if pi > 0.55:
            d.line([(163 + dx, y), (180 + dx, y + 15)], fill=ACCENT, width=6)
            d.line([(180 + dx, y + 15), (208 + dx, y - 14)], fill=ACCENT, width=6)
        d.text((240 + dx, y), it, font=font(52), fill=col, anchor="lm")


def tpl_statement(img, f, p):
    """引用/结论卡: 大引号 + 居中文字滑入。"""
    d = ImageDraw.Draw(img)
    pe = ease(p)
    d.text((120, 480), "“", font=font(160), fill=ACCENT, anchor="lm")
    text = str(f.get("text", ""))[:24]
    lines = wrap(text, 10)
    dy = int((1 - pe) * 60)
    y = 860 + dy
    col = tuple(int(c * pe + BG_TOP[i] * (1 - pe)) for i, c in enumerate(WHITE))
    for i, ln in enumerate(lines):
        d.text((W // 2, y + i * 110), ln, font=font(84), fill=col, anchor="mm")
    d.text((W // 2, y + len(lines) * 110 + 70), "—— 口播原文",
           font=font(36), fill=MUTED, anchor="mm")


TPLS = {"bigtext": tpl_bigtext, "number": tpl_number, "compare": tpl_compare,
        "checklist": tpl_checklist, "statement": tpl_statement}


# ============================== 主流程 ==============================
def timeline(sentences, dur):
    """按字数权重给每句分配起止时间。"""
    total = sum(len(s) for s in sentences) or 1
    usable = max(dur - 0.6, 0.5)
    t = 0.3
    out = []
    for s in sentences:
        sd = max(usable * len(s) / total, 1.0)
        out.append((t, t + sd))
        t += sd
    return out


def main():
    ap = argparse.ArgumentParser(description="幕后音图解视频生成器 v1")
    ap.add_argument("--script", required=True, help="逐字稿定稿 md")
    ap.add_argument("--audio", required=True, help="千问音频 wav")
    ap.add_argument("--out", required=True, help="成品 mp4")
    ap.add_argument("--title", default="图解视频")
    ap.add_argument("--no-llm", action="store_true", help="跳过 LLM, 规则分镜")
    args = ap.parse_args()

    script_path, audio, out = Path(args.script), Path(args.audio), Path(args.out)
    text = clean_script(script_path.read_text(encoding="utf-8"))
    sentences = split_sentences(text)
    if not sentences:
        sys.exit("稿子解析后为空")

    # 音频时长
    r = subprocess.run([FFMPEG.replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(audio)],
                       capture_output=True, text=True)
    dur = float(r.stdout.strip())
    tl = timeline(sentences, dur)
    print(f"[1/5] 稿件 {len(sentences)} 句, 音频 {dur:.1f}s")

    # 分镜
    if args.no_llm:
        sb = [rule_storyboard_one(i, s, len(sentences)) for i, s in enumerate(sentences)]
        print("[2/5] 规则分镜(跳过 LLM)")
    else:
        try:
            sb = llm_storyboard(sentences)
            print("[2/5] DeepSeek 分镜完成")
        except Exception as e:
            print(f"[2/5] LLM 分镜失败({e}), 回退规则分镜")
            sb = [rule_storyboard_one(i, s, len(sentences)) for i, s in enumerate(sentences)]
    # 分镜落盘供审查
    sb_path = out.with_suffix(".storyboard.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    sb_path.write_text(json.dumps(
        [{"idx": i, "start": tl[i][0], "end": tl[i][1], "sentence": sentences[i], **sb[i]}
         for i in range(len(sentences))], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"      分镜已存: {sb_path}")

    # 逐帧渲染
    frames_dir = TMP / f"frames_{uuid.uuid4().hex[:8]}"
    frames_dir.mkdir(parents=True, exist_ok=True)
    n = int(dur * FPS)
    bg = make_bg()
    print(f"[3/5] 渲染 {n} 帧 @ {FPS}fps ...")
    for i in range(n):
        t = i / FPS
        # 定位当前场景(首句 0.3s 前视为第 0 句未开始)
        cur = len(tl) - 1
        for k, (s0, s1) in enumerate(tl):
            if t < s1:
                cur = k
                break
        s0, s1 = tl[cur]
        local = max(0.0, t - s0)
        p = min(1.0, local / 0.6)          # 场景内动画进度
        img = bg.copy()
        TPLS[sb[cur]["template"]](img, sb[cur]["fields"], p)
        # 场景开头淡入(与纯背景混合)
        if local < TRANS:
            a = ease(local / TRANS)
            card = bg.copy()
            TPLS[sb[cur]["template"]](card, sb[cur]["fields"], p)
            img = Image.blend(bg, card, a)
        # 字幕(首句 0.3s 内不显示)
        if t >= tl[0][0]:
            draw_subtitle(img, sentences[cur])
        img.save(frames_dir / f"f_{i:05d}.png", "PNG")
        if i % 60 == 0 or i == n - 1:
            print(f"      渲染 {int(100 * (i + 1) / n)}%")
    print("[3/5] 渲染完成")

    # ffmpeg 合成
    print("[4/5] ffmpeg 合成 ...")
    mid = frames_dir / "mid.mp4"
    cmd = [FFMPEG, "-y", "-r", str(FPS), "-i", str(frames_dir / "f_%05d.png"),
           "-i", str(audio), "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-ar", "44100", "-shortest", str(mid)]
    rr = subprocess.run(cmd, capture_output=True, text=True)
    if rr.returncode != 0:
        sys.exit("合成失败:\n" + rr.stderr[-800:])

    # 拼品牌片头(若有)
    intro = BASE / "covers/intro.mp4"
    if intro.exists():
        fc = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[v0];"
            "[1:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[v1];"
            "[0:a]aresample=44100[a0];[1:a]aresample=44100[a1];"
            "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
        )
        cmd2 = [FFMPEG, "-y", "-i", str(intro), "-i", str(mid), "-filter_complex", fc,
                "-map", "[v]", "-map", "[a]", "-pix_fmt", "yuv420p", str(out)]
        rr2 = subprocess.run(cmd2, capture_output=True, text=True)
        if rr2.returncode != 0:
            sys.exit("片头拼接失败:\n" + rr2.stderr[-800:])
        print("[5/5] 已拼品牌片头")
    else:
        mid.replace(out)
        print("[5/5] 无片头, 直接输出")

    # 清理帧目录
    import shutil
    shutil.rmtree(frames_dir, ignore_errors=True)
    print(f"\n✅ 成品: {out}  ({out.stat().st_size // 1024} KB)")
    print(f"   分镜审查: {sb_path}")


if __name__ == "__main__":
    main()
