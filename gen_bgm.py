# -*- coding: utf-8 -*-
"""轻 BGM 生成：自研合成温暖氛围 pad（版权安全，无第三方素材）。
和弦进行 C-G-Am-F（柔和向上），正弦+谐波叠加，慢起音/淡出，低频过滤。
输出 16bit 立体声 WAV，缓存到 static/bgm_default.wav。"""
import os
import numpy as np
import wave
import io
import sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SR = 44100
DUR = 120          # 秒（覆盖最长口播）
FADE_IN = 2.0
FADE_OUT = 4.0
OUT = r"D:\heygem_data\gpt_sovits\static\bgm_default.wav"

# 和弦进行（C-G-Am-F，每和弦 4s，循环）
CHORDS = [
    [261.63, 329.63, 392.00, 523.25],   # C
    [196.00, 246.94, 293.66, 392.00],   # G
    [220.00, 261.63, 329.63, 440.00],   # Am
    [174.61, 220.00, 261.63, 349.23],   # F
]
CHORD_SEC = 4.0


def synth():
    total = int(SR * DUR)
    out = np.zeros(total)
    t_all = np.arange(total) / SR
    chord_len = int(SR * CHORD_SEC)
    n_chords = len(CHORDS)
    for start in range(0, total, chord_len):
        ci = (start // chord_len) % n_chords
        freqs = CHORDS[ci]
        n = min(chord_len, total - start)
        t = np.arange(n) / SR
        seg = np.zeros(n)
        for f in freqs:
            # 基音 + 0.5% 失谐（立体声宽度感）+ 轻微 2 次谐波
            seg += 0.55 * np.sin(2 * np.pi * f * t)
            seg += 0.25 * np.sin(2 * np.pi * f * 2 * t + 0.3)
            seg += 0.15 * np.sin(2 * np.pi * f * 1.005 * t + 1.1)
        # 和弦内慢起音（每和弦前 0.8s 渐入，避免突变）
        atk = int(0.8 * SR)
        seg[:atk] *= np.linspace(0.25, 1.0, atk)
        seg[-int(0.4 * SR):] *= np.linspace(1.0, 0.7, int(0.4 * SR))
        out[start:start + n] += seg
    # 整体淡入/淡出
    fi = int(FADE_IN * SR)
    fo = int(FADE_OUT * SR)
    out[:fi] *= np.linspace(0, 1, fi)
    out[-fo:] *= np.linspace(1, 0, fo)
    # 轻微低通（一阶平滑），去高频毛刺
    k = 0.15
    lp = np.copy(out)
    for i in range(1, len(out)):
        lp[i] = lp[i - 1] + k * (out[i] - lp[i - 1])
    out = lp
    # 归一化到 -20 dBFS 峰值
    peak = np.max(np.abs(out))
    if peak > 0:
        out = out / peak * 10 ** (-20 / 20)
    # 立体声：右声道延迟 12ms 制造宽度
    left = out
    delay = int(0.012 * SR)
    right = np.concatenate([np.zeros(delay), out[:-delay]])
    stereo = np.stack([left, right], axis=1)
    pcm = (stereo * 32767).astype(np.int16)
    return pcm


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    pcm = synth()
    with wave.open(OUT, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    print(f"BGM 已生成: {OUT} ({pcm.shape[0]/SR:.0f}s, {os.path.getsize(OUT)//1024}KB)")


if __name__ == "__main__":
    main()
