#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
千问 Qwen-Audio-3.0-TTS —— 用已注册好的"张老师"克隆音色做合成
走 dashscope SDK（tts_v2），稳。

用法:
  # 单条合成
  python qwen_tts.py synth --text "今天讲个税务小知识" --out qwen_out/x.wav

  # 批量合成（按行读一个 txt，每行一条）
  python qwen_tts.py batch --in compare_samples.txt --outdir qwen_out

  # 用自定义 voice_id（覆盖默认）
  python qwen_tts.py synth --voice <voice_id> --text "..." --out out.wav

环境变量:
  DASHSCOPE_API_KEY  百炼 API Key（必填）
  QWEN_VOICE_ID      自定义 voice_id（可选；默认用脚本里硬编码的张老师 voice_id）

voice_id 历史:
  - 旧 (formal): cosyvoice-v3-plus-zhang-6b2c59919a5d450b8e8b586c939a95a6  (基于 27s YP 税收朗诵录音)
  - 自然 (natural): cosyvoice-v3-plus-zhangnat-e96892017bab41638cbd1f3bd14912c1  (基于 30s 公转私 自然讲话)
  - v2 (chat, ★默认): cosyvoice-v3-plus-zhangc2-28a7c3541e1c45518a03046c11baeb1d  (基于 cjps 茶桌 40s 去混响段，用户 2026-07-23 选定 F 样品)
"""
import os
import sys
import time
import argparse
from pathlib import Path

# 让 model_keys.env 里的 key 自动灌进环境变量（dashscope SDK 直接读 env）
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from model_providers import ensure_env
    ensure_env()
except Exception:
    pass

# --- 默认配置 ---
# ★ 张老师的"最终选中音色"（2026-07-23 用户亲耳选定 qwen_sample_cjps_f.wav）
# 来源：cjps.mp4 茶桌视频中段 40s 去混响参考段重克隆（v2），叙事自然且像本人
# 参数锁定：语速 1.0（贴原声）、pitch 1.0（保音色像本人）、音量 50
DEFAULT_VOICE_ID = "cosyvoice-v3-plus-zhangc2-28a7c3541e1c45518a03046c11baeb1d"
DEFAULT_MODEL = "cosyvoice-v3-plus"
# F 样品锁定语速：1.0（原速，最像本人；低于 1.0 会偏柔但不如原声稳）
DEFAULT_SPEECH_RATE = 1.0


def synth(text, voice, out_path, model=DEFAULT_MODEL, speech_rate=DEFAULT_SPEECH_RATE, pitch_rate=1.0, volume=50, retries=3):
    """合成单条文本 -> 保存 wav。失败自动重试。"""
    if not voice:
        raise ValueError(
            "voice_id 为空：租户尚未克隆或选择声音。请先在「声音」页克隆专属音色或选择公开模板后再生成。"
        )
    from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat

    last_err = None
    for i in range(retries):
        try:
            synth = SpeechSynthesizer(
                model=model,
                voice=voice,
                format=AudioFormat.WAV_22050HZ_MONO_16BIT,
                speech_rate=speech_rate,
                pitch_rate=pitch_rate,
                volume=volume,
                language_hints=["zh"],
            )
            audio_bytes = synth.call(text=text)
            if not isinstance(audio_bytes, (bytes, bytearray)) or len(audio_bytes) < 1000:
                raise RuntimeError(f"返回内容异常: type={type(audio_bytes).__name__}, len={len(audio_bytes) if hasattr(audio_bytes, '__len__') else '?'}")
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(audio_bytes)
            return out_path
        except Exception as e:
            le = ""
            try:
                le = str(getattr(synth, "last_error", "") or "")
            except Exception:
                pass
            last_err = f"{type(e).__name__}: {e}" + (f" | last_error={le}" if le else "")
        time.sleep(2 * (i + 1))
    sys.exit(f"合成失败（已重试 {retries} 次）: {last_err}")


def batch(in_src, voice, outdir, model=DEFAULT_MODEL):
    items = []
    p = Path(in_src)
    paths = sorted(p.glob("*.txt")) if p.is_dir() else [p]
    for fp in paths:
        for line in fp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                items.append((fp.stem, line))
    if not items:
        sys.exit("没有可合成的文本")
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"共 {len(items)} 条，模型={model}，voice={voice}")
    for idx, (stem, text) in enumerate(items, 1):
        out_path = outdir / f"{idx:03d}_{stem}.wav"
        print(f"[{idx}/{len(items)}] {text[:40]}...")
        synth(text, voice, str(out_path), model=model)
    print(f"完成，输出目录: {outdir}")


def main():
    ap = argparse.ArgumentParser(description="千问 CosyVoice-v3-plus 合成（张老师克隆音色）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("synth", help="单条合成")
    s1.add_argument("--text", required=True)
    s1.add_argument("--out", required=True)
    s1.add_argument("--voice", default=os.environ.get("QWEN_VOICE_ID", DEFAULT_VOICE_ID))
    s1.add_argument("--model", default=DEFAULT_MODEL)
    s1.add_argument("--speech-rate", type=float, default=DEFAULT_SPEECH_RATE)
    s1.add_argument("--pitch-rate", type=float, default=1.0)
    s1.add_argument("--volume", type=int, default=50)

    s2 = sub.add_parser("batch", help="批量合成（按行读 txt）")
    s2.add_argument("--in", dest="in_src", required=True)
    s2.add_argument("--outdir", required=True)
    s2.add_argument("--voice", default=os.environ.get("QWEN_VOICE_ID", DEFAULT_VOICE_ID))
    s2.add_argument("--model", default=DEFAULT_MODEL)

    args = ap.parse_args()
    if args.cmd == "synth":
        synth(args.text, args.voice, args.out,
              model=args.model, speech_rate=args.speech_rate,
              pitch_rate=args.pitch_rate, volume=args.volume)
        print(f"已保存: {args.out}")
    elif args.cmd == "batch":
        batch(args.in_src, args.voice, args.outdir, model=args.model)


if __name__ == "__main__":
    main()
