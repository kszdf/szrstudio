#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阿里(CosyVoice) vs 火山(豆包 声音复刻2.0) 配音真实度 A/B 对比脚本
=================================================================
用法（在你自己电脑、已配好 key 的环境运行）：
  python tts_ab_compare.py --text "要合成的同一段文案" --out-dir ./ab_test

前置条件：
  [阿里]  model_keys.env 里 DASHSCOPE_API_KEY 已填；老张 voice_id 沿用 qwen_tts.DEFAULT_VOICE_ID
  [火山]  ① 火山控制台「我的声音 > 复刻音色」上传老张录音(10-30s wav)，训练得到 voice_type
          ② 在火山 TTS 控制台「鉴权信息」拿到 appid / token / cluster / resource_id
          ③ 在 model_keys.env 追加：
              VOLC_APP_ID=...
              VOLC_TOKEN=...
              VOLC_CLUSTER=volcano_tts          (或你资源包对应的 cluster)
              VOLC_RESOURCE_ID=volc.service_type.xxxxx   (声音复刻资源，例如 20001 类)
              VOLC_VOICE_TYPE=你的克隆音色id
说明：
  - 用【同一段文本】分别调用两家，输出 A_阿里.wav 与 B_火山.mp3，戴耳机亲耳比。
  - 真实度是主观听感，本脚本只负责出样片，不替你打分。
  - 任一家没配 key，会自动跳过那一家并提示，不会报错中断。
"""
import os
import sys
import json
import uuid
import base64
import argparse
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# 加载本地 model_keys.env（与 model_providers 同一套）
try:
    from model_providers import ensure_env
    ensure_env()
except Exception:
    pass


def synth_aliyun(text, out_path):
    """复用现有 qwen_tts 出阿里 CosyVoice 音频（老张现有声音）。"""
    try:
        import qwen_tts
    except Exception as e:
        return False, f"无法导入 qwen_tts: {e}"
    if not os.environ.get("DASHSCOPE_API_KEY"):
        return False, "未配置 DASHSCOPE_API_KEY（阿里），跳过阿里样片"
    try:
        qwen_tts.synth(text, qwen_tts.DEFAULT_VOICE_ID, str(out_path),
                       model=getattr(qwen_tts, "DEFAULT_MODEL", None))
        return True, str(out_path)
    except Exception as e:
        return False, f"阿里合成失败: {e}"


def synth_volc(text, out_path):
    """调用火山(豆包)声音复刻 TTS，输出 mp3。"""
    try:
        import requests
    except Exception as e:
        return False, f"缺少 requests 库（pip install requests）: {e}"
    appid = os.environ.get("VOLC_APP_ID")
    token = os.environ.get("VOLC_TOKEN")
    voice_type = os.environ.get("VOLC_VOICE_TYPE")
    if not (appid and token and voice_type):
        return False, "未配置火山 VOLC_APP_ID / VOLC_TOKEN / VOLC_VOICE_TYPE，跳过火山样片"
    cluster = os.environ.get("VOLC_CLUSTER", "volcano_tts")
    resource_id = os.environ.get("VOLC_RESOURCE_ID", "")
    headers = {
        "Authorization": f"Bearer; {appid}",
        "Resource-Id": resource_id,
        "Content-Type": "application/json",
    }
    body = {
        "app": {"appid": appid, "token": token, "cluster": cluster},
        "user": {"uid": "zhang_user"},
        "audio": {"voice_type": voice_type, "encoding": "mp3", "speed_ratio": 1.0},
        "request": {"reqid": str(uuid.uuid4()), "text": text},
    }
    try:
        resp = requests.post("https://openspeech.bytedance.com/api/v1/tts",
                             json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 3000:
            return False, f"火山返回错误: {data}"
        audio = base64.b64decode(data["data"])
        Path(out_path).write_bytes(audio)
        return True, str(out_path)
    except Exception as e:
        return False, f"火山合成失败（请核对 cluster/resource_id/voice_type 是否匹配你买的资源包）: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True, help="同一段要合成的文案")
    ap.add_argument("--out-dir", default="./ab_test")
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"文案: {args.text[:40]}...\n")
    a_ok, a_msg = synth_aliyun(args.text, out / "A_阿里.wav")
    print(f"[{'✅' if a_ok else '⚠️'}] 阿里 : {a_msg}")

    b_ok, b_msg = synth_volc(args.text, out / "B_火山.mp3")
    print(f"[{'✅' if b_ok else '⚠️'}] 火山 : {b_msg}")

    print("\n---- 结果 ----")
    if a_ok and b_ok:
        print(f"两份样片已生成，戴耳机对比：\n  A = {a_msg}\n  B = {b_msg}")
    elif a_ok:
        print("只有阿里样片（火山未配置）。想比就在 model_keys.env 填火山四项后重跑。")
    elif b_ok:
        print("只有火山样片（阿里未配置）。")
    else:
        print("两家都没出片——请先按文件顶部说明配置 key。")


if __name__ == "__main__":
    main()
