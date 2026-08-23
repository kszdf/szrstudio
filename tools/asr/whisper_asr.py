# -*- coding: utf-8 -*-
"""faster-whisper 本地 ASR（funasr 未安装时的兜底方案）。

用已下载的 faster-whisper-small 模型（modelscope 缓存），CPU int8 推理，
提供与 funasr_asr.only_asr 同签名的 only_asr(input_file, language) -> str。
"""
import os

from faster_whisper import WhisperModel

MODEL_PATH = r"D:\heygem_data\cache\modelscope\models\AI-ModelScope--faster-whisper-small\snapshots\master"

_model = None


def _get_model():
    global _model
    if _model is None:
        # small 模型 + CPU int8：中文可用、内存占用小、加载快
        _model = WhisperModel(MODEL_PATH, device="cpu", compute_type="int8")
    return _model


def only_asr(input_file, language="zh"):
    """音频文件 → 文字。language 支持 zh / auto 等。"""
    model = _get_model()
    lang = None if language in ("auto", "") else language
    segments, info = model.transcribe(
        input_file,
        language=lang,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )
    text = "".join(s.text for s in segments).strip()
    return text
