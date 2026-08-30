# -*- coding: utf-8 -*-
"""税务官方风格信息层叠加: 在 i2v 动效上叠加 顶部知识点标签 + 中部信息卡 + 底部署名条。
风格参考税务官方AI动画(《关公说税》《喵小鱼》): 深蓝税务主色 + 白字卡片 + 警示元素 + 关键数字放大。
用法: python _i2v_info.py --clips 幕1..幕5 --out out.mp4 [--voice wav]
每幕信息: --tags "幕1标签,幕2标签,..." --cards "幕1信息,幕2信息,..." --nums "幕1数字,幕2数字,..."
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
FFMPEG = "ffmpeg"
W, H, FPS = 1080, 1920, 30

_F = {}
def font(size):
    if size not in _F:
        _F[size] = ImageFont.truetype(str(BASE / "fonts/simhei.ttf"), size)
    return _F[size]

# 税务蓝主色
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
    # 警示icon + 标签文字
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
    # 左蓝条装饰
    d.rounded_rectangle([70, card_y0 + 22, 94, card_y1 - 22], radius=12, fill=BLUE)
    # 主文字(可换行)
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
    # 关键数字放大(底部横条)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", required=True, help="逗号分隔 5 段 i2v mp4")
    ap.add_argument("--tags", required=True, help="逗号分隔 每幕顶部知识点标签")
    ap.add_argument("--cards", required=True, help="逗号分隔 每幕信息卡主文字")
    ap.add_argument("--nums", required=True, help="逗号分隔 每幕关键数字(可空串)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    clips = [s.strip() for s in args.clips.split(",") if s.strip()]
    tags = [s.strip() for s in args.tags.split(",")]
    cards = [s.strip() for s in args.cards.split(",")]
    nums = [s.strip() for s in args.nums.split(",")]
    assert len(clips) == len(tags) == len(cards) == len(nums), \
        f"数量不匹配: clips={len(clips)} tags={len(tags)} cards={len(cards)} nums={len(nums)}"

    tmp = Path(tempfile.mkdtemp(prefix="i2v_info_"))
    segs = []
    for i, c in enumerate(clips):
        # 1) 抽帧叠加信息层
        frames_dir = tmp / f"f{i}"
        frames_dir.mkdir()
        subprocess.run([FFMPEG, "-y", "-i", c, "-vf", "fps=30",
                        str(frames_dir / "f_%04d.png")], capture_output=True)
        frames = sorted(frames_dir.glob("f_*.png"))
        print(f"  幕{i+1}: 叠加信息层 {len(frames)} 帧 ...")
        for fp in frames:
            im = add_info(Image.open(fp), tags[i], cards[i], nums[i])
            im.save(fp)
        # 2) 重编码回视频 (强制 yuv420p + High profile, 否则 4:4:4 多数播放器打不开)
        seg = tmp / f"seg{i}.mp4"
        subprocess.run([FFMPEG, "-y", "-framerate", "30", "-i", str(frames_dir / "f_%04d.png"),
                        "-vf", f"scale={W}:{H},fps={FPS},format=yuv420p",
                        "-profile:v", "high", "-level", "4.0",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-an", str(seg)],
                       capture_output=True)
        segs.append(str(seg))
        print(f"  幕{i+1}: {seg.name} 完成")

    listf = tmp / "list.txt"
    with open(listf, "w", encoding="utf-8") as f:
        for s in segs:
            f.write(f"file '{s.replace(chr(92), '/')}'\n")
    concat = tmp / "concat.mp4"
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
                    "-c", "copy", str(concat)], capture_output=True)
    # 加配音: 5 句旁白逐幕 TTS 对齐(讲解式注销三步)
    from model_providers import ensure_env
    ensure_env()
    from qwen_tts import synth
    NARR = ["公司不经营了想注销，其实就三步。",
            "第一步，账结清，该补的税补掉，该报的报表报完。",
            "第二步，公示四十五天，公告债权债务，没人来找麻烦。",
            "第三步，注销登记，税务注销完再做工商注销。",
            "拿到注销通知书，公司才算真正注销完。"]
    assert len(segs) == len(NARR), "分幕数需与旁白句数一致"
    voiced = []
    for i, s in enumerate(segs):
        wav = tmp / f"v{i}.wav"
        synth(NARR[i], "cosyvoice-v3-plus-zhangc2-28a7c3541e1c45518a03046c11baeb1d",
              str(wav), speech_rate=0.90, pitch_rate=1.0, volume=50)
        segv = tmp / f"segv{i}.mp4"
        # 以画面时长为准, 配音不足处尾部静音补齐(apad), 保证音画不截断; 音频统一 44100 立体声
        subprocess.run([FFMPEG, "-y", "-i", s, "-i", str(wav),
                        "-filter_complex", "[1:a]apad,aresample=44100,aformat=channel_layouts=stereo[a]",
                        "-map", "0:v", "-map", "[a]",
                        "-c:v", "copy", "-c:a", "aac", "-shortest", str(segv)],
                       capture_output=True)
        voiced.append(str(segv))
    listf2 = tmp / "list2.txt"
    with open(listf2, "w", encoding="utf-8") as f:
        for s in voiced:
            f.write(f"file '{s.replace(chr(92), '/')}'\n")
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf2),
                    "-c", "copy", args.out], capture_output=True)
    print(f"成品(5句配音对齐): {args.out}")


if __name__ == "__main__":
    main()
