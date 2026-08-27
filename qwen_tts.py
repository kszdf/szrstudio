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


def synth(text, voice, out_path, model=DEFAULT_MODEL, speech_rate=DEFAULT_SPEECH_RATE, pitch_rate=1.0, volume=50, retries=3, timeout=90, instruction=""):
    """合成单条文本 -> 保存 wav。失败自动重试。带超时保护（防 dashscope 网络卡死无限阻塞）。
    instruction: CosyVoice v3 风格指令(文本描述语气/语速/情感), 空则不注入(保持原行为)。"""
    if not voice:
        raise ValueError(
            "voice_id 为空：租户尚未克隆或选择声音。请先在「声音」页克隆专属音色或选择公开模板后再生成。"
        )
    import threading
    from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat

    last_err = None
    for i in range(retries):
        try:
            kw = dict(
                model=model,
                voice=voice,
                format=AudioFormat.WAV_22050HZ_MONO_16BIT,
                speech_rate=speech_rate,
                pitch_rate=pitch_rate,
                volume=volume,
                language_hints=["zh"],
            )
            if instruction and instruction.strip():
                kw["instruction"] = instruction.strip()
            synth_ = SpeechSynthesizer(**kw)
            # 用守护线程 + 超时包裹 call，防止网络卡死时无限阻塞
            box = {}
            def _call():
                try:
                    box["audio"] = synth_.call(text=text)
                except Exception as _e:  # noqa: BLE001
                    box["err"] = _e
            t = threading.Thread(target=_call, daemon=True)
            t.start()
            t.join(timeout=timeout)
            if t.is_alive():
                raise TimeoutError(f"TTS 超时（>{timeout}s，网络或服务无响应）")
            if "err" in box:
                raise box["err"]
            audio_bytes = box.get("audio")
            if not isinstance(audio_bytes, (bytes, bytearray)) or len(audio_bytes) < 1000:
                import os as _os
                _dbg = {
                    "type": type(audio_bytes).__name__,
                    "len": len(audio_bytes) if hasattr(audio_bytes, "__len__") else "?",
                    "voice": (voice or "")[:20],
                    "cwd": _os.getcwd(),
                    "has_key": bool(_os.environ.get("DASHSCOPE_API_KEY")),
                    "key_head": (_os.environ.get("DASHSCOPE_API_KEY") or "")[:8],
                    "last_error": str(getattr(synth_, "last_error", "") or ""),
                }
                raise RuntimeError(f"返回内容异常: type={type(audio_bytes).__name__}, len={len(audio_bytes) if hasattr(audio_bytes, '__len__') else '?'} DBG={_dbg}")
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(audio_bytes)
            return out_path
        except Exception as e:
            le = ""
            try:
                le = str(getattr(synth_, "last_error", "") or "")
            except Exception:
                pass
            last_err = f"{type(e).__name__}: {e}" + (f" | last_error={le}" if le else "")
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"合成失败（已重试 {retries} 次）: {last_err}")


def _split_sentences(text):
    """按句末标点/换行拆分, 保留标点用于韵律判断。"""
    import re as _re
    parts = _re.split(r"([。！？\n])", text)
    sents, buf = [], ""
    for seg in parts:
        if seg in "。！？\n":
            if buf.strip():
                sents.append(buf + seg)
                buf = ""
        else:
            buf += seg
    if buf.strip():
        sents.append(buf)
    return [s for s in sents if s.strip()]


def _sentence_pace(sent, base_rate=0.85):
    """返回 (speech_rate, pause_after_ms, lead_ms)。
    lead_ms = 句前吸气停顿（重点/警示句前留白，制造"先顿一下再说"的真人感）。
    引导/结论/提醒句放慢并加长停顿, 列举密集句正常偏快。
    v2(2026-08-27): 整体语速下调(用户反馈"男声太快太AI"): 普通 0.92→0.85, 警示 0.90→0.84。"""
    slow_kw = ["先说清楚", "再提醒", "比如", "其实", "要注意", "还要提醒",
               "别", "不能", "不是", "红线", "谨慎", "务必", "别抱",
               "记住", "注意", "重点", "关键", "一定"]
    lead_ms = 0
    if any(k in sent for k in slow_kw):
        rate, base = 0.84, 600     # 警示/结论句: 放慢 + 更长的句后停顿
        lead_ms = 200              # 警示/结论句前吸气停顿
    elif sent.count("、") >= 2:
        rate, base = 0.96, 340     # 列举密集句: 略快但仍有呼吸
    else:
        rate, base = base_rate, 480
    s = sent.rstrip()
    if s.endswith(("？", "！", "?", "!")):
        base += 150
    return rate, base, lead_ms


# 注意: CosyVoice v3-plus 当前接口不接受 instruction 风格指令(实测返回 None, 2026-08-27),
# 自然度靠: 语速放慢 + 句间停顿 + 警示句前吸气停顿 实现。
def synth_natural(text, voice, out_path, model=DEFAULT_MODEL, base_rate=0.85, retries=3):
    """分句合成 + 逐句语速 + 句间静音, 解决'机械匀速无停顿'的 AI 痕迹。
    引导/结论句放慢到 0.84 并加长停顿, 列举密集句 0.96, 其余 0.85。"""
    import wave, os
    if not voice:
        raise ValueError(
            "voice_id 为空：租户尚未克隆或选择声音。请先在「声音」页克隆专属音色或选择公开模板后再生成。"
        )
    sents = _split_sentences(text) or [text]
    params = None
    buf = bytearray()
    tmp_files = []
    try:
        for i, s in enumerate(sents):
            rate, pause, lead_ms = _sentence_pace(s, base_rate)
            tmp = out_path + f".part{i}.wav"
            synth(s, voice, tmp, model=model, speech_rate=rate,
                  pitch_rate=1.0, volume=50, retries=retries)
            with wave.open(tmp, "rb") as w:
                if params is None:
                    params = (w.getnchannels(), w.getsampwidth(), w.getframerate())
                data = w.readframes(w.getnframes())
                # 重点/警示句前：先补一段"吸气停顿"再进本句（真人先顿一下再说）
                if lead_ms > 0:
                    sr = params[2]
                    n_lead = int(lead_ms / 1000 * sr)
                    buf += b"".join(b"\x00\x00" for _ in range(n_lead))
                buf += data
                if i < len(sents) - 1 and pause > 0:
                    sr = params[2]
                    n_sil = int(pause / 1000 * sr)
                    buf += b"".join(b"\x00\x00" for _ in range(n_sil))
            tmp_files.append(tmp)
        from pathlib import Path as _P
        _P(out_path).parent.mkdir(parents=True, exist_ok=True)
        with wave.open(out_path, "wb") as w:
            w.setnchannels(params[0]); w.setsampwidth(params[1]); w.setframerate(params[2])
            w.writeframes(bytes(buf))
        return out_path
    finally:
        for t in tmp_files:
            try:
                os.remove(t)
            except OSError:
                pass


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
