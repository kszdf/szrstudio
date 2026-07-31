#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
滚动字幕卡短视频生成器（不出镜 · 双声 · 逐字卡拉OK高亮 · 5行窗口滚动）

功能:
  - 逐句 TTS（男=张老师克隆音 zhangc2 / 女=江老师克隆音 jiangnv3，均 cosyvoice-v3-plus）
  - 真实音频时长驱动时间轴，画面当前句逐字与声音同步渐亮（灰→金黄）
  - 屏幕固定 5 行文字窗口：当前句在底部，其余 4 句（已读）向上滚动，读毕滚出顶部消失
  - 不显示"张老师/女声主播"任何角色标签，仅以音色区分男女声
  - 底部品牌条「慧根堂·老张讲财税」
  - 默认背景：浅色海景沙滩拍滚动画（numpy 程序化生成，海水轻轻在沙滩拍滚）
    --bg-style blackgold 切黑金流动；--bg <图片> 用任意静态图作底

用法:
  python make_scroll_video.py --dialogue demo_dialogue_v2.txt --out output/video/scroll.mp4
  python make_scroll_video.py --dialogue x.txt --out x.mp4 --bg-style blackgold
  python make_scroll_video.py --dialogue x.txt --out x.mp4 --bg covers/bg_seaside.png
  python make_scroll_video.py --dialogue x.txt --out x.mp4 --dry-tts   # 跳过真实TTS（用静音占位）快速验证画面

依赖: Pillow, numpy, ffmpeg(全量), dashscope(真实TTS)
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

# 让 qwen_tts 在导入时就把 DASHSCOPE_API_KEY 灌进环境变量
try:
    from qwen_tts import synth as _qwen_synth, DEFAULT_VOICE_ID as _DEFAULT_MALE
except Exception as e:  # pragma: no cover
    print(f"[WARN] 无法导入 qwen_tts: {e}")
    _qwen_synth = None
    _DEFAULT_MALE = "cosyvoice-v3-plus-zhangc2-28a7c3541e1c45518a03046c11baeb1d"

# 角色音色（定稿）
MALE_VOICE = "cosyvoice-v3-plus-zhangc2-28a7c3541e1c45518a03046c11baeb1d"
MALE_MODEL = "cosyvoice-v3-plus"
FEMALE_VOICE = "cosyvoice-v3-plus-jiangnv3-991b204c1d564ac7a60f0cb9a8fd78bd"
FEMALE_MODEL = "cosyvoice-v3-plus"

# 画布
W, H = 1080, 1920
FPS = 24
FONT_PATH = os.path.join(HERE, "fonts", "simhei.ttf")

# 字幕窗口布局（竖屏中央偏上区域，5 行槽位）
PANEL_TOP = 560
PANEL_BOTTOM = 1560
N_ROWS = 5
ROW_H = (PANEL_BOTTOM - PANEL_TOP) / N_ROWS  # 200
MAX_W = W - 120  # 文字最大宽度（留出描边余量，避免长句贴边/溢出）
LEFT_X = 80      # 字幕统一左对齐起始 x（右侧留 40px 余量，安全不溢出）

# 字号（竖屏1080宽下，当前句约11-12字/行，符合视频号每行12-15字规范；醒目不溢出）
HL_SIZE = 92       # 当前句（卡拉OK）：白字黑边，最醒目
HIST_SIZE = 70     # 历史句
STROKE_W = 5       # 白字黑描边，保证浅/深背景都清晰可读（全网规范首推白字黑边）
STROKE_FILL = (0, 0, 0)   # 黑边

# 颜色：还原 8385 经典「白字黑边」清晰风格（全网规范首推：任何背景都清晰），关键词红色高亮
SUB_DONE = (255, 255, 255)   # 当前句已读：纯白（卡拉OK 高亮扫过，最清晰）
SUB_TODO = (205, 212, 226)   # 当前句未读：浅蓝灰，提示"待读"，与纯白形成逐字高亮对比
HIST_RGB = (190, 198, 214)   # 历史句基础：淡蓝灰（配合 alpha 逐渐淡出）
BRAND_RGB = (255, 255, 255)  # 品牌条

# 顶部固定标题（≤10字，自动生成，不随滚动；参照视频号常规大小，不喧宾夺主）
TITLE_MAX_CHARS = 10
TITLE_SIZE = 84               # 视频号常规标题字号（之前 200 过大，缩小到舒服比例）
TITLE_FILL = (255, 201, 64)   # 醒目金
TITLE_STROKE = (16, 30, 60)   # 深海军蓝描边
TITLE_STROKE_W = 4
TITLE_TOP = 84                   # 标题区顶部 y（缩小与正文之间的留白）
TITLE_RULE = (255, 188, 48)      # 标题下装饰金线
TITLE_RULE_W = 4
TITLE_BAND = (16, 30, 60)        # 标题底纹条（深蓝，增强视觉冲击力）
TITLE_BAND_A = 205               # 底纹条不透明度
TITLE_PAD_Y = 12                 # 底纹条上下内边距（缩小，避免"点的位置空间"过大）

# 副标题（出片界面填写，标题下方一行小字说明，如"建筑财税·避坑指南"）
SUBTITLE_SIZE = 38
SUBTITLE_FILL = (255, 201, 64)     # 与标题同金，保持统一视觉
SUBTITLE_STROKE = (16, 30, 60)     # 深海军蓝描边，浅背景可读
SUBTITLE_STROKE_W = 3
SUBTITLE_GAP = 16                 # 副标题与标题金线间距

# 强调高亮（关键词 / **...** 手动标记）：统一红色，覆盖黄色
EMPH_DONE = (255, 40, 40)     # 当前句已读强调：红
EMPH_TODO = (255, 120, 120)   # 当前句未读强调：浅红
EMPH_HIST = (255, 55, 55)     # 历史句强调：红
KEYWORDS = ["虚开发票", "税务稽查", "金税四期", "公转私", "暂估成本", "个人卡流水",
            "个人卡", "偷税", "逃税", "留抵退税", "进项发票", "销项发票",
            "滞纳金", "税务风险", "隐匿收入"]

# 正文滚动字幕（提词器式：逐物理行，从上往下阅读）
# 当前行固定在中上部高亮；已读行在其上方逐行上移并淡出；未读行在其下方暗显待进入。
CURRENT_Y = 880                  # 当前（正在朗读）行的垂直中心，中上部（不靠下）
ROW_GAP = 172                    # 相邻物理行固定垂直间距（统一节奏，行与行分开）
MAX_HIST = 2                     # 当前行上方最多显示的历史行数（已读、向上淡出）
MAX_NEXT = 2                     # 当前行下方最多显示的未读行数（暗黄、待讲）
NEXT_SIZE = 62                   # 未读行字号（比当前行小，明显"待讲"态）
HIST_ALPHA_BASE = 230            # 最新历史行不透明度
HIST_ALPHA_STEP = 70             # 每往上一行透明度下降，最老行逐渐隐去
NEXT_ALPHA = 200                 # 未读行不透明度（暗黄、弱存在感）
NEXT_RGB = (135, 142, 158)        # 未读行暗黄，明显比当前行暗，提示"还没讲到"


# ---------------------------------------------------------------- 工具
def _cjk_wrap(draw, text, font, max_w):
    """纯中文/中英混排按字符贪婪换行（无空格分词）。
    额外修正：
      1. 遇到标点导致溢出时，优先把标点留在上一行末尾（允许 6% 轻微溢出），避免新行以标点开头。
      2. 禁止出现仅含标点的孤行；若有，合并回上一行（允许最多 12% 轻微溢出），保证视觉完整。"""
    lines, cur = [], ""
    PUNCT = "。，、；：！？）」』》.,;:!?)]}>"
    for ch in text:
        would_fit = draw.textlength(cur + ch, font=font) <= max_w
        if would_fit:
            cur += ch
        else:
            # 若溢出字符是标点，尝试把它“吸”到当前行末尾（允许 6% 轻微溢出），
            # 让下一行从非标点字符开始。
            if ch in PUNCT and cur and draw.textlength(cur + ch, font=font) <= max_w * 1.06:
                cur += ch
            else:
                if cur:
                    lines.append(cur)
                cur = ch
    if cur:
        lines.append(cur)
    # 后处理：禁止仅含标点的孤行，强制合并回上一行（单个标点不会导致明显溢出）
    cleaned = []
    for ln in lines:
        if cleaned and ln and all(c in PUNCT for c in ln):
            cleaned[-1] = cleaned[-1] + ln
            continue
        cleaned.append(ln)
    return cleaned


def _wrap_to_lines(draw, text, base_size, max_w, max_lines, min_size):
    """优先保证 行数<=max_lines 且 单行宽<=max_w；放不下则缩字号到 min_size，仍超则按 min_size 分行。"""
    from PIL import ImageFont
    size = base_size
    while size >= min_size:
        font = ImageFont.truetype(FONT_PATH, size)
        lines = _cjk_wrap(draw, text, font, max_w)
        if len(lines) <= max_lines:
            return lines, size
        size -= 2
    # 退到最小字号仍超：就用最小字号分行（可能超过 max_lines，但保证不溢出宽度）
    font = ImageFont.truetype(FONT_PATH, min_size)
    return _cjk_wrap(draw, text, font, max_w), min_size


def _draw_text_with_stroke(draw, xy, text, font, fill, stroke_w=STROKE_W, stroke_fill=(0, 0, 0)):
    draw.text(xy, text, font=font, fill=fill, stroke_width=stroke_w, stroke_fill=stroke_fill)


# ---------------------------------------------------------------- 强调 / 关键词高亮
def _clean_markers(text):
    """去掉 ** 标记，得到纯朗读文本（供 TTS 使用）。"""
    return text.replace("**", "")


def _split_by_keywords(chunk):
    """把 chunk 中命中的关键词切成强调段，其余为普通段。"""
    out, i, buf = [], 0, ""
    while i < len(chunk):
        hit = None
        for kw in KEYWORDS:
            if chunk.startswith(kw, i):
                hit = kw
                break
        if hit:
            if buf:
                out.append((False, buf)); buf = ""
            out.append((True, hit))
            i += len(hit)
        else:
            buf += chunk[i]; i += 1
    if buf:
        out.append((False, buf))
    return out


def split_emphasis(raw):
    """将文本拆成 (是否强调, 文本段) 列表。
    - 手动标记 **...** 视为强调
    - 其余文本自动高亮命中 KEYWORDS 的关键词
    """
    parts, i, buf = [], 0, ""
    while i < len(raw):
        if raw.startswith("**", i):
            if buf:
                parts.append((False, buf)); buf = ""
            j = raw.find("**", i + 2)
            if j == -1:
                buf = raw[i + 2:]; i = len(raw); break
            parts.append((True, raw[i + 2:j])); i = j + 2
        else:
            buf += raw[i]; i += 1
    if buf:
        parts.append((False, buf))
    out = []
    for emph, chunk in parts:
        if emph:
            out.append((True, chunk))
        else:
            out.extend(_split_by_keywords(chunk))
    return out


def _wrap_chars(draw, chars, font, max_w):
    """chars: list[(ch, emph)]，按宽度贪婪换行，保留每字强调标记。"""
    lines, cur, cw = [], [], 0.0
    for ch, emph in chars:
        w = draw.textlength(ch, font=font)
        if cw + w > max_w and cur:
            lines.append(cur); cur, cw = [], 0.0
        cur.append((ch, emph)); cw += w
    if cur:
        lines.append(cur)
    return lines


def _layout_chars(draw, raw, base_size, max_w, max_lines, min_size):
    """返回 (lines, fs)；lines: list[list[(ch, emph)]]，优先保证 行数<=max_lines。"""
    size = base_size
    while size >= min_size:
        font = ImageFont.truetype(FONT_PATH, size)
        segs = split_emphasis(raw)
        chars = [(c, e) for e, chunk in segs for c in chunk]
        lines = _wrap_chars(draw, chars, font, max_w)
        if len(lines) <= max_lines:
            return lines, size
        size -= 2
    font = ImageFont.truetype(FONT_PATH, min_size)
    segs = split_emphasis(raw)
    chars = [(c, e) for e, chunk in segs for c in chunk]
    return _wrap_chars(draw, chars, font, max_w), min_size


# ---------------------------------------------------------------- 背景动画（numpy）
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def gen_seaside(t, w=W, h=H):
    """浅色海景 + 沙滩潮汐拍滚（numpy 程序化，海水在沙滩轻轻拍滚）。"""
    yy = np.arange(h)[:, None].astype(np.float32)
    xx = np.arange(w)[None, :].astype(np.float32)
    img = np.empty((h, w, 3), dtype=np.float32)

    horizon = h * 0.55
    beach = h * 0.80

    # 天空：顶部浅蓝 -> 地平线近白
    sky_top = np.array([205, 232, 252], dtype=np.float32)
    sky_bot = np.array([232, 245, 255], dtype=np.float32)
    f = np.clip(yy / horizon, 0, 1)  # (h,1)
    sky = (sky_top[None, None, :] * (1 - f)[..., None]
           + sky_bot[None, None, :] * f[..., None])  # (h,1,3)

    # 海面：青蓝 + 流动波纹高光
    sea_base = np.array([70, 158, 205], dtype=np.float32)
    ripple = 26.0 * np.sin(xx * 0.012 + t * 0.9 + yy * 0.010)  # (h,w)
    sea = sea_base[None, None, :] + ripple[..., None]  # (h,w,3)
    # 近岸海面略浅
    shallow = np.clip((yy - horizon) / (beach - horizon), 0, 1) * 30  # (h,1)
    sea = sea + shallow[..., None]  # (h,1,1) broadcasts over (h,w,3)

    # 沙滩：米黄
    sand = np.array([238, 223, 184], dtype=np.float32)[None, None, :]  # (1,1,3)

    # 行选择掩码 (h,1,1)，可正确广播到 (h,w,3)
    m_sky = (yy < horizon)[..., None]
    m_sea = ((yy >= horizon) & (yy < beach))[..., None]
    bg = np.where(m_sky, sky, np.where(m_sea, sea, sand))

    # 潮汐泡沫带：在 beach 线附近随相位上下拍滚
    foam_y = beach + 22 * np.sin(t * 1.15 + xx * 0.006)  # (1,w)
    dist = np.abs(yy - foam_y)  # (h,w)
    foam = np.clip(1.0 - dist / 14.0, 0, 1)  # (h,w) 泡沫强度
    foam_rgb = np.array([252, 252, 250], dtype=np.float32)[None, None, :]
    bg = bg * (1 - foam[..., None]) + foam_rgb * foam[..., None]

    return np.clip(bg, 0, 255).astype(np.uint8)


def gen_black_gold(t, w=W, h=H):
    """黑金流动背景。"""
    yy = np.arange(h)[:, None].astype(np.float32)
    xx = np.arange(w)[None, :].astype(np.float32)
    val = 38 + 34 * np.sin((xx + yy) * 0.0045 + t * 0.8)
    val = np.clip(val, 0, 255)
    g = val[..., None]
    rgb = np.concatenate([g, g * 0.80, g * 0.22], axis=2)
    return np.clip(rgb, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- 对话解析 + TTS
def parse_dialogue(path):
    segs = []
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    return parse_dialogue_text(text)


def parse_dialogue_text(text):
    """从字符串解析 女：/男： 对话体，无角色前缀默认男声。"""
    segs = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("女") or line.startswith("女：") or line.startswith("女:"):
            role = "F"
            text = line[line.find("：") + 1:] if "：" in line else line[line.find(":") + 1:]
        elif line.startswith("男") or line.startswith("男：") or line.startswith("男:"):
            role = "M"
            text = line[line.find("：") + 1:] if "：" in line else line[line.find(":") + 1:]
        else:
            # 无角色前缀：默认男声
            role = "M"
            text = line
        text = text.strip()
        if text:
            segs.append((role, text))
    return segs


def synth_dialogue_audio(dialogue_text, out_wav, dry=False, gap=0.28,
                         female_voice=FEMALE_VOICE, female_model=FEMALE_MODEL,
                         male_voice=MALE_VOICE, male_model=MALE_MODEL,
                         male_rate=0.90, female_rate=0.98, male_pitch=0.95,
                         female_pitch=1.02, male_vol=53, female_vol=49):
    """独立合成男女双声对话音频（平台「出音频」对话试听用）。"""
    segs = parse_dialogue_text(dialogue_text)
    if not segs:
        raise SystemExit("对话稿为空或解析失败")
    tmpdir = tempfile.mkdtemp(prefix="scroll_audio_")
    try:
        seg_wavs, t = [], 0.0
        for i, (role, text) in enumerate(segs):
            wav = os.path.join(tmpdir, f"a_{i:03d}.wav")
            d = tts_one(text, role, wav, dry, female_voice, female_model, male_voice, male_model,
                         male_rate=male_rate, female_rate=female_rate, male_pitch=male_pitch,
                         female_pitch=female_pitch, male_vol=male_vol, female_vol=female_vol)
            seg_wavs.append(wav)
            t += d + (0 if i == len(segs) - 1 else gap)
        if len(seg_wavs) == 1:
            shutil.copy(seg_wavs[0], out_wav)
        else:
            build_audio(seg_wavs, gap, out_wav, tmpdir)
        return out_wav, segs
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _wav_duration(path):
    """用 ffprobe 取真实音频时长；某些 TTS 返回的 wav 头 nframes 异常，ffprobe 按实际数据解码更准确。"""
    ffprobe = r"D:\ffmpeg\ffmpeg-8.1.2-full_build\bin\ffprobe.exe"
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True)
        return float(r.stdout.strip())
    except Exception:
        with wave.open(path, "rb") as wf:
            return wf.getnframes() / wf.getframerate()


def tts_one(text, role, out_wav, dry, female_voice, female_model, male_voice, male_model,
            male_rate=0.96, female_rate=0.96, male_pitch=1.0, female_pitch=1.0,
            male_vol=52, female_vol=52):
    """逐句合成。支持分声线独立调感情/快慢：男声沉稳慢、女声略活泼。
    speech_rate 越低越慢；pitch_rate 越高越亮/尖；volume 为音量(0-100)。"""
    voice = female_voice if role == "F" else male_voice
    model = female_model if role == "F" else male_model
    speech_rate = female_rate if role == "F" else male_rate
    pitch_rate = female_pitch if role == "F" else male_pitch
    volume = female_vol if role == "F" else male_vol
    if dry or _qwen_synth is None:
        # 静音占位（2.4s），仅验证渲染/编码链路
        subprocess.run(
            [FFMPEG, "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
             "-t", "2.4", "-c:a", "pcm_s16le", out_wav],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 2.4
    try:
        # 分声线参数：男声 speech_rate 0.94 更沉稳权威；女声 1.0 略快亲和；pitch 微调冷暖
        _qwen_synth(_clean_markers(text), voice, out_wav, model=model,
                    speech_rate=speech_rate, pitch_rate=pitch_rate, volume=volume)
    except SystemExit:
        raise
    return _wav_duration(out_wav)


def build_audio(seg_wavs, gap, out_audio, tmpdir):
    """句间插入 gap 秒静音，用 concat demuxer 拼成总音频。"""
    gap_wav = os.path.join(tmpdir, "gap.wav")
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", f"anullsrc=r=22050:cl=mono",
         "-t", f"{gap:.3f}", "-c:a", "pcm_s16le", gap_wav],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    files = []
    for i, sw in enumerate(seg_wavs):
        files.append(sw)
        if i < len(seg_wavs) - 1:
            files.append(gap_wav)
    listfile = os.path.join(tmpdir, "audio_list.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        for p in files:
            f.write(f"file '{p.replace(chr(92), '/')}'\n")
    subprocess.run(
        [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy", out_audio],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_audio


# ---------------------------------------------------------------- 渲染
def _build_flat_lines(segs, starts, durs, draw):
    """把每段对话拆成物理行，并给每行按字数比例切分该段时长，分配时间区间。
    返回 list[dict]: seg, role, chars(list[(ch,emph)]), start, end,
                     char_off(段内字符偏移), seg_start, seg_dur, seg_total。
    这样渲染时可「逐物理行」像提词器一样滚动，而不是整段折多行一次性出现。
    """
    flat = []
    for i, (role, text) in enumerate(segs):
        raw = _clean_markers(text)
        lines, fs = _layout_chars(draw, raw, HL_SIZE, MAX_W, max_lines=3, min_size=58)
        # 兜底：长句在最小字号仍折行时，若最后一行只剩 ≤3 个字符，把它合并回上一行，
        # 避免画面出现孤零零的小尾巴（如"了。"）。轻微溢出由 MAX_W 的屏幕边距消化。
        if len(lines) >= 2 and len(lines[-1]) <= 3:
            lines[-2].extend(lines[-1])
            lines = lines[:-1]
        seg_dur = durs[i]
        seg_total = max(1, sum(len(ln) for ln in lines))
        acc = 0
        for ln in lines:
            n = len(ln)
            ls = starts[i] + (acc / seg_total) * seg_dur
            le = starts[i] + ((acc + n) / seg_total) * seg_dur
            flat.append({
                "seg": i, "role": role, "chars": ln,
                "start": ls, "end": le, "char_off": acc,
                "seg_start": starts[i], "seg_dur": seg_dur, "seg_total": seg_total,
                "size": fs,                       # 该段实际换行字号，渲染必须一致，否则溢出
            })
            acc += n
    return flat


def _find_current_line(flat, tc):
    """按内容时间 tc 定位当前物理行：落在 [start,end) 内即命中；否则取最后一个 start<=tc 的。"""
    for i, fl in enumerate(flat):
        if fl["start"] <= tc < fl["end"]:
            return i
    idx = 0
    for i, fl in enumerate(flat):
        if fl["start"] <= tc:
            idx = i
    return idx


def _fit_bg(im, mode="fill"):
    """把背景图按缩放模式适配到画布 (W,H)。
    fill(填充/cover)   : 等比放大到覆盖全屏，居中裁切（全幅铺满，无黑边，最常用）
    contain(适应)      : 等比缩放到完整可见，居中放置，多余处填黑（留黑边）
    stretch(拉伸)      : 直接拉满画布（可能变形，原默认行为）
    """
    im = im.convert("RGB")
    iw, ih = im.size
    if mode == "stretch":
        return im.resize((W, H))
    if mode == "fill":  # cover
        scale = max(W / iw, H / ih)
        nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
        scaled = im.resize((nw, nh))
        left = (nw - W) // 2
        top = (nh - H) // 2
        return scaled.crop((left, top, left + W, top + H))
    # contain
    scale = min(W / iw, H / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    scaled = im.resize((nw, nh))
    base = Image.new("RGB", (W, H), (0, 0, 0))
    base.paste(scaled, ((W - nw) // 2, (H - nh) // 2))
    return base


def render_frame(bg_rgb, flat, tc, t,
                 bg_static=None, bg_frames=None, bg_fps=1.0, intro=False,
                 title="", subtitle=""):
    """返回 PIL.Image（1080x1920），已叠加字幕窗口与品牌条。
    flat: _build_flat_lines 输出；tc: 内容时间轴（已扣除 intro 静音前缀）。
    渲染逻辑（提词器式，从上往下阅读）：
      当前行固定在 CURRENT_Y 高亮（卡拉OK 逐字）；
      已读行在其上方逐行上移并淡出；
      未读行在其下方暗显、待进入。
    """
    if bg_frames is not None:
        idx = int(t * bg_fps) % len(bg_frames)
        img = bg_frames[idx].copy().convert("RGBA")
    elif bg_static is not None:
        img = bg_static.copy().resize((W, H)).convert("RGBA")
    else:
        img = Image.fromarray(bg_rgb).convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")

    from PIL import ImageFont

    if intro:
        f = ImageFont.truetype(FONT_PATH, 72)
        _draw_text_with_stroke(draw, (W // 2 - draw.textlength("老张讲财税", font=f) / 2, H // 2 - 60),
                               "老张讲财税", f, (255, 255, 255), stroke_w=3)
        f2 = ImageFont.truetype(FONT_PATH, 40)
        _draw_text_with_stroke(draw, (W // 2 - draw.textlength("建筑财税·避坑指南", font=f2) / 2, H // 2 + 40),
                               "建筑财税·避坑指南", f2, (220, 230, 245), stroke_w=2)
        _draw_brand(draw)
        return img.convert("RGB")

    if not flat:
        _draw_brand(draw)
        if title:
            _draw_title(draw, title, subtitle)
        return img.convert("RGB")

    cur_idx = _find_current_line(flat, tc)
    fl = flat[cur_idx]

    # 当前行卡拉OK已读字符数：段内全局 done → 当前行局部 done
    seg_prog = (tc - fl["seg_start"]) / fl["seg_dur"] if fl["seg_dur"] > 0 else 0.0
    seg_prog = min(1.0, max(0.0, seg_prog))
    done_global = seg_prog * fl["seg_total"]
    local_done = int(min(len(fl["chars"]), max(0, done_global - fl["char_off"])))

    # 收集可见行：上方历史（已读、淡出） / 当前（高亮） / 下方未读（暗显）
    items = []  # (y, kind, flat_line, depth)
    hi = cur_idx - 1
    depth = 1
    while hi >= 0 and depth <= MAX_HIST:
        items.append((CURRENT_Y - depth * ROW_GAP, "hist", flat[hi], depth))
        depth += 1
        hi -= 1
    items.append((CURRENT_Y, "cur", fl, 0))
    ui = cur_idx + 1
    depth = 1
    while ui < len(flat) and depth <= MAX_NEXT:
        items.append((CURRENT_Y + depth * ROW_GAP, "next", flat[ui], depth))
        depth += 1
        ui += 1

    # 自上而下绘制（上方先画，下方后画；无重叠，纯为层次稳定）
    for y, kind, fll, depth in sorted(items, key=lambda it: it[0]):
        # 必须使用该行实际换行字号；长句被缩字号后若硬用大号会溢出屏幕
        base_size = fll.get("size", HL_SIZE)
        if kind == "cur":
            font = ImageFont.truetype(FONT_PATH, base_size)
            _draw_karaoke_line(draw, fll["chars"], y, font,
                               fll["char_off"], fll["char_off"] + local_done)
        elif kind == "hist":
            font = ImageFont.truetype(FONT_PATH, min(HIST_SIZE, base_size))
            alpha = max(40, HIST_ALPHA_BASE - (depth - 1) * HIST_ALPHA_STEP)
            _draw_history_line(draw, fll["chars"], y, font, alpha)
        else:  # next（未读）
            font = ImageFont.truetype(FONT_PATH, min(NEXT_SIZE, base_size))
            _draw_next_line(draw, fll["chars"], y, font, NEXT_ALPHA)

    _draw_brand(draw)
    if title:
        _draw_title(draw, title, subtitle)
    return img.convert("RGB")


def _draw_karaoke_line(draw, line, y, font, done_start, done_end):
    """绘制当前段落的单个物理行；done_start/end 是相对当前段落首字符的已读范围。"""
    lw = sum(draw.textlength(ch, font=font) for ch, _ in line)
    x = LEFT_X
    for idx, (ch, emph) in enumerate(line):
        gi = done_start + idx
        if done_start <= gi < done_end:
            col = EMPH_DONE if emph else SUB_DONE
        else:
            col = EMPH_TODO if emph else SUB_TODO
        _draw_text_with_stroke(draw, (x, y), ch, font, col, stroke_w=STROKE_W)
        x += draw.textlength(ch, font=font)


def _draw_history_line(draw, line, y, font, alpha=255):
    """绘制历史段落的单个物理行；alpha 越低越淡（已读越久越隐去）。"""
    lw = sum(draw.textlength(ch, font=font) for ch, _ in line)
    x = LEFT_X
    fill_base = HIST_RGB + (alpha,)
    emph_fill = EMPH_HIST + (alpha,)
    stroke_fill = STROKE_FILL + (alpha,)
    for ch, emph in line:
        _draw_text_with_stroke(draw, (x, y), ch, font,
                               emph_fill if emph else fill_base,
                               stroke_w=2, stroke_fill=stroke_fill)
        x += draw.textlength(ch, font=font)


def _draw_next_line(draw, line, y, font, alpha=255):
    """绘制未读行：暗黄、低存在感，提示"还没讲到"。"""
    lw = sum(draw.textlength(ch, font=font) for ch, _ in line)
    x = LEFT_X
    fill_base = NEXT_RGB + (alpha,)
    emph_fill = EMPH_HIST + (alpha,)
    stroke_fill = STROKE_FILL + (alpha,)
    for ch, emph in line:
        _draw_text_with_stroke(draw, (x, y), ch, font,
                               emph_fill if emph else fill_base,
                               stroke_w=2, stroke_fill=stroke_fill)
        x += draw.textlength(ch, font=font)


def _draw_brand(draw):
    from PIL import ImageFont
    f = ImageFont.truetype(FONT_PATH, 38)
    txt = "慧根堂 · 老张讲财税"
    x = (W - draw.textlength(txt, font=f)) / 2
    _draw_text_with_stroke(draw, (x, H - 96), txt, f, BRAND_RGB, stroke_w=2)


def _auto_title(segs):
    """自动生成 ≤10 字标题：取首句核心短语（去开场客套、截到首个断句标点前）。"""
    leadins = ["张老师，", "张老师:", "老师，", "老师:", "我想请教一下", "我想问一下",
               "我想咨询", "啊，", "哎，", "那个", "请问", "您说", "是这样的"]
    for role, text in segs:
        t = _clean_markers(text).strip()
        for p in leadins:
            if t.startswith(p):
                t = t[len(p):].strip()
        # 截到首个断句标点前，得到核心短语
        for sep in ["，", "？", "?", "。", "！"]:
            if sep in t:
                t = t.split(sep)[0]
                break
        t = t.strip().rstrip("呢吗吧啊呀哦呃")
        if t:
            return t[:TITLE_MAX_CHARS]
    return "慧根堂财税"


def _draw_title(draw, title, subtitle=""):
    """顶部固定标题：深蓝底纹条 + 大字(金+深蓝描边) + 金色装饰线，不随字幕滚动。
    可选副标题：标题金线下方一行小字（金+深蓝描边）。"""
    from PIL import ImageFont
    font = ImageFont.truetype(FONT_PATH, TITLE_SIZE)
    # 自动换行（按字符），最多 2 行，确保不超宽
    lines = _cjk_wrap(draw, title, font, MAX_W)[:2]
    line_h = int(TITLE_SIZE * 1.12)
    pad_y = TITLE_PAD_Y
    band_top = TITLE_TOP - pad_y
    band_bottom = TITLE_TOP + len(lines) * line_h + pad_y
    # 标题底纹条（深蓝半透明，整行宽，增强视觉冲击力）
    draw.rectangle([0, band_top, W, band_bottom],
                   fill=(TITLE_BAND[0], TITLE_BAND[1], TITLE_BAND[2], TITLE_BAND_A))
    # 标题文字（金 + 深蓝描边）
    y = TITLE_TOP
    widths = []
    for line in lines:
        lw = draw.textlength(line, font=font)
        widths.append(lw)
        x = (W - lw) / 2
        _draw_text_with_stroke(draw, (x, y), line, font, TITLE_FILL,
                               stroke_w=TITLE_STROKE_W, stroke_fill=TITLE_STROKE)
        y += line_h
    # 标题下装饰金线（宽度取最宽行，居中）
    rule_w = min(MAX_W, max(widths) + 36)
    ry = band_bottom + 6
    draw.line([(W / 2 - rule_w / 2, ry), (W / 2 + rule_w / 2, ry)],
              fill=TITLE_RULE, width=TITLE_RULE_W)
    # 副标题（金线下方一行，金+深蓝描边；自动换行最多 2 行）
    if subtitle and subtitle.strip():
        sfont = ImageFont.truetype(FONT_PATH, SUBTITLE_SIZE)
        slines = _cjk_wrap(draw, subtitle.strip(), sfont, MAX_W)[:2]
        sline_h = int(SUBTITLE_SIZE * 1.25)
        sy = ry + SUBTITLE_GAP
        for sl in slines:
            slw = draw.textlength(sl, font=sfont)
            _draw_text_with_stroke(draw, ((W - slw) / 2, sy), sl, sfont,
                                   SUBTITLE_FILL, stroke_w=SUBTITLE_STROKE_W,
                                   stroke_fill=SUBTITLE_STROKE)
            sy += sline_h


# ---------------------------------------------------------------- 主流程
def make_video(dialogue, out_path, bg_style="seaside", bg_image=None, dry=False,
               gap=0.28, no_intro=False, bgm=None, title=None, subtitle=None,
               bg_fit="fill",
               female_voice=FEMALE_VOICE, female_model=FEMALE_MODEL,
               male_voice=MALE_VOICE, male_model=MALE_MODEL,
               male_rate=0.90, female_rate=0.98, male_pitch=0.95,
               female_pitch=1.02, male_vol=53, female_vol=49):
    segs = parse_dialogue(dialogue)
    if not segs:
        raise SystemExit("对话文件为空或解析失败")
    title = title or _auto_title(segs)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmpdir = tempfile.mkdtemp(prefix="scroll_")

    # 1) TTS 逐句
    seg_wavs, starts, durs = [], [], []
    t = 0.0
    for i, (role, text) in enumerate(segs):
        wav = os.path.join(tmpdir, f"a_{i:03d}.wav")
        d = tts_one(text, role, wav, dry, female_voice, female_model, male_voice, male_model,
                    male_rate=male_rate, female_rate=female_rate, male_pitch=male_pitch,
                    female_pitch=female_pitch, male_vol=male_vol, female_vol=female_vol)
        seg_wavs.append(wav)
        starts.append(t)
        durs.append(d)
        t += d + (0 if i == len(segs) - 1 else gap)
    total = t if seg_wavs else 0
    # 预计算物理行时间轴（提词器式逐行滚动用）
    _tmp_draw = ImageDraw.Draw(Image.new("RGBA", (W, H)))
    flat = _build_flat_lines(segs, starts, durs, _tmp_draw)
    intro_dur = 0.0 if no_intro else 1.4

    audio_wav = os.path.join(tmpdir, "audio_total.wav")
    build_audio(seg_wavs, gap, audio_wav, tmpdir)
    # 修复开场不同步：intro 标题页期间音频应为静音，使音画时间轴对齐
    if intro_dur > 0:
        intro_sil = os.path.join(tmpdir, "intro_silence.wav")
        subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                        "-t", f"{intro_dur:.3f}", "-c:a", "pcm_s16le", intro_sil],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        final_audio = os.path.join(tmpdir, "audio_final.wav")
        listfile = os.path.join(tmpdir, "intro_list.txt")
        with open(listfile, "w", encoding="utf-8") as f:
            f.write(f"file '{intro_sil.replace(chr(92), '/')}'\n")
            f.write(f"file '{audio_wav.replace(chr(92), '/')}'\n")
        subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", listfile,
                        "-c", "copy", final_audio], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        audio_wav = final_audio

    # 2) 编码（rawvideo 管道）
    out_duration = intro_dur + total
    bg_static = None
    bg_frames = None
    bg_fps = 1.0
    if bg_image:
        if bg_image.lower().endswith(".gif"):
            im = Image.open(bg_image)
            bg_frames = []
            durations = []
            for i in range(im.n_frames):
                im.seek(i)
                durations.append(im.info.get("duration", 100))
                bg_frames.append(_fit_bg(im, bg_fit))
            avg_dur = sum(durations) / max(1, len(durations))
            bg_fps = 1000.0 / avg_dur if avg_dur > 0 else 10.0
        else:
            bg_static = _fit_bg(Image.open(bg_image).convert("RGB"), bg_fit)

    cmd = [FFMPEG, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", audio_wav]
    if bgm:
        cmd += ["-i", bgm, "-filter_complex", "[1:a][2:a]amix=inputs=2[a]", "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-crf", "23", "-c:a", "aac", "-b:a", "128k", "-shortest",
            "-movflags", "+faststart", out_path]

    ffmpeg_log = os.path.join(tmpdir, "ffmpeg.log")
    with open(ffmpeg_log, "w", encoding="utf-8") as flog:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=flog, stderr=subprocess.STDOUT)
        n_frames = int(out_duration * FPS) + 2
        frame_bytes = W * H * 3
        bg_func = gen_black_gold if bg_style == "blackgold" else gen_seaside
        try:
            for fi in range(n_frames):
                tt = fi / FPS
                if tt < intro_dur:
                    bg = (bg_func(tt) if (bg_static is None and bg_frames is None) else None)
                    frame = render_frame(bg, flat, 0.0, tt,
                                         bg_static=bg_static, bg_frames=bg_frames, bg_fps=bg_fps, intro=True, title=title, subtitle=subtitle or "")
                else:
                    tc = tt - intro_dur
                    if bg_static is None and bg_frames is None:
                        bg = bg_func(tc)
                    else:
                        bg = None
                    frame = render_frame(bg, flat, tc, tc,
                                         bg_static=bg_static, bg_frames=bg_frames, bg_fps=bg_fps, title=title, subtitle=subtitle or "")
                # 分块写入管道，避免单帧 6.2MB 直写触发 Windows 管道 EINVAL
                data = np.asarray(frame, dtype=np.uint8).tobytes()
                for off in range(0, frame_bytes, 1 << 20):
                    chunk = data[off:off + (1 << 20)]
                    if not chunk:
                        break
                    try:
                        proc.stdin.write(chunk)
                    except (BrokenPipeError, OSError):
                        # ffmpeg 已因 -shortest 结束，停止写帧
                        break
                if proc.poll() is not None:
                    break
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
        rc = proc.wait()
    if rc != 0:
        try:
            log_tail = open(ffmpeg_log, encoding="utf-8", errors="ignore").read()[-1500:]
        except Exception:
            log_tail = "(无法读取日志)"
        raise RuntimeError(f"ffmpeg 退出码 {rc}，部分日志：\n{log_tail}")
    shutil.rmtree(tmpdir, ignore_errors=True)
    size_kb = os.path.getsize(out_path) // 1024
    if bg_frames is not None:
        bg_label = "GIF动态背景"
    elif bg_static is not None:
        bg_label = "自定义背景"
    else:
        bg_label = bg_style
    print(f"成品: {out_path}  ({size_kb} KB)\n"
          f"   {W}x{H} 竖屏 | {bg_label} | "
          f"大字逐字高亮 | 段落分明 | 声画同步 | 不出镜")
    return out_path


def naturalize_dialogue(text):
    """用 DeepSeek 把书面双声稿改写为自然口语（加语气词、去 AI 播音腔）。
    严格保留 女：/男： 角色标记与行结构；任何失败都回退原稿，绝不阻塞出片。"""
    try:
        from model_providers import get_text_config, deepseek_chat
    except Exception as e:
        print(f"[WARN] 无法导入 model_providers，跳过自然化: {e}")
        return text
    try:
        cfg = get_text_config()
    except Exception as e:
        print(f"[WARN] 获取文本模型配置失败，跳过自然化: {e}")
        return text
    prompt = (
        "你是一位资深财税短视频脚本编辑。下面的双声对话稿由两位财税专家（老张=实战派税务顾问，女声=江老师=专业财税顾问）出镜讲解。\n"
        "任务：把生硬的书面稿，改写为「专家在咨询室里给客户娓娓道来」的自然口吻——专业、务实、可信赖，去除 AI 播音腔，但绝不是街边闲聊。\n"
        "严格要求：\n"
        "1. 必须严格保留每一行开头的角色标记「女：」或「男：」，不得增删角色、不得合并行、不得改变标记写法；\n"
        "2. 语气词要极度克制：只在关键转折处用极少量自然连接（如「咱们」「其实」「说白了」「你听我讲」「比方说」「对吧」），严禁使用「啊、嘛、呢、哎哟、好家伙、对喽」这类过于随意或夸张的口语；不要每句都加，保持专家professional感；\n"
        "3. 适度软化书面腔（如「应当」改「一般得」、「然而」改「不过」、「例如」改「比方说」、「进行核查」改「核对一下」），但必须保持财税专业准确性与术语规范，不编造数据、不改动原意、不丢专业权威感；\n"
        "4. 用逗号、句号制造自然停顿，像真人慢慢讲，不要一口气念完；长短句结合，有讲解节奏；\n"
        "5. 不得删除、合并或省略原稿任何一句，必须逐句对应改写，保持原句数量与顺序，仅做语气软化与极少量自然连接；\n"
        "6. 不要输出任何解释、不要加标题，只输出改写后的对话稿本身。\n\n"
        "原稿：\n" + text
    )
    try:
        out = deepseek_chat(prompt, cfg["model"], cfg["key"],
                            cfg.get("base_url", "https://api.deepseek.com"), timeout=90)
        out = out.strip()
        # 去掉可能的 ``` 代码块包裹
        if out.startswith("```"):
            parts = out.split("```")
            out = parts[1] if len(parts) > 1 else out
            if out[:4] in ("text", "txt", "对话", "原稿"):
                out = out.split("\n", 1)[1] if "\n" in out else out
        out = out.strip()
        # 校验：改写后若完全丢失角色标记，判定失败回退原稿
        if not any(m in out for m in ("女：", "男：", "女:", "男:")):
            print("[WARN] 自然化输出丢失角色标记，回退原稿")
            return text
        return out
    except Exception as e:
        print(f"[WARN] 自然化调用失败，使用原稿: {e}")
        return text


def main():
    ap = argparse.ArgumentParser(description="滚动字幕卡短视频生成（不出镜·双声·卡拉OK）")
    ap.add_argument("--dialogue", required=True, help="对话稿 txt（每行 女：/男： 开头）")
    ap.add_argument("--out", required=True, help="输出 mp4 路径")
    ap.add_argument("--bg-style", default="seaside", choices=["seaside", "blackgold"],
                    help="背景风格，固定为滚动海浪(seaside)，不再提供黑金等其他底色")
    ap.add_argument("--bg", default=None, help="自定义背景图片路径（覆盖 --bg-style）")
    ap.add_argument("--bg-fit", default="fill", choices=["fill", "contain", "stretch"],
                    help="背景缩放模式：fill=填充/覆盖(默认) contain=适应/留边 stretch=拉伸/变形")
    ap.add_argument("--dry-tts", action="store_true", help="跳过真实TTS，用静音占位快速验画面")
    ap.add_argument("--gap", type=float, default=0.28, help="句间静音秒数（0.28 给配音呼吸/停顿感，更像专家讲解）")
    ap.add_argument("--no-intro", action="store_true", help="不生成开头标题页")
    ap.add_argument("--bgm", default=None, help="背景音乐 mp3（可选，与配音混音）")
    ap.add_argument("--title", default=None, help="顶部固定标题（覆盖自动生成，≤10字最佳）")
    ap.add_argument("--subtitle", default=None, help="副标题（标题下方一行小字，如：建筑财税·避坑指南）")
    ap.add_argument("--female-voice", default=FEMALE_VOICE)
    ap.add_argument("--female-model", default=FEMALE_MODEL)
    ap.add_argument("--male-voice", default=MALE_VOICE)
    ap.add_argument("--male-model", default=MALE_MODEL)
    # 分声线感情/快慢（speech_rate 越低越慢；pitch_rate 越高越亮；volume 0-100）
    ap.add_argument("--male-rate", type=float, default=0.90, help="男声语速（默认0.90更慢、权威沉稳）")
    ap.add_argument("--female-rate", type=float, default=0.98, help="女声语速（默认0.98自然略快、亲和）")
    ap.add_argument("--male-pitch", type=float, default=0.95, help="男声音调（默认0.95更低沉老练）")
    ap.add_argument("--female-pitch", type=float, default=1.02, help="女声音调（默认1.02略亮、清晰）")
    ap.add_argument("--male-vol", type=int, default=53, help="男声音量(0-100，略高显权威)")
    ap.add_argument("--female-vol", type=int, default=49, help="女声音量(0-100)")
    ap.add_argument("--natural", action="store_true",
                    help="调用 DeepSeek 把书面稿自动改写为自然口语（加语气词、去AI感、像真人在叙事）")
    args = ap.parse_args()

    dialogue_arg = args.dialogue
    if args.natural:
        try:
            with open(args.dialogue, encoding="utf-8-sig") as _f:
                _raw = _f.read()
            _natural = naturalize_dialogue(_raw)
            _tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                               delete=False, encoding="utf-8")
            _tmp.write(_natural)
            _tmp.close()
            dialogue_arg = _tmp.name
            print(f"[INFO] 已自然化改写对话稿（{len(_raw)}→{len(_natural)}字），临时文件: {dialogue_arg}")
        except Exception as e:
            print(f"[WARN] 自然化预处理失败，使用原稿: {e}")

    make_video(dialogue_arg, args.out, bg_style=args.bg_style, bg_image=args.bg,
               dry=args.dry_tts, gap=args.gap, no_intro=args.no_intro, bgm=args.bgm,
               bg_fit=args.bg_fit,
               title=args.title, subtitle=args.subtitle,
               female_voice=args.female_voice, female_model=args.female_model,
               male_voice=args.male_voice, male_model=args.male_model,
               male_rate=args.male_rate, female_rate=args.female_rate,
               male_pitch=args.male_pitch, female_pitch=args.female_pitch,
               male_vol=args.male_vol, female_vol=args.female_vol)


if __name__ == "__main__":
    main()
