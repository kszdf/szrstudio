# -*- coding: utf-8 -*-
"""测试女声 TTS 对特定句子的合成是否失败。"""
import os
import subprocess
import sys

BASE = r"D:\heygem_data\gpt_sovits"
VOICE_F = "cosyvoice-v3-plus-jiangnv3-991b204c1d564ac7a60f0cb9a8fd78bd"
out = r"D:\heygem_data\演示输出\_tts_test.wav"

sentences = [
    "张老师，借款不还什么风险？",
    "女：张老师，借款不还什么风险？",
    "张老师，公司借款给老板个人不还，有什么风险？",
    "那借款不还，要交多少税？",
]
for s in sentences:
    if os.path.exists(out):
        os.remove(out)
    code = ("import sys; sys.path.insert(0, r'%s');"
            "from qwen_tts import synth_natural;"
            "synth_natural(%r, %r, %r)") % (BASE, s, VOICE_F, out)
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           cwd=BASE, timeout=90)
        size = os.path.getsize(out) if os.path.exists(out) else 0
        print(f"[{'OK ' if size > 6000 else 'FAIL'}] {s[:25]:28s} size={size}")
        if size <= 6000:
            print(f"    stderr: {(r.stderr or r.stdout or '')[-200:]}")
    except subprocess.TimeoutExpired:
        print(f"[TIMEOUT] {s[:25]}")
