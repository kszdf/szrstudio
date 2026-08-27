# -*- coding: utf-8 -*-
"""
auto_edit.py — 嵌套自动剪辑后处理（P2 专业版）

对 make_scroll_video.py 产出的成片做「剪辑风格」后处理，与「字幕风格」正交组合，
形成 3(剪辑) × 3(字幕) = 9 种最终成片风格。

专业升级（v2）：
  - 接收 --title 参数，片头卡烧入真实视频标题（准确传达主题，非通用品牌名）
  - 主片加 Ken Burns 微缩放运动（消除静态死板感，增加节奏/动感）
  - 片头/主片/主片/片尾衔接处加淡入淡出（替代硬切，视觉流畅）
  - 支持 --transition xfade（真交叉渐变）与 simple（可靠淡入淡出）两种转场模式；
    xfail 时自动降级，绝不阻断交付。

剪辑风格：
  fast      快节奏卡点   —— 品牌色动效片头卡(1.3s,含真实标题) + Ken Burns 主片 + CTA 片尾卡(1.3s)，快进快出
  artistic  文艺电影感   —— 上下黑边 letterbox + 暖色调 + 暗角 vignette，竖屏变电影画幅
  vlog      日常 vlog 风 —— 圆角 + 青色描边边框 + 左下角姓名条，像手机自拍 vlog 框
  pip       画中画混剪   —— 副视频圆角叠加右下角（出镜数字人片段等）

用法：
  python auto_edit.py --input in.mp4 --output out.mp4 --edit-style fast --title "老板最容易踩的坑"
  python auto_edit.py --input in.mp4 --output out.mp4 --edit-style vlog --name-tag "追梦 · 数字人"
  python auto_edit.py --input in.mp4 --output out.mp4 --edit-style fast --transition xfade   # 真交叉渐变
  python auto_edit.py --input in.mp4 --output out.mp4 --edit-style fast --dry-run
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

# ----------------------------------------------------------------- 路径 / 常量
HERE = os.path.dirname(os.path.abspath(__file__))
FFMPEG = os.environ.get("FFMPEG_BIN", r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe")
FFPROBE = os.environ.get("FFPROBE_BIN", r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffprobe.exe")
FONT_PATH = "C:/Windows/Fonts/simhei.ttf"
if not os.path.exists(FONT_PATH):
    for _fp in ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                "/System/Library/Fonts/PingFang.ttc"):
        if os.path.exists(_fp):
            FONT_PATH = _fp
            break

W, H = 1080, 1920          # 竖屏 9:16
CARD_T = 1.3               # 片头/片尾卡时长(秒)
CRF = 23
XF_DUR = 0.40              # 转场时长（秒），用于 xfade / 淡入淡出

EDIT_STYLES = ("fast", "artistic", "vlog", "pip")
TRANSITIONS = ("simple", "xfade")


# ----------------------------------------------------------------- PIL 卡片/遮罩
def _load_font(size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()


def _make_gradient(size, c1, c2):
    """竖向线性渐变背景 (RGB)。"""
    from PIL import Image, ImageDraw
    w, h = size
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    r1, g1, b1 = c1
    r2, g2, b2 = c2
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=(int(r1 + (r2 - r1) * t),
                                       int(g1 + (g2 - g1) * t),
                                       int(b1 + (b2 - b1) * t)))
    return img


def _draw_centered(d, box, text, font, fill, line_h=None):
    """在 box=(x0,y0,x1,y1) 内垂直居中、自动换行绘制多行文本。"""
    x0, y0, x1, y1 = box
    line_h = line_h or font.getbbox("测")[3] + 18
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur); cur = ""; continue
        cur += ch
        if d.textlength(cur, font=font) > (x1 - x0):
            lines.append(cur); cur = ""
    if cur:
        lines.append(cur)
    total = line_h * len(lines)
    y = y0 + (y1 - y0 - total) / 2
    for ln in lines:
        tw = d.textlength(ln, font=font)
        d.text((x0 + (x1 - x0 - tw) / 2, y), ln, font=font, fill=fill)
        y += line_h


def _wrap_text(text, draw, font, max_w):
    """按像素宽度换行（中文按字）。返回行列表。"""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur); cur = ""; continue
        test = cur + ch
        if draw.textlength(test, font=font) > max_w and cur:
            lines.append(cur); cur = ch
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def _fit_text(draw, text, max_w, avail_h, base=240):
    """自适应字号：从大到小试，保证不溢出宽度、行数≤3、总高≤可用高。返回(font, lines, lh)。"""
    for size in (base, 200, 170, 140, 110, 84, 70):
        f = _load_font(size)
        lines = _wrap_text(text or "", draw, f, max_w)
        lh = f.getbbox("测")[3] + 16
        if len(lines) <= 3 and lh * len(lines) <= avail_h:
            if max((draw.textlength(ln, font=f) for ln in lines)) <= max_w + 4:
                return f, lines, lh
    f = _load_font(60)
    lines = _wrap_text(text or "", draw, f, max_w)
    return f, lines, f.getbbox("测")[3] + 14


def _make_intro_card(path, title="追梦", subtitle="短视频智能生产平台",
                     tag="AI 数字人 · 一键成片"):
    """片头卡：真实标题大字 + 品牌副标 + 标签。"""
    from PIL import Image, ImageDraw
    img = _make_gradient((W, H), (79, 70, 229), (6, 182, 212))
    d = ImageDraw.Draw(img)
    # 半透明白色圆角面板
    d.rounded_rectangle([W // 2 - 380, H // 2 - 380, W // 2 + 380, H // 2 + 380],
                        radius=48, fill=(255, 255, 255, 28))
    tf, title_lines, tlh = _fit_text(d, title, W // 2 + 340, H // 2 - 340, base=240)
    ty = H // 2 - 320
    for i, ln in enumerate(title_lines):
        w = d.textlength(ln, font=tf)
        d.text((W // 2 - w / 2, ty + i * tlh), ln, font=tf, fill=(255, 255, 255))
    _draw_centered(d, (150, H // 2 + 20, W - 150, H // 2 + 160),
                   subtitle, _load_font(72), (255, 255, 255))
    _draw_centered(d, (150, H // 2 + 220, W - 150, H // 2 + 320),
                   tag, _load_font(48), (220, 240, 255))
    img.convert("RGB").save(path)


def _make_outro_card(path, title="关注 昆山老张讲财税", subtitle="评论区扣「方案」，清单发你"):
    """片尾卡：关注账号 + 留资 CTA。"""
    from PIL import Image, ImageDraw
    img = _make_gradient((W, H), (15, 23, 42), (30, 41, 59))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([W // 2 - 300, H // 2 - 300, W // 2 + 300, H // 2 + 300],
                        radius=40, outline=(6, 182, 212), width=8, fill=(255, 255, 255, 14))
    _draw_centered(d, (120, H // 2 - 260, W - 120, H // 2 + 40),
                   title, _load_font(150), (255, 255, 255))
    _draw_centered(d, (120, H // 2 + 120, W - 120, H // 2 + 260),
                   subtitle, _load_font(64), (203, 213, 225))
    img.convert("RGB").save(path)


def _make_round_mask(path, radius=70):
    from PIL import Image, ImageDraw
    m = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(m).rounded_rectangle([0, 0, W - 1, H - 1], radius=radius,
                                        fill=(255, 255, 255, 255))
    m.save(path)


def _make_name_tag(path, text="追梦 · 数字人"):
    from PIL import Image, ImageDraw
    tw, th = 560, 130
    img = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, tw - 1, th - 1], radius=65,
                        fill=(15, 23, 42, 200), outline=(6, 182, 212, 255), width=4)
    f = _load_font(54)
    w = d.textlength(text, font=f)
    d.text(((tw - w) / 2, (th - f.getbbox("测")[3]) / 2 - 4), text, font=f, fill=(255, 255, 255))
    img.save(path)


# ----------------------------------------------------------------- 工具
def _run(cmd, dry_run, log):
    if dry_run:
        log.append(" ".join(cmd))
        return 0
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log.append("FFMPEG_FAIL: " + (r.stderr or "")[:800])
    return r.returncode


def _card_to_mp4(png, out_mp4, dry_run, log):
    cmd = [FFMPEG, "-y", "-loop", "1", "-i", png, "-t", str(CARD_T), "-r", "30",
           "-vf", f"fade=in:0:8,fade=out:29:{int(CARD_T*30)-8},format=yuv420p",
           "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "20", out_mp4]
    return _run(cmd, dry_run, log)


def _probe_duration(video):
    """取视频时长（秒）。失败返回 0。"""
    try:
        out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                              "-of", "json", video], capture_output=True, text=True, timeout=20)
        return float(json.loads(out.stdout).get("format", {}).get("duration", 0) or 0)
    except Exception:
        return 0.0


# ----------------------------------------------------------------- 三种风格（保持原有逻辑不变）
def build_artistic(in_mp4, out_mp4, tmp, dry_run, log):
    vf = ("scale=1080:1920:force_original_aspect_ratio=decrease,"
          "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
          "drawbox=x=0:y=0:w=1080:h=200:color=black:t=fill,"
          "drawbox=x=0:y=1720:w=1080:h=200:color=black:t=fill,"
          "colorbalance=rs=0.10:gs=-0.02:bs=-0.10,"
          "vignette=0.28:0.5,format=yuv420p")
    return _run([FFMPEG, "-y", "-i", in_mp4, "-vf", vf,
                 "-c:v", "libx264", "-crf", "22", "-pix_fmt", "yuv420p",
                 "-c:a", "copy", out_mp4], dry_run, log)


def build_vlog(in_mp4, out_mp4, tmp, dry_run, log, name_tag="追梦 · 数字人"):
    mask_p = os.path.join(tmp, "mask.png")
    tag_p = os.path.join(tmp, "tag.png")
    if not dry_run:
        _make_round_mask(mask_p)
        _make_name_tag(tag_p, name_tag)
    else:
        log.append(f"PIL: round_mask -> {mask_p}")
        log.append(f"PIL: name_tag({name_tag}) -> {tag_p}")
    fc = ("[0:v]format=rgba[vm];"
          "[1:v]format=rgba[mask];"
          "[vm][mask]alphamerge[r];"
          "[r]pad=1108:1948:14:14:color=0x06B6D4[padded];"
          "[padded][2:v]overlay=40:(H-170)[out]")
    return _run([FFMPEG, "-y", "-i", in_mp4, "-i", mask_p, "-i", tag_p,
                 "-filter_complex", fc, "-map", "[out]",
                 "-c:v", "libx264", "-crf", str(CRF), "-pix_fmt", "yuv420p",
                 "-c:a", "copy", out_mp4], dry_run, log)


def build_pip(in_mp4, out_mp4, tmp, dry_run, log, overlay=None):
    if not overlay or not os.path.exists(overlay):
        log.append("PIP 跳过：未提供有效的 overlay 副视频")
        return 1
    mask_p = os.path.join(tmp, "pipmask.png")
    if not dry_run:
        _make_round_mask(mask_p, radius=60)
    else:
        log.append(f"PIL: pip_round_mask -> {mask_p}")
    fc = ("[1:v]scale=345:-1[ovs];"
          "[2:v]format=rgba,scale=345:-1[mask_s];"
          "[ovs]format=rgba[ovr];"
          "[ovr][mask_s]alphamerge[ovrounded];"
          "[0:v][ovrounded]overlay=W-w-40:(H-h-40)[out]")
    return _run([FFMPEG, "-y", "-i", in_mp4, "-i", overlay, "-i", mask_p,
                 "-filter_complex", fc, "-map", "[out]",
                 "-c:v", "libx264", "-crf", str(CRF), "-pix_fmt", "yuv420p",
                 "-c:a", "copy", out_mp4], dry_run, log)


# ----------------------------------------------------------------- Fast 风格（专业版：标题 + Ken Burns + 平滑转场）
def _build_kenburns_main(in_mp4, tmp, dry_run, log, transition="simple"):
    """对主片做 Ken Burns 缩放 + 淡入淡出边界处理。
    返回 (processed_main_path, duration_sec) 或 (None, 0)。"""
    dur = _probe_duration(in_mp4)
    if dur <= 0:
        log.append("KENBURNS_SKIP: 无法获取主片时长")
        return None, 0.0
    out = os.path.join(tmp, "main_kb.mp4")

    if transition == "xfade":
        # xfade 模式：仅 Ken Burns，不做 fade（由外层 xfade 处理转场）
        vf = (f"scale=1080:1920:force_original_aspect_ratio=decrease,"
              f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
              f"setsar=1,"
              f"zoompan=z='min(1.06,1.02+0.0006*on)':d=1:"
              f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
              f"s=1080x1920:fps=30,"
              f"format=yuv420p")
    else:
        # simple 模式：Ken Burns + 边界淡入淡出（伪平滑过渡）
        fi = min(XF_DUR, dur * 0.08)
        fo = min(XF_DUR, dur * 0.08)
        start_t = round(dur - fo, 3)
        vf = (f"scale=1080:1920:force_original_aspect_ratio=decrease,"
              f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
              f"setsar=1,"
              f"zoompan=z='min(1.06,1.02+0.0006*on)':d=1:"
              f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
              f"s=1080x1920:fps=30,"
              f"fade=t=in:st=0:d={fi:.2f},"
              f"fade=t=out:st={start_t:.2f}:d={fo:.2f},"
              f"format=yuv420p")

    rc = _run([FFMPEG, "-y", "-i", in_mp4, "-vf", vf,
               "-c:v", "libx264", "-crf", str(CRF), "-pix_fmt", "yuv420p",
               "-an", out], dry_run, log)
    return (out, dur) if rc == 0 else (None, 0.0)


def _final_mux(silent, audio_src, out, bgm, dry_run, log):
    """视频(silent，含片头/片尾卡拼接) + 原音频(audio_src) 合成。
    可选 BGM：低音量(0.10≈-20dB)混入，开头淡入、成片结束前淡出，不盖人声。"""
    if bgm and os.path.exists(bgm):
        dur = _probe_duration(audio_src) or 30.0
        fade_out = max(2.0, dur - 3.0)
        fc = (
            "[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[v0];"
            f"[2:a]volume=0.10,afade=t=in:d=2,afade=t=out:st={fade_out:.2f}:d=3[bgm];"
            "[v0][bgm]amix=inputs=2:duration=first:normalize=0[a]"
        )
        cmd = [FFMPEG, "-y", "-i", silent, "-i", audio_src, "-i", bgm,
               "-filter_complex", fc, "-map", "0:v:0", "-map", "[a]",
               "-c:v", "copy", "-c:a", "aac", out]
    else:
        cmd = [FFMPEG, "-y", "-i", silent, "-i", audio_src,
               "-map", "0:v:0", "-map", "1:a:0?",
               "-c:v", "copy", "-c:a", "aac", out]
    return _run(cmd, dry_run, log)


def _build_fast_simple(intro_m, main_kb, outro_m, in_mp4, out_mp4, dry_run, log, bgm=None):
    """Simple 转场：concat（硬切但被 fade 掩盖）+ 音频接回 + 可选 BGM。
    intro_m=None 时跳过片头（主片 + 片尾卡 两段拼接）。"""
    silent = os.path.join(os.path.dirname(main_kb), "silent_concat.mp4")
    if intro_m:
        rc = _run([FFMPEG, "-y", "-i", intro_m, "-i", main_kb, "-i", outro_m,
                    "-filter_complex",
                    "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
                    "-map", "[v]", "-c:v", "libx264", "-crf", str(CRF),
                    "-pix_fmt", "yuv420p", silent], dry_run, log)
    else:
        rc = _run([FFMPEG, "-y", "-i", main_kb, "-i", outro_m,
                    "-filter_complex",
                    "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                    "-map", "[v]", "-c:v", "libx264", "-crf", str(CRF),
                    "-pix_fmt", "yuv420p", silent], dry_run, log)
    if rc != 0:
        return rc
    # 合成音频（含可选 BGM）。注意：不用 -shortest——否则音频(主片)短于视频(主片+片尾卡)时片尾卡被截掉。
    return _final_mux(silent, in_mp4, out_mp4, bgm, dry_run, log)


def _build_fast_xfade(intro_m, main_kb, outro_m, in_mp4, out_mp4, dur, dry_run, log):
    """Xfade 转场：真交叉渐变（高级模式，需精确时序对齐音频）。"""
    audio_tmp = os.path.join(os.path.dirname(main_kb), "main.aac")
    if not dry_run:
        # 提取主片音频
        _run([FFMPEG, "-y", "-i", in_mp4, "-vn", "-c:a", "aac", "-b:a", "128k", audio_tmp],
             False, log)

    xd = XF_DUR
    o1 = max(0.01, CARD_T - xd)           # intro→main 开始交叉时刻
    o2 = max(o1 + 0.1, CARD_T + dur - 2 * xd)  # main→outro 开始交叉时刻

    # 视频：三段 xfade 串联
    vfc = (
        f"[0:v]format=yuv420p[introv];"
        f"[1:v]format=yuv420p[mainv];"
        f"[2:v]format=yuv420p[outrov];"
        f"[introv][mainv]xfade=transition=fade:duration={xd:.2f}:offset={o1:.2f}[x1];"
        f"[x1][outrov]xfade=transition=fade:duration={xd:.2f}:offset={o2:.2f}[vout]"
    )
    # 音频：静音(前段) + 主片音频 + 静音(后段)，总时长匹配视频
    total_v = CARD_T + dur + CARD_T - 2 * xd
    pre_a = max(0, o1)
    post_a = max(0, total_v - (o1 + dur))
    afc = (f"anullsrc=r=44100:cl=stereo:duration={pre_a:.2f}[sp];"
           f"[sp][3:a]concat=n=2:v=0:a=1[a_mid];"
           f"anullsrc=r=44100:cl=stereo:duration={post_a:.2f}[ep];"
           f"[a_mid][ep]concat=n=2:v=0:a=1[aout]")

    cmd = [FFMPEG, "-y",
           "-i", intro_m, "-i", main_kb, "-i", outro_m, "-i", audio_tmp,
           "-filter_complex", vfc, "-map", "[vout]",
           "-af", afc, "-map", "[aout]",
           "-c:v", "libx264", "-crf", str(CRF), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", out_mp4]
    return _run(cmd, dry_run, log)


def build_fast(in_mp4, out_mp4, tmp, dry_run, log,
               title="追梦", subtitle="短视频智能生产平台", transition="simple",
               no_intro=False, no_kenburns=False, bgm=None):
    """Fast 风格：片头卡(可跳过) + Ken Burns 主片(可跳过) + 平滑转场 + CTA 片尾卡。
    no_intro=True 跳过片头卡（成片自带片头）；no_kenburns=True 跳过缩放动效
    （数字人已自带运动，且缩放会裁切已烧录字幕导致 QC 贴边误判）。"""
    intro_p = os.path.join(tmp, "intro.png")
    outro_p = os.path.join(tmp, "outro.png")
    intro_m = os.path.join(tmp, "intro.mp4")
    outro_m = os.path.join(tmp, "outro.mp4")
    if not dry_run:
        if not no_intro:
            _make_intro_card(intro_p, title=title, subtitle=subtitle)
        _make_outro_card(outro_p)
    else:
        log.append(f"PIL: intro_card(title='{title}') -> {intro_p}" if not no_intro else "PIL: intro skipped (no_intro)")
        log.append(f"PIL: outro_card -> {outro_p}")

    rc = 0
    if not no_intro:
        rc |= _card_to_mp4(intro_p, intro_m, dry_run, log)
    rc |= _card_to_mp4(outro_p, outro_m, dry_run, log)
    if rc != 0:
        return rc

    # Ken Burns 处理主片（可跳过：数字人已自带运动，缩放会裁字幕）
    main_kb, dur = _build_kenburns_main(in_mp4, tmp, dry_run, log, transition) if not no_kenburns else (in_mp4, 0)
    if not main_kb:
        # Ken Burns 失败：降级为直通（不加效果，确保不阻断）
        log.append("KENBURNS_FALLBACK: 使用原始主片（无效果）")
        main_kb = in_mp4

    if no_intro:
        # 无片头：直接主片 + 片尾卡（intro_m=None）
        rc = _build_fast_simple(None, main_kb, outro_m, in_mp4, out_mp4, dry_run, log, bgm=bgm)
    elif transition == "xfade":
        rc = _build_fast_xfade(intro_m, main_kb, outro_m, in_mp4, out_mp4, dur, dry_run, log)
        if rc != 0:
            log.append("XFAD_FALLBACK: xfade 失败，降级为 simple 转场")
            rc = _build_fast_simple(intro_m, main_kb, outro_m, in_mp4, out_mp4, dry_run, log, bgm=bgm)
    else:
        rc = _build_fast_simple(intro_m, main_kb, outro_m, in_mp4, out_mp4, dry_run, log, bgm=bgm)
    return rc


# ----------------------------------------------------------------- 主流程
def auto_edit(in_mp4, out_mp4, edit_style="fast", name_tag="昆山老张讲财税",
              overlay=None, dry_run=False, title="", subtitle="",
              transition="simple", no_intro=False, no_kenburns=False, bgm=None):
    assert edit_style in EDIT_STYLES, f"edit_style must be one of {EDIT_STYLES}"
    assert transition in TRANSITIONS, f"transition must be one of {TRANSITIONS}"
    if not dry_run:
        assert os.path.exists(in_mp4), f"input not found: {in_mp4}"
    log = []
    tmp = tempfile.mkdtemp(prefix="auto_edit_")
    if edit_style == "fast":
        rc = build_fast(in_mp4, out_mp4, tmp, dry_run, log,
                        title=title or "昆山老张讲财税",
                        subtitle=subtitle or "财税干货 · 老板必看",
                        transition=transition, no_intro=no_intro, no_kenburns=no_kenburns,
                        bgm=bgm)
    elif edit_style == "vlog":
        rc = build_vlog(in_mp4, out_mp4, tmp, dry_run, log, name_tag=name_tag)
    elif edit_style == "pip":
        rc = build_pip(in_mp4, out_mp4, tmp, dry_run, log, overlay=overlay)
    else:
        rc = build_artistic(in_mp4, out_mp4, tmp, dry_run, log)
    manifest = {
        "input": in_mp4, "output": out_mp4, "edit_style": edit_style,
        "name_tag": name_tag, "title": title, "subtitle": subtitle,
        "transition": transition, "rc": rc, "dry_run": dry_run, "log": log,
    }
    with open(out_mp4 + ".edit.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return rc, manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--edit-style", default="fast", choices=EDIT_STYLES)
    ap.add_argument("--name-tag", default="昆山老张讲财税")
    ap.add_argument("--overlay", default=None,
                    help="pip 画中画模式：叠加到主片的副视频路径")
    ap.add_argument("--title", default="", help="片头卡显示的真实视频标题（fast 风格）")
    ap.add_argument("--subtitle", default="", help="片头卡副标题")
    ap.add_argument("--transition", default="simple", choices=TRANSITIONS,
                    help="转场模式: simple(淡入淡出+concat, 可靠) | xfade(真交叉渐变, 高级)")
    ap.add_argument("--no-intro", action="store_true",
                    help="跳过片头卡（成片已自带片头时使用，避免双重片头）")
    ap.add_argument("--no-kenburns", action="store_true",
                    help="跳过 Ken Burns 缩放（数字人已自带运动，且缩放会裁切已烧录字幕）")
    ap.add_argument("--bgm", default=None, help="背景音乐 WAV 路径（可选，低音量混入）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rc, man = auto_edit(args.input, args.output, args.edit_style,
                         args.name_tag, args.overlay, args.dry_run,
                         args.title, args.subtitle, args.transition,
                         args.no_intro, args.no_kenburns, args.bgm)
    if args.dry_run:
        print("\n".join(man["log"]))
    print(f"auto_edit[{args.edit_style}/{args.transition}] rc={rc} -> {args.output}")
    sys.exit(rc)


if __name__ == "__main__":
    main()
