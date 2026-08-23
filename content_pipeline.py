#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内容生产链（千问主力版）
  主题/素材文本  ->  选题定位  ->  口播逐字稿  ->  二次改写(老张叙事风)  ->  千问TTS音频

依赖：
  - dashscope 文本模型 (qwen-turbo) 做选题/改写
  - dashscope cosyvoice-v3-plus 用已锁定的老张 v2 音色配音 (DEFAULT_VOICE_ID 在 qwen_tts.py)

用法:
  # 单条
  python content_pipeline.py run --topic "个人卡流水为什么会被税务盯上" --out-dir qwen_out/demo1

  # 批量（topics.txt 每行一个主题/素材）
  python content_pipeline.py batch --in topics.txt --out-dir qwen_out/

  # 只生成文案不配音（先审稿）
  python content_pipeline.py run --topic "..." --out-dir qwen_out/demo1 --no-audio

  # 人工审改完 03_逐字稿定稿.md 后，单独出音频（剥离三段标记、带违禁词检查）
  python content_pipeline.py tts --from qwen_out/demo1/03_逐字稿定稿.md --out qwen_out/demo1/04_音频.wav

环境变量: DASHSCOPE_API_KEY (百炼 Key，文本+语音共用)

二创稿结构（定稿 .md）：
  === 开头 === 抓眼球/痛点引入
  === 正文 === 核心讲解
  === 结尾（钩子） === 留资引导
违禁词红线：改写提示词内置各大平台禁用词+替换建议，改写后自动跑检查并落盘 03_违禁词检查.md
"""
import os
import sys
import time
import argparse
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qwen_tts import synth, synth_natural, DEFAULT_VOICE_ID, DEFAULT_MODEL
from forbidden_words import build_guidance, scan as scan_forbidden, format_report, clean_script
from model_providers import ensure_env, get_text_config, deepseek_chat
ensure_env()  # 让 model_keys.env 里的 key 自动生效

# 老张讲财税的口播风格（已与用户对齐：朋友聊天叙事、不居高临下、干净不啰嗦、留资钩子）
STYLE_GUIDE = """老张讲财税口播风格要求：
- 像跟朋友聊天叙事，不居高临下说教
- 干净叙事，不用"老哥/哥/唠/咱"等过度口语、大白话、口头禅
- 口语化但专业，财税术语准确
- 娓娓道来、像面对面交谈而非朗读脚本；关键处用短句重音式强调，节奏自然有起伏、不机械念稿
- 结尾带留资钩子（引导关注/私信/评论，自然不生硬）
- 命中老板刚需痛点（虚开发票、暂估成本、个人卡流水、公转私等）
- 时长 30-60 秒口播量，约 80-150 字，一句一意、节奏清晰

【财税术语准确度护栏 - 必须遵守】
- 概念严禁混淆：个人卡收营业款 = 私户收款/隐匿收入，绝不是"公转私"（公转私是公司账户转入个人账户）；二者本质不同，不可互换使用。
- 定性要稳妥：只讲"风险/可能被认定/易被系统比对发现异常"，不要写"一定被罚/一定坐牢/直接认定为XX罪"等绝对化结论。
- 不确定具体定性时，用通俗但中性的说法（如"这笔钱说不清来源，系统一比对就露馅"），绝不下错误定性结论。
- 涉及金额、比例、政策条文时，如不确定准确数字，宁可泛述也不要编具体数字。"""


def llm(prompt, retries=3):
    """按 model_providers 配置自动选 deepseek / qwen；任一家 key 即可用。
    模型名一律用配置里的（deepseek->deepseek-chat / qwen->qwen-turbo），
    不再透传写死参数，避免把 qwen-turbo 塞给 deepseek 接口。"""
    cfg = get_text_config()  # 没 key 时抛清晰异常
    prov = cfg["provider"]
    m = cfg["model"]
    last = None
    for i in range(retries):
        try:
            if prov == "deepseek":
                return deepseek_chat(prompt, m, cfg["key"], cfg["base_url"]).strip()
            # qwen / dashscope
            from dashscope import Generation
            return Generation.call(model=m, prompt=prompt, result_format="message").output.choices[0].message.content.strip()
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 * (i + 1))
    sys.exit(f"LLM 调用失败（重试 {retries} 次，供应商={prov}）: {last}")


def plan_topic(topic):
    p = (
        "你是财税短视频选题策划。\n"
        f"用户给的主题/素材：{topic}\n\n"
        "短视频爆款标准：7天互动率(赞+藏+评)/播放 ≥ 8%，必须命中老板刚需痛点，能挂留资钩子。\n"
        "请输出：\n1) 最值得做的选题角度（一句话）\n2) 为什么这个角度能戳中老板痛点（一两句）\n"
        f"\n{STYLE_GUIDE}"
    )
    return llm(p)


def write_script(topic, plan):
    p = (
        "根据选题写口播逐字稿正文。\n"
        f"主题/素材：{topic}\n选题角度：{plan}\n\n"
        "要求：直接输出逐字稿正文，不要任何解释、不要加引号或前缀，符合下面风格：\n"
        f"{STYLE_GUIDE}"
    )
    return llm(p)


def rewrite(script, req=""):
    fb = build_guidance()
    p = (
        "对以下口播稿做二次改写，保持原意与财税信息准确。\n"
        f"改写要求：{req or '更自然、像朋友聊天叙事、不啰嗦、节奏更顺'}\n\n"
        "【结构要求】输出必须严格分成三段，用下面标记包裹（便于人工审改，不要改动标记文字）：\n"
        "=== 开头 ===\n（抓眼球 / 痛点引入，1-2句）\n"
        "=== 正文 ===\n（核心讲解，3-5句，一句一意、节奏清晰）\n"
        "=== 结尾（钩子） ===\n（留资引导 / 关注，自然不生硬，1-2句）\n\n"
        f"{fb}\n\n"
        f"风格：\n{STYLE_GUIDE}\n\n"
        f"逐字稿：\n{script}\n\n"
        "直接输出三段式改写稿（含 === 标记），不要额外解释。"
    )
    return llm(p)


def run_one(topic, out_dir, rewrite_req="", no_audio=False):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] 选题定位: {topic[:30]}...")
    plan = plan_topic(topic)
    (out_dir / "01_选题.md").write_text(plan, encoding="utf-8")

    print("[2/4] 生成口播逐字稿...")
    draft = write_script(topic, plan)
    (out_dir / "02_逐字稿草稿.md").write_text(draft, encoding="utf-8")

    print("[3/4] 二次改写(老张叙事风 + 违禁词红线 + 三段式)...")
    final = rewrite(draft, rewrite_req)
    (out_dir / "03_逐字稿定稿.md").write_text(final, encoding="utf-8")
    print("  ---- 定稿（开头 / 正文 / 结尾（钩子）） ----")
    print(final)
    print("  --------------")

    # 违禁词检查（门禁）：命中高危写报告，供人工复核
    hits = scan_forbidden(final)
    if hits:
        rep = format_report(hits)
        (out_dir / "03_违禁词检查.md").write_text(rep, encoding="utf-8")
        print("[违禁词检查] 发现风险词，已写入 03_违禁词检查.md：")
        print(rep)
    else:
        print("[违禁词检查] ✅ 未发现违禁词风险")

    if no_audio:
        print("[4/4] 跳过配音(--no-audio)：请审阅 03_逐字稿定稿.md，确认后用 `tts` 子命令出音频")
        return final

    print("[4/4] 千问TTS配音(老张v2音色)...")
    audio_path = out_dir / "04_音频.wav"
    synth_natural(clean_script(final), DEFAULT_VOICE_ID, str(audio_path), model=DEFAULT_MODEL)
    print(f"  已保存: {audio_path}")
    return final


def tts_from(script_path, audio_path, model=DEFAULT_MODEL):
    """从已审改的定稿(.md，支持三段式标记)读取文本，剥离标记后配音。"""
    txt = Path(script_path).read_text(encoding="utf-8")
    final = clean_script(txt)
    Path(audio_path).parent.mkdir(parents=True, exist_ok=True)
    synth(final, DEFAULT_VOICE_ID, str(audio_path), model=model)
    print(f"  已生成音频: {audio_path}")
    hits = scan_forbidden(txt)
    if hits:
        print(format_report(hits))
    else:
        print("[违禁词检查] ✅ 定稿无违禁词风险")
    return final


def batch(in_src, out_dir, rewrite_req="", no_audio=False):
    p = Path(in_src)
    topics = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not topics:
        sys.exit("没有可处理的主题")
    out_dir = Path(out_dir)
    print(f"共 {len(topics)} 条")
    for i, t in enumerate(topics, 1):
        print(f"\n===== [{i}/{len(topics)}] =====")
        run_one(t, out_dir / f"{i:03d}", rewrite_req=rewrite_req, no_audio=no_audio)


def main():
    ap = argparse.ArgumentParser(description="内容生产链：选题→逐字稿→改写→千问TTS音频")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("run", help="单条")
    s1.add_argument("--topic", required=True)
    s1.add_argument("--out-dir", required=True)
    s1.add_argument("--rewrite", default="")
    s1.add_argument("--no-audio", action="store_true")

    s2 = sub.add_parser("batch", help="批量(txt每行一主题)")
    s2.add_argument("--in", dest="in_src", required=True)
    s2.add_argument("--out-dir", required=True)
    s2.add_argument("--rewrite", default="")
    s2.add_argument("--no-audio", action="store_true")

    s3 = sub.add_parser("tts", help="从已审改的定稿.md生成音频(三段式也可)")
    s3.add_argument("--from", dest="script_path", required=True)
    s3.add_argument("--out", required=True)

    args = ap.parse_args()
    if args.cmd == "run":
        run_one(args.topic, args.out_dir, rewrite_req=args.rewrite, no_audio=args.no_audio)
    elif args.cmd == "batch":
        batch(args.in_src, args.out_dir, rewrite_req=args.rewrite, no_audio=args.no_audio)
    elif args.cmd == "tts":
        tts_from(args.script_path, args.out)


if __name__ == "__main__":
    if not os.environ.get("DASHSCOPE_API_KEY"):
        sys.exit("请先设置 DASHSCOPE_API_KEY")
    main()
