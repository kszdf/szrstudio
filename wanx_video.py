#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wanx_video.py — AI 动态背景生成（阿里万相·文生视频，零注册零新key）

背景:
  Pexels/Pixabay 素材库需要注册 API key（QQ邮箱验证邮件常被拦截），手动下载素材又不现实。
  本项目已有阿里云 DashScope key（cosyvoice 配音 / wanx 生图都在用），
  万相提供「文生视频」——输入画面描述直接生成 5 秒动态视频，可完全替代外部素材库：
    · 零注册：复用 DASHSCOPE_API_KEY；
    · 更可控：提示词直接写死「无人物、竖版9:16、真实摄影」，不存在素材库"搜到带人镜头"的问题；
    · 内容相关：直接用 LLM 分镜生成的 image_prompt（讲发票 → 发票特写动态画面）；
    · 有缓存：按 prompt 哈希缓存，同稿重跑 0 成本。

成本: 5秒视频约 0.1~1.8 元/幕（视模型档位），失败自动降级生图/照片库，绝不阻塞出片。
"""
import hashlib
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
CACHE_DIR = BASE / "storage" / "wanx_videos"

# 候选模型: 按实测可用性顺序（wanx2.1-t2v-turbo 实测 5 秒出片, 低价turbo档）
MODELS = ["wanx2.1-t2v-turbo", "wanx2.1-t2i-video", "wanx2.6-t2i-video", "wanx2.6-t2v-turbo"]
_SIZE = "720*1280"          # 竖版 9:16
_POLL_STEP = 5
_POLL_MAX = 180             # 单段视频生成最长等 3 分钟
_GEN_LOCK = threading.Lock()
_AVAILABLE = None           # 惰性判定: key 是否存在（能否真生成以调用结果为准）

_STYLE_SUFFIX = "真实摄影，自然光影，电影级画质，画面纯净，无人物，无文字，无字母，无数字，竖版9:16构图"


def is_available():
    """DASHSCOPE_API_KEY 是否存在（不预判开通，调用失败会降级）。"""
    global _AVAILABLE
    if _AVAILABLE is None:
        v = os.environ.get("DASHSCOPE_API_KEY") or ""
        if not v:
            try:
                for raw in (BASE / "model_keys.env").read_text(encoding="utf-8").splitlines():
                    line = raw.strip()
                    if line.startswith("DASHSCOPE_API_KEY=") and line.split("=", 1)[1].strip():
                        v = line.split("=", 1)[1].strip()
                        break
            except Exception:
                pass
        _AVAILABLE = bool(v)
    return _AVAILABLE


def _submit(model, prompt, api_key):
    from dashscope import VideoSynthesis
    return VideoSynthesis.call(model=model, prompt=prompt, size=_SIZE,
                               api_key=api_key, prompt_extend=False)


def _fetch_task(task_id, api_key):
    import json
    req = urllib.request.Request(
        f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}",
        headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _poll(task_id, api_key):
    t0 = time.time()
    while time.time() - t0 < _POLL_MAX:
        time.sleep(_POLL_STEP)
        try:
            out = _fetch_task(task_id, api_key).get("output", {})
        except Exception:
            continue
        st = out.get("task_status", "?")
        if st in ("SUCCEEDED", "SUCCESS"):
            return out.get("video_url") or (out.get("results") or [{}])[0].get("url")
        if st in ("FAILED", "CANCELED"):
            return None
    return None


def _download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    tmp = dest.parent / (dest.stem + "." + hashlib.md5(os.urandom(8)).hexdigest()[:8] + ".part")
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    if tmp.stat().st_size < 50 * 1024:
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(dest)
    return True


def gen_video(image_prompt, title=""):
    """按画面描述生成一段竖版动态视频, 返回本地 mp4 路径; 失败返回 None(全程不抛)。"""
    if not is_available():
        return None
    prompt = (image_prompt or "").strip() + "，" + _STYLE_SUFFIX
    h = hashlib.md5(prompt.encode("utf-8")).hexdigest()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / f"{h}.mp4"
    if dest.exists() and dest.stat().st_size > 50 * 1024:
        return str(dest)
    with _GEN_LOCK:   # 生成并行时串行化提交, 防并发超频/超限
        if dest.exists() and dest.stat().st_size > 50 * 1024:
            return str(dest)
        api_key = os.environ.get("DASHSCOPE_API_KEY") or ""
        last_err = ""
        for model in MODELS:
            try:
                rsp = _submit(model, prompt, api_key)
                if rsp.status_code != 200:
                    last_err = f"{model}:{getattr(rsp, 'message', '')}"[:100]
                    continue
                url = _poll(rsp.output.task_id, api_key)
                if url and _download(url, dest):
                    print(f"      [ai-video] OK: {title[:10]} ({model}) -> {dest.name}")
                    return str(dest)
                last_err = f"{model}:生成失败/下载失败"
            except Exception as e:
                last_err = f"{model}:{str(e)[:80]}"
        print(f"      [ai-video] 失败({last_err}), 本幕回退静态背景")
        return None
