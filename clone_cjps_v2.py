#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cjps 茶桌音色 v2：用更干净/更长的参考段重克隆，语速贴原声，pitch=1.0 保像本人。"""
import os
import sys
from pathlib import Path

MODEL = "cosyvoice-v3-plus"
REF = "natural_ref/cjps_ref_v2.wav"        # v2 参考：更长+去混响加强
PREFIX = "zhangc2"                         # <=10 字符
VOICE_ID_FILE = "natural_ref/cjps_v2_voice_id.txt"
TEXT = (
    "前阵子一位做实业的老客户来找我，说他们厂一年销项不少，可进项总是凑不齐，"
    "算下来税负偏高。我翻了翻他的采购流水，问题就出在上游供货这一环，"
    "不少交易没拿回发票。这层不理顺，金税四期一比对就容易露馅。"
)


def main():
    from dashscope import Files
    from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat, VoiceEnrollmentService

    print("[1] 上传参考:", REF)
    rsp = Files.upload(REF, purpose="voice-clone")
    fid = rsp.output["uploaded_files"][0]["file_id"]
    print("    file_id:", fid)
    lst = Files.list()
    url = None
    for f in lst.output["files"]:
        if f["file_id"] == fid:
            url = f["url"]
            break
    if not url:
        sys.exit("找不到 url")
    print("[2] 克隆音色 prefix=%s" % PREFIX)
    svc = VoiceEnrollmentService()
    vid = svc.create_voice(MODEL, PREFIX, url, language_hints=["zh"])
    print("    VOICE_ID:", vid)
    Path(VOICE_ID_FILE).write_text(vid, encoding="utf-8")
    print("[3] 合成样品 (pitch=1.0, volume=50)")
    for name, sr in [("cjps_e", 0.97), ("cjps_f", 1.0)]:
        s = SpeechSynthesizer(
            model=MODEL, voice=vid,
            format=AudioFormat.WAV_22050HZ_MONO_16BIT,
            speech_rate=sr, pitch_rate=1.0, volume=50,
            language_hints=["zh"],
        )
        b = s.call(text=TEXT)
        Path(f"natural_ref/qwen_sample_{name}.wav").write_bytes(b)
        print(f"    {name} saved (speech_rate={sr})")
    print("DONE")


if __name__ == "__main__":
    if not os.environ.get("DASHSCOPE_API_KEY"):
        sys.exit("请先设置 DASHSCOPE_API_KEY")
    main()
