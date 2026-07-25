#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地 GPT-SoVITS 微调模型推理 —— 把对比稿合成为 wav，与千问结果做 A/B 对比。

用法:
  cd D:/heygem_data/gpt_sovits/GPT_SoVITS
  D:/python311/python.exe ../sovits_infer.py

说明:
  - 自动找 logs_s2_v2 下最新的 G_*.pth 和 logs_s1/ckpt 下最新的 *.ckpt
  - 参考音频用训练集里最长的那段（其 ASR 文本作 prompt_text，保证对齐）
  - 输出到 D:/heygem_data/gpt_sovits/sovits_out/
"""
import os
import sys
import glob
import wave
import numpy as np

REPO = "D:/heygem_data/gpt_sovits/GPT_SoVITS"
ROOT = "D:/heygem_data/gpt_sovits"
EXP = os.path.join(ROOT, "training_data", "zhang")
COMPARE_TXT = os.path.join(ROOT, "compare_samples.txt")
OUT_DIR = os.path.join(ROOT, "sovits_out")
PRETRAINED = os.path.join(REPO, "pretrained_models")


def find_latest(pattern, default=None):
    files = glob.glob(pattern)
    if not files:
        return default
    return max(files, key=os.path.getmtime)


def load_ref_from_list(raw_list):
    """从 zhang_raw.list 取 (wav, text)，挑最长的 wav 当参考。"""
    best = None
    with open(raw_list, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            wav = parts[0]
            text = parts[-1] if len(parts) >= 4 else ""
            sz = os.path.getsize(wav) if os.path.exists(wav) else 0
            if best is None or sz > best[0]:
                best = (sz, wav, text)
    return best[1], best[2]


def save_wav(path, sr, audio):
    audio = np.asarray(audio, dtype=np.float32)
    # 归一化到 int16
    peak = np.max(np.abs(audio)) + 1e-6
    if peak > 1.0:
        audio = audio / peak
    data = (audio * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def main():
    sys.path.insert(0, REPO)
    sys.path.insert(0, ROOT)

    g_pth = find_latest(os.path.join(EXP, "logs_s2_v2", "G_*.pth"))
    s1_ckpt = find_latest(os.path.join(EXP, "logs_s1", "ckpt", "*.ckpt"))
    if not g_pth:
        sys.exit("找不到 SoVITS 权重 G_*.pth，训练可能还没产出。")
    if not s1_ckpt:
        sys.exit("找不到 GPT 权重 *.ckpt，s1 训练可能还没产出。")
    print(f"[权重] SoVITS={g_pth}\n        GPT={s1_ckpt}")

    ref_wav, ref_text = load_ref_from_list(os.path.join(EXP, "zhang_raw.list"))
    print(f"[参考] {ref_wav}\n        prompt_text={ref_text[:40]}...")

    from TTS_infer_pack.TTS import TTS

    configs = {
        "version": "v2",
        "device": "cuda",
        "is_half": True,
        "t2s_weights_path": s1_ckpt,
        "vits_weights_path": g_pth,
        "bert_base_path": os.path.join(PRETRAINED, "chinese-roberta-wwm-ext-large"),
        "cnhuhbert_base_path": os.path.join(PRETRAINED, "chinese-hubert-base"),
    }
    tts = TTS(configs)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(COMPARE_TXT, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    print(f"共 {len(lines)} 条，开始合成...")
    for i, text in enumerate(lines, 1):
        sr, audio = tts.run({
            "text": text,
            "text_lang": "zh",
            "ref_audio_path": ref_wav,
            "prompt_text": ref_text,
            "prompt_lang": "zh",
            "top_k": 15,
            "top_p": 1.0,
            "temperature": 1.0,
            "text_split_method": "cut1",
            "batch_size": 1,
            "speed_factor": 1.0,
        })
        out = os.path.join(OUT_DIR, f"{i:03d}.wav")
        save_wav(out, sr, audio)
        print(f"[{i}/{len(lines)}] -> {out}")

    print(f"完成，输出目录: {OUT_DIR}")


if __name__ == "__main__":
    main()
