#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 cjps 茶桌视频音频克隆"聊天/叙事"音色，并合成一段样品。
依赖: dashscope (tts_v2)，需 DASHSCOPE_API_KEY 环境变量。
"""
import os
import sys
import time
from pathlib import Path

MODEL = "cosyvoice-v3-plus"
REF = "natural_ref/cjps_ref_28s.wav"          # 茶桌中段 28s 参考音频
PREFIX = "zhangchat"                          # 克隆前缀(短)
OUT_ID = "natural_ref/cjps_voice_id.txt"      # voice_id 落盘
OUT_WAV = "natural_ref/qwen_sample_cjps.wav"  # 样品

# 聊天/叙事风样例文本（不是讲稿，像在茶桌跟朋友唠）
SAMPLE_TEXT = (
    "前两天我一老哥，开了个小加工厂，非拉我喝茶。"
    "他说老张你帮我算算，我这厂一年进项少、销项多，税负老高。"
    "我端着茶杯跟他说，哥，你这账得从采购端理，光在销售上琢磨没用。"
)


def upload_and_get_url(path):
    from dashscope import Files
    print(f"[1/3] 上传参考音频: {path}")
    rsp = Files.upload(path, purpose="voice-clone")
    file_id = None
    obj = getattr(rsp, "output", None)
    if isinstance(obj, dict):
        ups = obj.get("uploaded_files") or []
        if ups:
            file_id = ups[0].get("file_id")
    if not file_id and isinstance(rsp, dict):
        file_id = rsp.get("file_id")
    if not file_id:
        print("UPLOAD RSP:", rsp)
        sys.exit("上传失败：找不到 file_id")
    print("    file_id:", file_id)
    listing = Files.list()
    lobj = getattr(listing, "output", None)
    items = []
    if isinstance(lobj, dict):
        items = lobj.get("files") or []
    url = None
    for it in (items or []):
        fid = it.get("file_id") if isinstance(it, dict) else getattr(it, "file_id", None)
        if fid == file_id:
            url = it.get("url") if isinstance(it, dict) else getattr(it, "url", None)
            break
    if not url:
        print("LIST:", listing)
        sys.exit("找不到公网 url")
    return url


def clone(url):
    from dashscope.audio.tts_v2 import VoiceEnrollmentService
    print(f"[2/3] 克隆音色 -> model={MODEL}, prefix={PREFIX}")
    svc = VoiceEnrollmentService()
    vid = svc.create_voice(MODEL, PREFIX, url, language_hints=["zh"])
    if isinstance(vid, str):
        return vid
    for attr in ("output", "body"):
        obj = getattr(vid, attr, None)
        if isinstance(obj, dict):
            v = obj.get("voice_id") or obj.get("voice")
            if v:
                return v
    if hasattr(vid, "voice_id"):
        return vid.voice_id
    print("CLONE RSP:", vid)
    sys.exit("克隆失败：找不到 voice_id")


def synth(text, voice, out_path, speech_rate=0.92, pitch_rate=0.92, volume=45):
    from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat
    print(f"[3/3] 合成样品 (speech_rate={speech_rate}, pitch_rate={pitch_rate}, volume={volume})")
    s = SpeechSynthesizer(
        model=MODEL, voice=voice,
        format=AudioFormat.WAV_22050HZ_MONO_16BIT,
        speech_rate=speech_rate, pitch_rate=pitch_rate, volume=volume,
        language_hints=["zh"],
    )
    b = s.call(text=text)
    if not isinstance(b, (bytes, bytearray)) or len(b) < 1000:
        sys.exit(f"合成返回异常: {type(b).__name__} len={len(b) if hasattr(b,'__len__') else '?'}")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    open(out_path, "wb").write(b)
    return out_path


if __name__ == "__main__":
    if not os.environ.get("DASHSCOPE_API_KEY"):
        sys.exit("请先设置环境变量 DASHSCOPE_API_KEY")
    url = upload_and_get_url(REF)
    print("    url:", url)
    vid = clone(url)
    print("    VOICE_ID:", vid)
    Path(OUT_ID).write_text(vid, encoding="utf-8")
    out = synth(SAMPLE_TEXT, vid, OUT_WAV)
    print("    SAMPLE:", out)
    print("DONE")
