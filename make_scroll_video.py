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
MAX_W = W - 160  # 文字最大宽度

# 字号
HL_SIZE = 58      # 当前句（卡拉OK）
HIST_SIZE = 46    # 历史句
STROKE_W = 2

# 颜色（浅色海景底，深蓝半透明背板保证可读性）
PANEL_ALPHA = 150
PANEL_RGB = (12, 28, 54)
CUR_DONE = (255, 210, 74)    # 已读字符：金黄
CUR_TODO = (225, 235, 245)   # 未读字符：浅白
HIST_RGB = (126, 138, 160)   # 历史句：石板灰
BRAND_RGB = (255, 255, 255)  # 品牌条


# ---------------------------------------------------------------- 工具
def _cjk_wrap(draw, text, font, max_w):
    """纯中文/中英混排按字符贪婪换行（无空格分词）。"""
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
        for line in f:
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


def tts_one(text, role, out_wav, dry, female_voice, female_model, male_voice, male_model):
    voice = female_voice if role == "F" else male_voice
    model = female_model if role == "F" else male_model
    if dry or _qwen_synth is None:
        # 静音占位（2.4s），仅验证渲染/编码链路
        subprocess.run(
            [FFMPEG, "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
             "-t", "2.4", "-c:a", "pcm_s16le", out_wav],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 2.4
    try:
        _qwen_synth(text, voice, out_wav, model=model, speech_rate=1.0, pitch_rate=1.0, volume=50)
    except SystemExit:
        raise
    with wave.open(out_wav, "rb") as wf:
        fr = wf.getframerate()
        n = wf.getnframes()
    return n / fr


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
def render_frame(bg_rgb, segs, starts, durs, cur, prog, t, bg_image=None, intro=False):
    """返回 PIL.Image（1080x1920），已叠加字幕窗口与品牌条。"""
    if bg_image is not None:
        img = bg_image.copy()
        img = img.resize((W, H))
    else:
        img = Image.fromarray(bg_rgb)
    draw = ImageDraw.Draw(img, "RGBA")

    from PIL import ImageFont
    # 半透明深蓝字幕背板
    draw.rectangle([40, PANEL_TOP - 30, W - 40, PANEL_BOTTOM + 30],
                   fill=(PANEL_RGB[0], PANEL_RGB[1], PANEL_RGB[2], PANEL_ALPHA))

    if intro:
        f = ImageFont.truetype(FONT_PATH, 72)
        _draw_text_with_stroke(draw, (W // 2 - draw.textlength("老张讲财税", font=f) / 2, H // 2 - 60),
                               "老张讲财税", f, (255, 255, 255), stroke_w=3)
        f2 = ImageFont.truetype(FONT_PATH, 40)
        _draw_text_with_stroke(draw, (W // 2 - draw.textlength("建筑财税·避坑指南", font=f2) / 2, H // 2 + 40),
                               "建筑财税·避坑指南", f2, (220, 230, 245), stroke_w=2)
        _draw_brand(draw)
        return img

    # 窗口内可见句： [cur-4, cur]
    for s in range(max(0, cur - N_ROWS + 1), cur + 1):
        r = s - (cur - N_ROWS + 1)  # 0=顶部, N_ROWS-1=底部(当前)
        slot_y = PANEL_TOP + (r - prog) * ROW_H + ROW_H / 2  # 槽位垂直中心
        role, text = segs[s]
        if s == cur:
            _draw_karaoke(draw, text, slot_y, prog)
        else:
            _draw_history(draw, text, slot_y)

    _draw_brand(draw)
    return img


def _draw_karaoke(draw, text, slot_y, prog):
    from PIL import ImageFont
    lines, fs = _wrap_to_lines(draw, text, HL_SIZE, MAX_W, max_lines=2, min_size=44)
    font = ImageFont.truetype(FONT_PATH, fs)
    total = max(1, sum(len(l) for l in lines))
    done_n = int(prog * total)
    line_h = fs * 1.25
    y0 = slot_y - (len(lines) * line_h) / 2
    gi = 0
    for line in lines:
        lw = draw.textlength(line, font=font)
        x = (W - lw) / 2
        y = y0
        for ch in line:
            col = CUR_DONE if gi < done_n else CUR_TODO
            _draw_text_with_stroke(draw, (x, y), ch, font, col, stroke_w=STROKE_W)
            x += draw.textlength(ch, font=font)
            gi += 1
        y0 += line_h


def _draw_history(draw, text, slot_y):
    from PIL import ImageFont
    lines, fs = _wrap_to_lines(draw, text, HIST_SIZE, MAX_W, max_lines=2, min_size=36)
    font = ImageFont.truetype(FONT_PATH, fs)
    line_h = fs * 1.25
    y0 = slot_y - (len(lines) * line_h) / 2
    for line in lines:
        lw = draw.textlength(line, font=font)
        x = (W - lw) / 2
        _draw_text_with_stroke(draw, (x, y0), line, font, HIST_RGB, stroke_w=1)
        y0 += line_h


def _draw_brand(draw):
    from PIL import ImageFont
    f = ImageFont.truetype(FONT_PATH, 38)
    txt = "慧根堂 · 老张讲财税"
    x = (W - draw.textlength(txt, font=f)) / 2
    _draw_text_with_stroke(draw, (x, H - 96), txt, f, BRAND_RGB, stroke_w=2)


# ---------------------------------------------------------------- 主流程
def make_video(dialogue, out_path, bg_style="seaside", bg_image=None, dry=False,
               gap=0.18, no_intro=False, bgm=None,
               female_voice=FEMALE_VOICE, female_model=FEMALE_MODEL,
               male_voice=MALE_VOICE, male_model=MALE_MODEL):
    segs = parse_dialogue(dialogue)
    if not segs:
        raise SystemExit("对话文件为空或解析失败")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmpdir = tempfile.mkdtemp(prefix="scroll_")

    # 1) TTS 逐句
    seg_wavs, starts, durs = [], [], []
    t = 0.0
    for i, (role, text) in enumerate(segs):
        wav = os.path.join(tmpdir, f"a_{i:03d}.wav")
        d = tts_one(text, role, wav, dry, female_voice, female_model, male_voice, male_model)
        seg_wavs.append(wav)
        starts.append(t)
        durs.append(d)
        t += d + (0 if i == len(segs) - 1 else gap)
    total = t - gap if seg_wavs else 0

    audio_wav = os.path.join(tmpdir, "audio_total.wav")
    build_audio(seg_wavs, gap, audio_wav, tmpdir)

    # 2) 编码（rawvideo 管道）
    intro_dur = 0.0 if no_intro else 1.4
    out_duration = intro_dur + total
    bg_static = None
    if bg_image:
        bg_static = Image.open(bg_image).convert("RGB")

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
        try:
            for fi in range(n_frames):
                tt = fi / FPS
                if tt < intro_dur:
                    frame = render_frame(gen_seaside(tt) if not bg_static else None, segs,
                                         starts, durs, 0, 0, tt, bg_image=bg_static, intro=True)
                else:
                    tc = tt - intro_dur
                    # 定位当前句
                    cur = len(segs) - 1
                    for s in range(len(segs)):
                        if starts[s] <= tc <= starts[s] + durs[s]:
                            cur = s
                            break
                        if starts[s] + durs[s] < tc:
                            cur = s
                    prog = 0.0
                    if 0 <= cur < len(segs):
                        prog = min(1.0, max(0.0, (tc - starts[cur]) / durs[cur])) if durs[cur] > 0 else 0
                    if bg_style == "blackgold" and bg_static is None:
                        bg = gen_black_gold(tc)
                    elif bg_static is None:
                        bg = gen_seaside(tc)
                    else:
                        bg = None
                    frame = render_frame(bg, segs, starts, durs, cur, prog, tc, bg_image=bg_static)
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
    print(f"成品: {out_path}  ({size_kb} KB)\n"
          f"   {W}x{H} 竖屏 | {bg_style if not bg_static else '自定义背景'} | "
          f"大字逐字高亮 | 段落分明 | 声画同步 | 不出镜")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="滚动字幕卡短视频生成（不出镜·双声·卡拉OK）")
    ap.add_argument("--dialogue", required=True, help="对话稿 txt（每行 女：/男： 开头）")
    ap.add_argument("--out", required=True, help="输出 mp4 路径")
    ap.add_argument("--bg-style", default="seaside", choices=["seaside", "blackgold"])
    ap.add_argument("--bg", default=None, help="自定义背景图片路径（覆盖 --bg-style）")
    ap.add_argument("--dry-tts", action="store_true", help="跳过真实TTS，用静音占位快速验画面")
    ap.add_argument("--gap", type=float, default=0.18, help="句间静音秒数")
    ap.add_argument("--no-intro", action="store_true", help="不生成开头标题页")
    ap.add_argument("--bgm", default=None, help="背景音乐 mp3（可选，与配音混音）")
    ap.add_argument("--female-voice", default=FEMALE_VOICE)
    ap.add_argument("--female-model", default=FEMALE_MODEL)
    ap.add_argument("--male-voice", default=MALE_VOICE)
    ap.add_argument("--male-model", default=MALE_MODEL)
    args = ap.parse_args()

    make_video(args.dialogue, args.out, bg_style=args.bg_style, bg_image=args.bg,
               dry=args.dry_tts, gap=args.gap, no_intro=args.no_intro, bgm=args.bgm,
               female_voice=args.female_voice, female_model=args.female_model,
               male_voice=args.male_voice, male_model=args.male_model)


if __name__ == "__main__":
    main()
