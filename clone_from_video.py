#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clone_from_video.py — 从视频/音频克隆张老师声音（CosyVoice 声音复刻）

用法:
  D:/heygem/py310/Scripts/python.exe clone_from_video.py <视频或音频路径> [前缀]

流程:
  1. ffmpeg 提取人声音频(去视频) → 检查时长(建议 10~30s 干净人声)
  2. 上传 dashscope → VoiceEnrollmentService 克隆 → 得到 voice_id
  3. 合成 2 段样品(语速 0.94/1.0)供对比试听
  4. 输出 voice_id 并写入 natural_ref/{prefix}_voice_id.txt

素材要求(阿里云声音复刻): 10~20 秒即可, 人声清晰、无BGM/无混响/无杂音,
说话自然(口播/讲课/聊天均可), 时长过长会自动截取中间段。
"""
import os
import re
import sys
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
FFMPEG = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"
FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")
MODEL = "cosyvoice-v3-plus"
SAMPLE_TEXT = (
    "有老板问我啊，公司账上的钱，能往个人卡上转吗？千万不能公转私。"
    "报销可以，借款往来不行。一旦被认定资金混同，有限责任瞬间变无限责任。"
    "记住，早做打算才是上策。"
)


def main():
    if len(sys.argv) < 2:
        sys.exit("用法: clone_from_video.py <素材路径> [前缀]")
    src = Path(sys.argv[1])
    if not src.exists():
        sys.exit(f"素材不存在: {src}")
    prefix = (sys.argv[2] if len(sys.argv) > 2 else "zhang_vx").strip()
    prefix = re.sub(r"[^a-zA-Z0-9_]", "", prefix)[:10]
    out_dir = BASE / "natural_ref"
    out_dir.mkdir(parents=True, exist_ok=True)

    # [0] 总时长
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
                       capture_output=True, text=True)
    total = float(r.stdout.strip() or 0)
    print(f"[0] 素材 {src.name} 总时长 {total:.1f}s")

    # [1] 提取人声: 截取 12~28s 的干净中段(若总时长>35s 从 20% 处取 16s; 否则整段截 16s)
    seg_len = 16.0
    start = 0.0
    if total > 35:
        start = total * 0.2
    ref = out_dir / f"{prefix}_ref.wav"
    subprocess.run([FFMPEG, "-y", "-ss", f"{start:.1f}", "-i", str(src),
                    "-t", f"{seg_len:.1f}", "-vn", "-ac", "1", "-ar", "22050",
                    str(ref)], capture_output=True, text=True)
    if not ref.exists() or ref.stat().st_size < 10000:
        sys.exit("提取音频失败(可能无音轨)")
    print(f"[1] 参考段已提取: {ref.name} ({seg_len:.0f}s, 单声道22k)")

    # [2] 上传 + 克隆
    from dashscope import Files
    from dashscope.audio.tts_v2 import VoiceEnrollmentService
    if not os.environ.get("DASHSCOPE_API_KEY"):
        sys.exit("请先设置 DASHSCOPE_API_KEY")
    print("[2] 上传参考音频...")
    rsp = Files.upload(str(ref), purpose="voice-clone")
    fid = rsp.output["uploaded_files"][0]["file_id"]
    url = None
    for f in Files.list().output["files"]:
        if f["file_id"] == fid:
            url = f["url"]
            break
    if not url:
        sys.exit("找不到上传文件 url")
    print(f"    克隆中 prefix={prefix} (约30-60s)...")
    svc = VoiceEnrollmentService()
    vid = svc.create_voice(MODEL, prefix, url, language_hints=["zh"])
    print(f"    VOICE_ID: {vid}")
    (out_dir / f"{prefix}_voice_id.txt").write_text(vid, encoding="utf-8")

    # [3] 合成样品对比
    from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat
    for name, sr in [("vx_094", 0.94), ("vx_100", 1.0)]:
        s = SpeechSynthesizer(model=MODEL, voice=vid,
                              format=AudioFormat.WAV_22050HZ_MONO_16BIT,
                              speech_rate=sr, pitch_rate=1.0, volume=50,
                              language_hints=["zh"])
        out = out_dir / f"qwen_sample_{name}.wav"
        out.write_bytes(s.call(text=SAMPLE_TEXT))
        print(f"    样品: {out.name} (speech_rate={sr})")
    print("\nDONE. voice_id 已存:", out_dir / f"{prefix}_voice_id.txt")
    print("替换 MALE_VOICE 后即可全平台用新声音。先试听 natural_ref/qwen_sample_vx_094.wav")


if __name__ == "__main__":
    main()
