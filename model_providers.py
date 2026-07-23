#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
model_providers.py
  多模型供应商统一入口（写稿 / 配音）
  Key 读取优先级：环境变量 > 本地 model_keys.env 文件

用法:
  from model_providers import get_text_config, get_tts_config, ensure_env, get_key
  ensure_env()
  cfg = get_text_config()        # 写稿配置（自动选有 key 的供应商）
  cfg = get_tts_config()         # 配音配置（目前固定 dashscope）
"""
import os
import json
import urllib.request
import urllib.error
from pathlib import Path

BASE = Path(__file__).resolve().parent
ENV_FILE = BASE / "model_keys.env"


def _parse_env_file(path: Path) -> dict:
    """读 KEY=VALUE 文件；忽略注释/空行；空值视为未设置。"""
    out = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if v:
            out[k] = v
    return out


_FILE_CACHE = None


def _file_keys() -> dict:
    global _FILE_CACHE
    if _FILE_CACHE is None:
        _FILE_CACHE = _parse_env_file(ENV_FILE)
    return _FILE_CACHE


def get_key(name: str) -> str | None:
    """按名称取 key：环境变量 > 本地文件。"""
    v = os.environ.get(name)
    if v:
        return v
    return _file_keys().get(name)


def ensure_env() -> None:
    """把本地文件里的 key 灌进环境变量（不覆盖已有），让下游 SDK 自动识别。"""
    for k, v in _file_keys().items():
        os.environ.setdefault(k, v)


# ------------------------------------------------------------------ 写稿
def get_text_config(force_provider: str | None = None) -> dict:
    """
    返回写稿配置:
      {"provider": "deepseek"|"qwen", "key": "...", "base_url": "...", "model": "..."}
    选择规则：force_provider > TEXT_PROVIDER 环境 > 自动（有 deepseek 用 deepseek，否则 qwen）
    没有可用供应商时抛 RuntimeError（提示清晰）。
    """
    prov = (force_provider or os.environ.get("TEXT_PROVIDER") or "").strip().lower()
    if not prov:
        if get_key("DEEPSEEK_API_KEY"):
            prov = "deepseek"
        elif get_key("DASHSCOPE_API_KEY"):
            prov = "qwen"
    if prov == "deepseek":
        key = get_key("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("已选择 deepseek 但未配置 DEEPSEEK_API_KEY，请在 model_keys.env 填写。")
        return {"provider": "deepseek", "key": key,
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat"}
    if prov == "qwen":
        key = get_key("DASHSCOPE_API_KEY")
        if not key:
            raise RuntimeError("已选择 qwen 但未配置 DASHSCOPE_API_KEY，请在 model_keys.env 填写。")
        return {"provider": "qwen", "key": key,
                "base_url": "https://dashscope.aliyuncs.com",
                "model": "qwen-turbo"}
    raise RuntimeError(
        "未配置任何写稿模型 key。请在 model_keys.env 填写 DEEPSEEK_API_KEY（推荐）或 DASHSCOPE_API_KEY。"
    )


def deepseek_chat(prompt: str, model: str, key: str,
                  base_url: str = "https://api.deepseek.com",
                  timeout: int = 60) -> str:
    """DeepSeek 文本对话（OpenAI 兼容接口，纯标准库 urllib，无新依赖）。"""
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model or "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"DeepSeek HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"DeepSeek 网络错误: {e.reason}") from None
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"DeepSeek 返回结构异常: {data}") from e


# ------------------------------------------------------------------ 配音
def get_tts_config() -> dict:
    """返回配音配置（目前固定 dashscope / CosyVoice，保留老张声音）。"""
    key = get_key("DASHSCOPE_API_KEY") or get_key("DASHSCOPE_API_KEY_TTS")
    if not key:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY（TTS 配音需要，用于保留老张克隆声音）。")
    return {"provider": "dashscope", "key": key,
            "model": "cosyvoice-v3-plus"}