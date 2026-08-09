#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周更内容创作自动化流水线（老张讲财税）
=================================================================
复用既有后端，不改动 8385 工作台 / make_scroll_video / make_avatar_video：
  - 选题/二创：model_providers.get_text_config() + deepseek_chat（DeepSeek）
  - 违禁词：forbidden_words.scan / build_guidance
  - 成片：make_scroll_video.py（滚动字幕卡，双声/单声皆可用，无需 Docker）

子命令：
  gen-topics [--week W] [--limit N]       生成 N 条选题 → queue.json + topics md
  select     [--week W] [--mode auto|manual] [--keep 1,2] [--drop 3,7]
  create-scripts [--week W] [--limit N]   为已选选题生成口播稿（过违禁词）
  render     [--week W] [--slot afternoon|evening] [--seq N] [--dry-tts]
  archive    [--keep 4]                    旧周归档
  health                             探活 8385
  week                               打印当前 ISO 周号

调度（由自动化框架 / Task Scheduler 调用）：
  周日 23:30  gen-topics          （生成 14 条）
  周一 08:00  select --mode auto + create-scripts   （确认兜底 + 批量二创）
  每天 15:00  render --slot afternoon
  每天 20:00  render --slot evening
"""
from __future__ import annotations
import os
import sys
import json
import argparse
import subprocess
import shutil
import datetime
import re
import textwrap

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import model_providers as mp
import forbidden_words as fw

PY = sys.executable
WEEKLY_ROOT = os.path.join(BASE, "output", "weekly")
FFMPEG_BINS = [
    r"D:\ffmpeg\ffmpeg-8.1.2-full_build\bin",
    r"C:\ffmpeg\bin",
]


# ------------------------------------------------------------------ 基础工具
def _extend_path():
    for b in FFMPEG_BINS:
        if os.path.isdir(b) and b not in os.environ.get("PATH", ""):
            os.environ["PATH"] = b + ";" + os.environ.get("PATH", "")


def week_key(d=None):
    d = d or datetime.datetime.now()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_dir(wk=None):
    return os.path.join(WEEKLY_ROOT, wk or week_key())


def ensure_week_dir(wk):
    d = week_dir(wk)
    for sub in ("topics", "scripts", "audio", "videos", "pkg"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    return d


def queue_path(wk):
    return os.path.join(week_dir(wk), "queue.json")


def load_queue(wk):
    p = queue_path(wk)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_queue(wk, q):
    with open(queue_path(wk), "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=2)


def load_profile():
    p = os.path.join(BASE, "account_profile.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"account": "老张讲财税", "weekly_count": 14,
            "subtitle": "老张讲财税 · 老板避坑",
            "hit_topics": ["公转私", "虚开发票", "个人卡流水"]}


def slugify(s):
    s = re.sub(r"[^\w一-龥-]", "_", s.strip())
    return s[:24].strip("_") or "topic"


def slot_for(seq):
    """seq 1-based → {day:0=Mon, period}。1=周一15:00,2=周一20:00,...14=周日20:00"""
    idx = seq - 1
    day = idx // 2
    period = "afternoon" if idx % 2 == 0 else "evening"
    return {"day": day, "period": period}


# ------------------------------------------------------------------ LLM
def llm(prompt, enable_search=False, timeout=120):
    cfg = mp.get_text_config()
    return mp.deepseek_chat(prompt, cfg["model"], cfg["key"],
                            cfg["base_url"], timeout=timeout, enable_search=enable_search)


def _strip_fence(s):
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n", "", s)
        s = s.rstrip("`").strip()
    return s


def llm_json(prompt, expect_obj=True, retries=2):
    last_err = None
    for _ in range(retries + 1):
        try:
            raw = llm(prompt, timeout=120)
            raw = _strip_fence(raw).strip()
            try:
                return json.loads(raw)
            except Exception:
                pass
            if expect_obj:
                m = re.search(r"\{.*\}", raw, re.DOTALL)
            else:
                m = re.search(r"\[.*\]", raw, re.DOTALL)
            if m:
                return json.loads(m.group(0))
            last_err = ValueError("响应中未找到 JSON")
        except Exception as e:  # noqa
            last_err = e
        prompt = prompt + "\n\n⚠️ 上次未返回可解析的 JSON，请只返回严格 JSON，不要任何解释或代码块标记。"
    raise RuntimeError(f"LLM 返回无法解析为 JSON：{last_err}")


# ------------------------------------------------------------------ 提示词
def build_topics_prompt(prof, n):
    hit = "、".join(prof.get("hit_topics", []))
    return textwrap.dedent(f"""\
    你是资深财税短视频选题策划，服务账号「{prof.get('account')}」。
    人设：{prof.get('persona')}；受众：{prof.get('audience')}；调性：{prof.get('tone')}。
    核心痛点池：{hit}。

    请基于账号定位，生成 {n} 条本周可发的爆款短视频选题。要求：
    1. 紧扣老板刚需痛点，结合近期税务政策/稽查热点（已联网检索）。
    2. 每条有强钩子、能挂留资。
    3. 为每条判断成片形态 style：
       - "dialogue" = 男女双声对话（适合争议/场景演绎）
       - "monologue" = 老张单声独白（适合干货讲解/风险提示）
       两种形态尽量均衡分布。
    4.     返回严格 JSON 数组（即使只有 1 条也要是数组），每项：
       {{"title"(≤14字),"angle","pain","hook","platform","duration"(30-60整数),"relevance"(0-100整数),"style"}}
    只返回 JSON 数组，不要任何解释或代码块标记。
    """)


def build_script_prompt(prof, t):
    style = t.get("style", "monologue")
    if style == "dialogue":
        fmt = ('输出男女对话稿：每行以 "女：" 或 "男：" 开头（女=江老师做引导/追问，男=老张讲风险），'
               '6-10 句，有场景感、像真实对话。')
    else:
        fmt = ('输出三段式独白，用标记分段：=== 开头 === / === 正文 === / === 结尾（钩子） ===，'
               '老张口语化、去 AI 痕迹。')
    guide = fw.build_guidance()
    return textwrap.dedent(f"""\
    你是「{prof.get('account')}」的资深脚本编剧。人设：{prof.get('persona')}；调性：{prof.get('tone')}。

    为以下选题写一条短视频口播稿：
    标题：{t.get('title')}
    角度：{t.get('angle')}
    老板痛点：{t.get('pain')}
    开头钩子：{t.get('hook')}
    成片形态：{style}

    要求：
    - {fmt}
    - 严禁使用以下违禁词（出现即用合规表述替换，不可留原词）：
    {guide}
    - 另输出发布文案 publish：一行标题 + 3-5 个 #话题标签 + 一句引导（用"评论区聊聊"等软引导，绝不出现加微信/扫码/留电话等）。
    返回严格 JSON：{{"script":"...","publish":"..."}}
    只返回 JSON，不要代码块标记。
    """)


# ------------------------------------------------------------------ 文本→对话稿 feed
def to_dialogue_feed(script_text, style):
    if style == "dialogue":
        out = []
        for ln in script_text.splitlines():
            s = ln.strip()
            if not s:
                continue
            if re.match(r"^(女|男)\s*[:：]", s):
                out.append(s)
        if not out:
            out = [l.strip() for l in script_text.splitlines() if l.strip()]
        return "\n".join(out)
    clean = fw.clean_script(script_text)
    return "\n".join("男：" + l for l in clean.splitlines() if l.strip())


def ensure_clean(text, style, retries=2):
    for _ in range(retries + 1):
        hits = fw.scan(text)
        high = [h for h in hits if h["level"] == "high" and not h.get("need_human")]
        if not high:
            return text
        hitstr = "；".join(f"{h['word']}→{h.get('suggest','')}" for h in high)
        prompt = (f"以下口播稿命中违禁词，请在不改变意思前提下替换掉这些词（{hitstr}），"
                  f"保持原有角色标记/分段格式，返回纯文本：\n\n{text}")
        try:
            text = llm(prompt, timeout=90)
        except Exception:
            return text
    return text


# ------------------------------------------------------------------ 子命令
def cmd_gen_topics(args):
    prof = load_profile()
    wk = args.week or week_key()
    d = ensure_week_dir(wk)
    n = args.limit or prof.get("weekly_count", 14)
    print(f"[gen-topics] 调用 DeepSeek 生成 {n} 条选题（联网检索）…")
    raw = llm(build_topics_prompt(prof, n), enable_search=True, timeout=150)
    try:
        topics = llm_json(_strip_fence(raw), expect_obj=False)
        if isinstance(topics, dict):
            topics = [topics]
    except Exception as e:
        print("  选题解析失败：", e)
        return
    q = {"week": wk, "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
         "confirmed": False, "topics": []}
    for i, t in enumerate(topics[:n], 1):
        q["topics"].append({
            "seq": i,
            "title": t.get("title", f"选题{i}"),
            "angle": t.get("angle", ""),
            "pain": t.get("pain", ""),
            "hook": t.get("hook", ""),
            "platform": t.get("platform", ""),
            "duration": int(t.get("duration", 45)),
            "relevance": int(t.get("relevance", 80)),
            "style": t.get("style", "monologue"),
            "selected": True,
            "script_path": None,
            "dialogue_path": None,
            "video_path": None,
            "status": "pending",
            "target_slot": slot_for(i),
        })
    save_queue(wk, q)
    write_topics_md(wk, q)
    print(f"[gen-topics] 完成 {len(q['topics'])} 条 → {queue_path(wk)}")


def cmd_select(args):
    wk = args.week or week_key()
    q = load_queue(wk)
    if not q:
        print(f"[select] 未找到 {wk} 队列，请先 gen-topics"); return
    if args.keep:
        ks = set(int(x) for x in args.keep.split(",") if x.strip())
        for t in q["topics"]:
            t["selected"] = t["seq"] in ks
        q["confirmed"] = True
    elif args.drop:
        ds = set(int(x) for x in args.drop.split(",") if x.strip())
        for t in q["topics"]:
            if t["seq"] in ds:
                t["selected"] = False
        q["confirmed"] = True
    else:  # auto
        if not q["confirmed"]:
            q["confirmed"] = True
    save_queue(wk, q)
    print(f"[select] confirmed={q['confirmed']}，已选 "
          f"{sum(1 for t in q['topics'] if t['selected'])} 条")


def _make_one_script(wk, t):
    prof = load_profile()
    d = week_dir(wk)
    slug = slugify(t["title"])
    script_md = os.path.join(d, "scripts", f"{t['seq']:02d}_{slug}.md")
    dlg = os.path.join(d, "scripts", f"{t['seq']:02d}_{slug}.dlg.txt")
    body = build_script_prompt(prof, t)
    print(f"  · 二创 #{t['seq']} {t['title']}（{t['style']}）…")
    data = llm_json(body, expect_obj=True)
    script_text = ensure_clean(data.get("script", ""), t["style"])
    publish = data.get("publish", "")
    with open(script_md, "w", encoding="utf-8") as f:
        f.write(script_text)
    feed = to_dialogue_feed(script_text, t["style"])
    with open(dlg, "w", encoding="utf-8") as f:
        f.write(feed)
    t["script_path"] = script_md
    t["dialogue_path"] = dlg
    t["publish"] = publish
    t["status"] = "scripted"


def cmd_create_scripts(args):
    wk = args.week or week_key()
    q = load_queue(wk)
    if not q:
        print(f"[create-scripts] 未找到 {wk} 队列"); return
    cnt = 0
    for t in q["topics"]:
        if not t["selected"] or t["status"] == "done":
            continue
        if t.get("script_path") and os.path.exists(t["script_path"]):
            continue
        if args.limit and cnt >= args.limit:
            break
        _make_one_script(wk, t)
        cnt += 1
    save_queue(wk, q)
    write_readme(wk, q)
    print(f"[create-scripts] 生成 {cnt} 条口播稿")


def cmd_render(args):
    _extend_path()
    wk = args.week or week_key()
    q = load_queue(wk)
    if not q:
        print(f"[render] 未找到 {wk} 队列"); return
    target = None
    for t in sorted(q["topics"], key=lambda x: x["seq"]):
        if args.seq:
            if t["seq"] == args.seq:
                target = t; break
        else:
            if t["selected"] and t["status"] != "done" and \
               t.get("target_slot", {}).get("period") == args.slot:
                target = t; break
    if not target:
        print(f"[render {args.slot or args.seq}] 本周无待渲染选题"); return
    # 确保脚本存在
    if not (target.get("script_path") and os.path.exists(target["script_path"])):
        _make_one_script(wk, target)
        save_queue(wk, q)
    prof = load_profile()
    d = week_dir(wk)
    slug = slugify(target["title"])
    out = os.path.join(d, "videos", f"{wk}-{target['seq']:02d}-{slug}.mp4")
    dlg = target.get("dialogue_path") or os.path.join(d, "audio", f"{target['seq']:02d}_dlg.txt")
    if not os.path.exists(dlg):
        feed = to_dialogue_feed(open(target["script_path"], encoding="utf-8").read(), target["style"])
        with open(dlg, "w", encoding="utf-8") as f:
            f.write(feed)
    title = target["title"][:10]
    subtitle = prof.get("subtitle", "老张讲财税 · 老板避坑")
    # 显式传入男女克隆音色：优先读 env（QWEN_MALE/FEMALE_VOICE_ID），缺则回退 documented 默认值。
    # 注：make_scroll_video.py 当前 MALE/FEMALE_VOICE 默认空（待「声音」页配置），
    # 自动化流水线无 UI，故在此强制传入，避免 TTS 报 voice_id 为空。
    male_v = mp.get_key("QWEN_MALE_VOICE_ID") or "cosyvoice-v3-plus-zhangc2-28a7c3541e1c45518a03046c11baeb1d"
    female_v = mp.get_key("QWEN_FEMALE_VOICE_ID") or "cosyvoice-v3-plus-jiangnv3-991b204c1d564ac7a60f0cb9a8fd78bd"
    cmd = [PY, os.path.join(BASE, "make_scroll_video.py"),
           "--dialogue", dlg, "--out", out,
           "--title", title, "--subtitle", subtitle, "--gap", "0.18",
           "--male-voice", male_v, "--female-voice", female_v]
    bg = prof.get("bg")
    if bg and os.path.exists(bg):
        cmd += ["--bg", bg]
        bg_fit = prof.get("bg_fit", "fill")
        if bg_fit in ("fill", "contain", "stretch"):
            cmd += ["--bg-fit", bg_fit]
    if args.dry_tts:
        cmd.append("--dry-tts")
    print(f"[render] #{target['seq']} {target['title']} → {out}")
    subprocess.run(cmd, check=True)
    side = out[:-4] + ".txt"
    with open(side, "w", encoding="utf-8") as f:
        f.write(build_sidecar(target))
    target["video_path"] = out
    target["status"] = "done"
    save_queue(wk, q)
    write_readme(wk, q)
    print(f"[render] 完成：{out}")


def cmd_archive(args):
    weeks = sorted(d for d in os.listdir(WEEKLY_ROOT)
                  if re.match(r"^\d{4}-W\d{2}$", d)) if os.path.isdir(WEEKLY_ROOT) else []
    if len(weeks) <= args.keep:
        print(f"[archive] 无需归档（{len(weeks)} 周 ≤ {args.keep}）"); return
    arch = os.path.join(WEEKLY_ROOT, "_archive")
    os.makedirs(arch, exist_ok=True)
    for w in weeks[:-args.keep]:
        src = os.path.join(WEEKLY_ROOT, w)
        dst = os.path.join(arch, w)
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.move(src, dst)
        print(f"[archive] {w} → _archive/")


def cmd_health(args):
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:8385/api/queue", timeout=5)
        print("8385 OK")
    except Exception as e:  # noqa
        print("8385 DOWN:", e)


def cmd_week(args):
    print(week_key())


# ------------------------------------------------------------------ 文档
def write_topics_md(wk, q):
    d = week_dir(wk)
    p = os.path.join(d, "topics", "week_topics.md")
    lines = [f"# {wk} 本周选题（{len(q['topics'])} 条）", "",
             f"> 生成时间：{q['generated_at']}　确认状态：{'已确认' if q['confirmed'] else '待确认（可 keep/drop 调整）'}", "",
             "| # | 标题 | 形态 | 适合平台 | 时长 | 相关度 | 痛点 |",
             "|---|---|---|---|---|---|---|"]
    for t in q["topics"]:
        lines.append(f"| {t['seq']} | {t['title']} | {t['style']} | {t['platform']} | "
                     f"{t['duration']}s | {t['relevance']} | {t['pain']} |")
    lines += ["", "## 调整方式",
              "- 只要其中若干条：`weekly_pipeline.py select --keep 1,2,5,8`",
              "- 去掉若干条：`weekly_pipeline.py select --drop 3,7,11`",
              "- 不调则周一 08:00 自动确认并全部二创。"]
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_readme(wk, q):
    d = week_dir(wk)
    p = os.path.join(d, "README.md")
    lines = [f"# {wk} 周更清单", "",
             f"- 生成：{q['generated_at']}　确认：{'是' if q['confirmed'] else '否'}"
             f"　已选：{sum(1 for t in q['topics'] if t['selected'])}", "",
             "| # | 标题 | 形态 | 已选 | 状态 | 成片 |",
             "|---|---|---|---|---|---|"]
    for t in q["topics"]:
        vp = os.path.relpath(t["video_path"], d) if t.get("video_path") else "—"
        lines.append(f"| {t['seq']} | {t['title']} | {t['style']} | "
                     f"{'✓' if t['selected'] else '✗'} | {t['status']} | {vp} |")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def build_sidecar(t):
    parts = [f"标题：{t['title']}", f"形态：{t['style']}", f"适合平台：{t['platform']}", ""]
    if t.get("publish"):
        parts.append("【发布文案】")
        parts.append(t["publish"])
        parts.append("")
    if t.get("script_path") and os.path.exists(t["script_path"]):
        parts.append("【口播稿】")
        parts.append(open(t["script_path"], encoding="utf-8").read())
    return "\n".join(parts)


# ------------------------------------------------------------------ CLI
def main():
    ap = argparse.ArgumentParser(description="周更内容创作自动化流水线")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("gen-topics")
    p1.add_argument("--week"); p1.add_argument("--limit", type=int)
    p1.set_defaults(func=cmd_gen_topics)

    p2 = sub.add_parser("select")
    p2.add_argument("--week")
    p2.add_argument("--mode", default="auto")
    p2.add_argument("--keep")
    p2.add_argument("--drop")
    p2.set_defaults(func=cmd_select)

    p3 = sub.add_parser("create-scripts")
    p3.add_argument("--week"); p3.add_argument("--limit", type=int)
    p3.set_defaults(func=cmd_create_scripts)

    p4 = sub.add_parser("render")
    p4.add_argument("--week")
    p4.add_argument("--slot", choices=["afternoon", "evening"], required=False)
    p4.add_argument("--seq", type=int)
    p4.add_argument("--dry-tts", action="store_true")
    p4.set_defaults(func=cmd_render)

    p5 = sub.add_parser("archive")
    p5.add_argument("--keep", type=int, default=4)
    p5.set_defaults(func=cmd_archive)

    p6 = sub.add_parser("health")
    p6.set_defaults(func=cmd_health)

    p7 = sub.add_parser("week")
    p7.set_defaults(func=cmd_week)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
