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
                "model": "deepseek-v4-flash"}
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
                  timeout: int = 60, enable_search: bool = False,
                  return_meta: bool = False) -> str | dict:
    """DeepSeek 文本对话（OpenAI 兼容接口，纯标准库 urllib，无新依赖）。
    enable_search=True 时开启联网搜索，返回内容会带上实时检索到的信息。
    return_meta=True 时返回 dict: {"content": ..., "search_results": [...], "raw": data}
       —— 当 enable_search 真正生效时，DeepSeek 会在 message 里确定性地附加
          search_results 字段（真实检索证据）。据此判断"到底有没有真联网"，
          而不是依赖模型自觉报告。默认 False（返回纯文本字符串，兼容其他调用方）。
    """
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model or "deepseek-v4-flash",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    if enable_search:
        payload["enable_search"] = True
    body = json.dumps(payload).encode("utf-8")
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
    content = data["choices"][0]["message"]["content"]
    if return_meta:
        msg = data["choices"][0]["message"]
        search_results = msg.get("search_results") or []
        return {"content": content, "search_results": search_results, "raw": data}
    return content


def tavily_search(query: str, api_key: str, topic: str = "finance",
                  days: int = 30, max_results: int = 8,
                  timeout: int = 30) -> dict:
    """Tavily Search API（专为 LLM 设计的联网搜索，纯标准库 urllib，无新依赖）。

    与 DeepSeek 的 enable_search 不同：Tavily 是独立搜索服务，官方 API 平台
    当前不提供联网搜索，因此把"真实联网检索"这件事交给 Tavily 做，
    DeepSeek 只负责基于检索结果做二创（分工清晰、确定性强）。

    参数:
      query      搜索词（如"最近一个月 公转私 税务稽查 热点"）
      api_key    Tavily API key（TAVILY_API_KEY）
      topic      general / news / finance（财税场景用 finance）
      days       限定最近 N 天（对应"近7天/近30天"等）
      max_results 返回条数
    返回 dict: {"results": [{title,url,content,published_date}], "raw": data}
      results 每项: title 标题 / url 来源链接 / content 内容摘要 / published_date 发布日期
    失败时抛 RuntimeError（HTTP 错误 / 网络错误）。
    """
    url = "https://api.tavily.com/search"
    payload = {
        "query": query,
        "topic": topic,
        "search_depth": "advanced",
        "days": days,
        "max_results": max_results,
        "include_answer": False,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Tavily HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Tavily 网络错误: {e.reason}") from None
    results = data.get("results") or []
    return {"results": results, "raw": data}


# ------------------------------------------------------------------ 配音
def get_tts_config() -> dict:
    """返回配音配置（目前固定 dashscope / CosyVoice，保留老张声音）。"""
    key = get_key("DASHSCOPE_API_KEY") or get_key("DASHSCOPE_API_KEY_TTS")
    if not key:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY（TTS 配音需要，用于保留老张克隆声音）。")
    return {"provider": "dashscope", "key": key,
            "model": "cosyvoice-v3-plus"}