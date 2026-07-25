#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
thirdparty_avatar.py
  第三方数字人视频生成适配器（与本地 HEYGEM 并列的可选出片引擎）

设计原则
  - 与 HEYGEM 完全解耦：本模块只负责"把一段文本 + 一个官方形象 + 声音模式"
    交给第三方 API，拿回一个 mp4。下游（字幕/质检/发布/预览）只吃最终 mp4，
    不感知出片引擎是谁 —— 所以两条路径对现有流程零侵入。
  - 供应商无关（provider-agnostic）：默认 generic REST 模式（提交→轮询→下载），
    覆盖大多数厂商（硅基 / HeyGen / 阿里云数字人 / 火山 等通用报文）。
    确定具体供应商后，在 _submit_* / _query_* 分支里补厂商特有报文即可。
  - 纯标准库（urllib），无新依赖，可直接被 rewrite_studio.py（跑在 3.13）import。
  - 未配置时所有入口返回清晰错误，绝不崩溃。

配置（写入 model_keys.env，切勿外发）：
  THIRDPARTY_AVATAR_PROVIDER=generic        # generic | heygen | shuju | aliyun | volc
  THIRDPARTY_AVATAR_KEY=                   # 厂商 API Key
  THIRDPARTY_AVATAR_BASE=                  # 厂商 API 基址，如 https://api.xxx.com（无结尾斜杠）
  THIRDPARTY_AVATAR_SUBMIT=/v1/video/submit  # 提交路径（可选，有默认）
  THIRDPARTY_AVATAR_QUERY=/v1/video/query    # 轮询路径（可选，有默认）
  THIRDPARTY_AVATAR_AVATARS=av1:财经女主播,av2:硬朗男专家,av3:知性讲师  # 官方形象库
  THIRDPARTY_AVATAR_VOICE_OFFICIAL=         # 官方音色 id（voice_mode=official 用）
  THIRDPARTY_AVATAR_VOICE_LAOZHANG=         # 老张克隆音色 id（voice_mode=brand 且平台支持克隆音色时用）

声音模式 voice_mode:
  official  -> 用平台官方 TTS 音色念稿（不出本人声）
  brand   -> 用老张克隆音色（若平台支持克隆音色，填 VOICE_LAOZHANG；
                若平台只支持上传音频驱动嘴型，则把本地 04_音频.wav 以 audio_base64 传入）
"""
import os
import json
import time
import base64
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

import model_providers as mp


# ------------------------------------------------------------------ 配置
def get_avatar_config() -> dict:
    mp.ensure_env()  # 把 model_keys.env 的 key 灌进环境变量
    return {
        "provider": (os.environ.get("THIRDPARTY_AVATAR_PROVIDER") or "generic").strip().lower(),
        "key": (os.environ.get("THIRDPARTY_AVATAR_KEY") or "").strip(),
        "base": (os.environ.get("THIRDPARTY_AVATAR_BASE") or "").rstrip("/").strip(),
        "submit_path": (os.environ.get("THIRDPARTY_AVATAR_SUBMIT") or "/v1/video/submit").strip(),
        "query_path": (os.environ.get("THIRDPARTY_AVATAR_QUERY") or "/v1/video/query").strip(),
        "avatars": _parse_avatars(os.environ.get("THIRDPARTY_AVATAR_AVATARS") or ""),
        "voice_official": (os.environ.get("THIRDPARTY_AVATAR_VOICE_OFFICIAL") or "").strip(),
        "voice_brand": (os.environ.get("THIRDPARTY_AVATAR_VOICE_LAOZHANG") or "").strip(),
    }


def _parse_avatars(s: str) -> list:
    """格式：id:名称,id:名称  ->  [{"id":..,"name":..}, ...]"""
    out = []
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            i, n = part.split(":", 1)
            out.append({"id": i.strip(), "name": n.strip()})
        else:
            out.append({"id": part, "name": part})
    return out


def is_configured() -> bool:
    c = get_avatar_config()
    return bool(c["key"] and c["base"])


def info() -> dict:
    """给前端 /api/thirdparty/info 用：是否配置、供应商、可选形象、声音模式。"""
    c = get_avatar_config()
    return {
        "configured": is_configured(),
        "provider": c["provider"],
        "avatars": c["avatars"],
        "voice_modes": [
            {"id": "official", "name": "官方音色（不出本人声）"},
            {"id": "brand", "name": "品牌克隆音（平台支持时）"},
        ],
    }


# ------------------------------------------------------------------ 网络
def _http_json(url: str, payload: dict | None, method: str, key: str,
               timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"第三方 API HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"第三方 API 网络错误: {e.reason}") from None


# ------------------------------------------------------------------ 供应商分支
def _build_submit_payload(c: dict, script_text: str, avatar_id: str,
                          voice_mode: str, brand_audio: str | None) -> dict:
    """构造提交报文。generic 模式；具体厂商在 provider 分支里覆写。"""
    provider = c["provider"]
    if provider == "generic":
        payload = {
            "text": script_text,
            "avatar_id": avatar_id,
        }
        if voice_mode == "brand":
            if brand_audio and Path(brand_audio).exists():
                b64 = base64.b64encode(
                    Path(brand_audio).read_bytes()).decode("ascii")
                payload["audio_base64"] = b64   # 上传音频驱动官方形象嘴型
            elif c["voice_brand"]:
                payload["voice_id"] = c["voice_brand"]
            else:
                raise RuntimeError(
                    "voice_mode=brand 但需要配置 THIRDPARTY_AVATAR_VOICE_LAOZHANG "
                    "或提供本地音频文件。")
        else:
            if not c["voice_official"]:
                raise RuntimeError(
                    "voice_mode=official 但未配置 THIRDPARTY_AVATAR_VOICE_OFFICIAL。")
            payload["voice_id"] = c["voice_official"]
        return payload
    # 其它厂商 TODO：在此按厂商文档补 _submit_<provider>
    raise RuntimeError(
        f"供应商 '{provider}' 的提交报文尚未实现，请到 thirdparty_avatar.py 补全。")


def _parse_submit_result(c: dict, data: dict) -> str:
    """从提交响应里抠出 task_id。兼容多种常见结构。"""
    if isinstance(data.get("task_id"), str):
        return data["task_id"]
    if isinstance(data.get("id"), str):
        return data["id"]
    d = data.get("data") or {}
    if isinstance(d.get("task_id"), str):
        return d["task_id"]
    if isinstance(d.get("taskId"), str):
        return d["taskId"]
    if isinstance(d.get("id"), str):
        return d["id"]
    raise RuntimeError(f"第三方提交响应未含 task_id：{json.dumps(data, ensure_ascii=False)[:300]}")


def _parse_query_result(c: dict, data: dict) -> dict:
    """把厂商轮询响应归一化：{status, progress, video_url}。"""
    # 状态归一
    raw = str((data.get("status") or data.get("state")
               or (data.get("data") or {}).get("status") or "")).lower()
    if raw in ("success", "done", "completed", "finished"):
        status = "done"
    elif raw in ("failed", "error", "err"):
        status = "error"
    else:
        status = "running"
    # 进度
    prog = data.get("progress") or (data.get("data") or {}).get("progress") or 0
    try:
        prog = float(prog)
    except (TypeError, ValueError):
        prog = 0.0
    # 视频地址
    url = (data.get("video_url") or data.get("url") or data.get("videoUrl")
           or (data.get("data") or {}).get("video_url")
           or (data.get("data") or {}).get("url"))
    return {"status": status, "progress": prog, "video_url": url}


# ------------------------------------------------------------------ 对外主流程
def submit(script_text: str, avatar_id: str | None = None,
           voice_mode: str = "official", brand_audio: str | None = None) -> str:
    """提交一次第三方数字人视频任务，返回 task_id。"""
    c = get_avatar_config()
    if not is_configured():
        raise RuntimeError(
            "未配置第三方数字人：请在 model_keys.env 填写 "
            "THIRDPARTY_AVATAR_KEY 与 THIRDPARTY_AVATAR_BASE。")
    avatar_id = avatar_id or (c["avatars"][0]["id"] if c["avatars"] else None)
    if not avatar_id:
        raise RuntimeError(
            "未配置第三方官方形象（THIRDPARTY_AVATAR_AVATARS 为空）。")
    payload = _build_submit_payload(c, script_text, avatar_id, voice_mode, brand_audio)
    url = c["base"] + c["submit_path"]
    data = _http_json(url, payload, "POST", c["key"])
    return _parse_submit_result(c, data)


def query(task_id: str) -> dict:
    """轮询任务状态，返回归一化 {status, progress, video_url}。"""
    c = get_avatar_config()
    url = c["base"] + c["query_path"]
    # 多数厂商用 GET 带 task_id；少数用 POST，这里先用 GET，失败由调用方重试
    try:
        data = _http_json(f"{url}?task_id={urllib.parse.quote(task_id)}", None, "GET", c["key"])
    except Exception:
        data = _http_json(url, {"task_id": task_id}, "POST", c["key"])
    return _parse_query_result(c, data)


def download(video_url: str, out_path: str | Path, timeout: int = 120) -> None:
    """把第三方返回的临时视频地址下载到本地 out_path。"""
    out_path = Path(out_path)
    req = urllib.request.Request(video_url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out_path.write_bytes(resp.read())


def run(script_text: str, avatar_id: str | None, voice_mode: str,
        brand_audio: str | None, out_path: str | Path,
        on_progress=None, poll_interval: float = 5.0,
        max_wait: float = 1800.0) -> str:
    """
    端到端：提交 → 轮询（带进度回调）→ 下载到 out_path。
    返回本地视频路径。on_progress(step:str, progress:int) 用于上报进度。
    """
    task_id = submit(script_text, avatar_id, voice_mode, brand_audio)
    if on_progress:
        on_progress(f"已提交第三方数字人任务（{task_id[:16]}…），等待渲染", 5)
    waited = 0.0
    while waited < max_wait:
        time.sleep(poll_interval)
        waited += poll_interval
        r = query(task_id)
        if r["status"] == "done":
            if on_progress:
                on_progress("第三方渲染完成，下载视频中", 95)
            if not r["video_url"]:
                raise RuntimeError("第三方任务完成但未返回视频地址。")
            download(r["video_url"], out_path)
            if on_progress:
                on_progress("✅ 第三方数字人视频已生成", 100)
            return str(out_path)
        if r["status"] == "error":
            raise RuntimeError(f"第三方数字人渲染失败（task={task_id}）。")
        if on_progress:
            on_progress(f"第三方渲染中（{r['progress']:.0f}%）", max(5, int(r["progress"] * 0.9)))
    raise RuntimeError(f"第三方数字人渲染超时（>{int(max_wait)}s，task={task_id}）。")


if __name__ == "__main__":
    # 自检：未配置时给出清晰提示，不崩溃
    print("configured:", is_configured())
    print("info:", json.dumps(info(), ensure_ascii=False))
    try:
        submit("测试文案", None, "official", None)
    except RuntimeError as e:
        print("submit (未配置预期报错):", e)
