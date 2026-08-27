#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
各大平台短视频违禁词 / 敏感词库（财税口播号专项）
================================================
用途：
  1) 给二次改写 LLM 当"红线提示词"（build_guidance()）
  2) 给机械筛查器当词表（scan()）
  3) 直接命令行跑：python forbidden_words.py <文件或文本> [--platform 抖音]

覆盖平台：抖音 / 视频号 / 快手 / 小红书 / B站
重点场景：财税科普、老板获客、税务风险提醒（最易被"极限词 + 诱导导流 + 财经承诺"三类误伤封号）

每条字段：
  word     违禁词 / 敏感词
  level    high=高危(出现即可能限流/封号) | medium=中等(视语境)
  platforms 命中平台（空=全平台通用）
  suggest  合规替代表述
  note     为什么危险 / 边界说明
  exact    True=精确子串命中即报；False=单字/弱约束，命中后标"需人工核对语境"

维护：财税政策与平台规则会变，建议每季度人工复核一次本词表。
"""
from __future__ import annotations
import sys
import re
from pathlib import Path

PLATFORMS = ["抖音", "视频号", "快手", "小红书", "B站"]

# ------------------------------------------------------------------ 词表
# category 仅用于 build_guidance 分组展示，不参与筛查逻辑
FORBIDDEN: list[dict] = [
    # ---------- 1) 绝对化 / 极限词（广告法，全平台职业打假重灾区） ----------
    dict(word="最", level="high", platforms=[], suggest="很/比较/多数/不少/在行内", note="广告法绝对化用语；作为最高级修饰时高危（最便宜/最好/最大）", exact=False),
    dict(word="最佳", level="high", platforms=[], suggest="很合适/比较稳妥", note="绝对化用语", exact=True),
    dict(word="最好", level="high", platforms=[], suggest="挺靠谱/比较稳妥", note="绝对化用语", exact=True),
    dict(word="最大", level="high", platforms=[], suggest="较大/不少", note="绝对化用语", exact=True),
    dict(word="第一", level="high", platforms=[], suggest="靠前/头部之一", note="广告法禁用'第一'", exact=True),
    dict(word="唯一", level="high", platforms=[], suggest="少有/难得", note="绝对化用语", exact=True),
    dict(word="顶级", level="high", platforms=[], suggest="专业/资深", note="绝对化用语", exact=True),
    dict(word="国家级", level="high", platforms=[], suggest="正规/合规", note="广告法禁用", exact=True),
    dict(word="世界级", level="high", platforms=[], suggest="行业内有口碑", note="绝对化用语", exact=True),
    dict(word="极品", level="high", platforms=[], suggest="优质", note="绝对化用语", exact=True),
    dict(word="极致", level="high", platforms=[], suggest="到位", note="绝对化用语", exact=True),
    dict(word="万能", level="high", platforms=[], suggest="好用/顺手", note="绝对化用语", exact=True),
    dict(word="绝对", level="high", platforms=[], suggest="基本/通常", note="绝对化用语（'绝对安全'等）", exact=True),
    dict(word="百分百", level="high", platforms=[], suggest="大概率/多数情况", note="绝对化承诺", exact=True),
    dict(word="100%", level="high", platforms=[], suggest="大概率/多数情况", note="绝对化承诺", exact=True),
    dict(word="首选", level="high", platforms=[], suggest="可以考虑", note="广告法禁用", exact=True),
    dict(word="独家", level="high", platforms=[], suggest="特色/少见", note="绝对化用语", exact=True),
    dict(word="王牌", level="high", platforms=[], suggest="主力/核心", note="绝对化用语", exact=True),
    dict(word="遥遥领先", level="high", platforms=[], suggest="有优势", note="绝对化用语，易触发审核", exact=True),
    dict(word="No.1", level="high", platforms=[], suggest="靠前", note="广告法禁用'第一'", exact=True),
    dict(word="领导品牌", level="high", platforms=[], suggest="业内常见服务商", note="绝对化用语", exact=True),
    dict(word="销量第一", level="high", platforms=[], suggest="销量不错", note="广告法禁用", exact=True),
    dict(word="空前绝后", level="high", platforms=[], suggest="少见", note="绝对化用语", exact=True),
    dict(word="完美", level="high", platforms=[], suggest="比较到位", note="绝对化用语，慎用", exact=True),

    # ---------- 2) 财税违规 / 敏感表述（财税号特有高危） ----------
    dict(word="避税", level="high", platforms=["抖音", "视频号", "小红书"], suggest="合规优化税负 / 税务合规安排", note="作为'方法建议'出现即违规；讲风险时可说'一旦被认定避税'", exact=True),
    dict(word="偷税", level="high", platforms=[], suggest="仅可在'风险提醒'语境讲'被认定偷逃税'，绝不作操作建议", note="作建议=教唆违法", exact=True),
    dict(word="逃税", level="high", platforms=[], suggest="同上，仅作风险提醒", note="作建议=教唆违法", exact=True),
    dict(word="节税", level="medium", platforms=["视频号", "小红书"], suggest="合规降负 / 优化税负", note="部分平台把'节税'视作违规筹划话术", exact=True),
    dict(word="返税", level="medium", platforms=["抖音", "视频号"], suggest="地方财政奖补（需说明政策依据，谨慎）", note="易被认定为违规返利诱导", exact=True),
    dict(word="税收洼地", level="medium", platforms=["抖音", "视频号"], suggest="合规的区域性财政政策", note="易被认定为违规筹划引流", exact=True),
    dict(word="包过", level="high", platforms=[], suggest="协助办理 / 按流程推进", note="承诺类违规", exact=True),
    dict(word="保过", level="high", platforms=[], suggest="协助办理", note="承诺类违规", exact=True),
    dict(word="必下款", level="high", platforms=[], suggest="符合条件可正常办理", note="承诺类违规", exact=True),
    dict(word="稳过", level="high", platforms=[], suggest="按流程办理", note="承诺类违规", exact=True),
    dict(word="内部渠道", level="high", platforms=[], suggest="正规办理流程", note="暗示走后门，违规", exact=True),
    dict(word="找关系", level="high", platforms=[], suggest="走正规流程", note="暗示违规操作", exact=True),
    dict(word="搞定", level="medium", platforms=[], suggest="办好 / 处理妥当", note="在'税务/工商'语境易暗示违规操作", exact=True),
    dict(word="0元", level="medium", platforms=["抖音", "小红书"], suggest="低成本 / 首单优惠（如有真实活动）", note="无依据'0元'易判虚假宣传", exact=True),
    dict(word="免费", level="medium", platforms=["抖音", "小红书"], suggest="有真实活动才用，并说明条件", note="无依据'免费'易判虚假宣传", exact=True),

    # ---------- 3) 诱导导流 / 留联系方式（封号红线，各平台最严） ----------
    dict(word="加微信", level="high", platforms=[], suggest="评论区交流 / 主页看介绍", note="站外导流红线，高危封号", exact=True),
    dict(word="微信号", level="high", platforms=[], suggest="不出现具体账号", note="留联系方式红线", exact=True),
    dict(word="威信", level="high", platforms=[], suggest="不出现", note="'微信'变体规避，照样判", exact=True),
    dict(word="V信", level="high", platforms=[], suggest="不出现", note="'微信'变体规避，照样判", exact=True),
    dict(word="加V", level="high", platforms=[], suggest="不出现", note="'微信'变体规避，照样判", exact=True),
    dict(word="vx", level="high", platforms=[], suggest="不出现", note="'微信'变体规避，照样判", exact=True),
    dict(word="扫码", level="high", platforms=[], suggest="不引导扫码", note="导流红线", exact=True),
    dict(word="扫一扫", level="high", platforms=[], suggest="不引导", note="导流红线", exact=True),
    dict(word="二维码", level="high", platforms=[], suggest="不提及", note="导流红线", exact=True),
    dict(word="留电话", level="high", platforms=[], suggest="不出现", note="留联系方式红线", exact=True),
    dict(word="留方式", level="high", platforms=[], suggest="不出现", note="留联系方式红线", exact=True),
    dict(word="私信我", level="medium", platforms=["视频号", "抖音"], suggest="用'评论区聊聊'等软引导", note="部分平台对'私信我'限流", exact=True),
    dict(word="加好友", level="high", platforms=[], suggest="不出现", note="导流红线", exact=True),
    dict(word="点击链接", level="high", platforms=[], suggest="不引导点击外链", note="站外导流红线", exact=True),
    dict(word="下载APP", level="high", platforms=[], suggest="不引导", note="站外导流红线", exact=True),
    dict(word="公众号搜", level="high", platforms=[], suggest="不出现具体公众号名引导搜索", note="导流红线", exact=True),

    # ---------- 4) 收益 / 理财承诺（财经严管） ----------
    dict(word="稳赚", level="high", platforms=[], suggest="有机会 / 看具体情况", note="承诺收益违规", exact=True),
    dict(word="稳赚不赔", level="high", platforms=[], suggest="不出现", note="承诺收益违规", exact=True),
    dict(word="无风险", level="high", platforms=[], suggest="风险可控 / 注意甄别", note="承诺无风险违规", exact=True),
    dict(word="保本", level="high", platforms=[], suggest="不出现", note="理财承诺违规", exact=True),
    dict(word="高收益", level="high", platforms=[], suggest="合理回报 / 看项目", note="夸大收益违规", exact=True),
    dict(word="躺赚", level="high", platforms=[], suggest="不出现", note="夸大收益违规", exact=True),
    dict(word="guaranteed", level="high", platforms=[], suggest="不出现", note="英文'保证'，承诺类违规", exact=True),

    # ---------- 5) 时政 / 敏感 ----------
    dict(word="领导人", level="high", platforms=[], suggest="不点名、不评论时政", note="时政敏感", exact=False),
    dict(word="涉军", level="high", platforms=[], suggest="不出现", note="涉密敏感", exact=False),
    dict(word="涉密", level="high", platforms=[], suggest="不出现", note="涉密敏感", exact=False),

    # ---------- 6) 医疗 / 功效（模板预留，财税号一般不用） ----------
    dict(word="治愈", level="medium", platforms=[], suggest="不出现", note="医疗功效违规（预留）", exact=True),
    dict(word="根治", level="medium", platforms=[], suggest="不出现", note="医疗功效违规（预留）", exact=True),
]

CATEGORY_TITLE = {
    "极限词": "1) 绝对化 / 极限词（广告法，全平台职业打假重灾区）",
    "财税敏感": "2) 财税违规 / 敏感表述（财税号特有高危）",
    "诱导导流": "3) 诱导导流 / 留联系方式（封号红线）",
    "收益承诺": "4) 收益 / 理财承诺（财经严管）",
    "时政敏感": "5) 时政 / 敏感",
    "医疗功效": "6) 医疗 / 功效（模板预留）",
}


# ------------------------------------------------------------------ 三段式结构清洗
import re as _re

_MARKER_RE = _re.compile(r"^\s*={3,}\s*.+?\s*={3,}\s*$", _re.MULTILINE)


def clean_script(text: str) -> str:
    """去掉【开头/正文/结尾（钩子）】等 === 标记行，返回纯净口播正文（供 TTS/字幕）。"""
    lines = [ln for ln in text.splitlines() if not _MARKER_RE.match(ln)]
    out = "\n".join(ln.strip() for ln in lines if ln.strip())
    return _re.sub(r"\n{2,}", "\n", out).strip()


# ------------------------------------------------------------------ 给 LLM 的红线提示词
def build_guidance() -> str:
    """生成给二次改写 LLM 的违禁词红线说明（含替换建议）。"""
    lines = ["【违禁词红线——出现即可能被限流/封号，改写时严禁使用，并用合规表述替代】"]
    groups: dict[str, list[dict]] = {}
    for e in FORBIDDEN:
        cat = _cat_of(e)
        groups.setdefault(cat, []).append(e)
    for cat in ["极限词", "财税敏感", "诱导导流", "收益承诺", "时政敏感", "医疗功效"]:
        items = groups.get(cat)
        if not items:
            continue
        words = "、".join(e["word"] for e in items)
        suggests = "；".join(f"{e['word']}→{e['suggest']}" for e in items if e.get("suggest"))
        block = f"{CATEGORY_TITLE[cat]}\n   禁用词：{words}"
        if suggests:
            block += f"\n   替换建议：{suggests}"
        lines.append(block)
    lines.append("改写时逐句自查：不出现上述任何违禁词；如需表达类似意思，用建议的合规替代表述。")
    return "\n".join(lines)


def _cat_of(e: dict) -> str:
    w = e["word"]
    if w in ("治愈", "根治"):
        return "医疗功效"
    if w in ("稳赚", "稳赚不赔", "无风险", "保本", "高收益", "躺赚", "guaranteed"):
        return "收益承诺"
    if w in ("领导人", "涉军", "涉密"):
        return "时政敏感"
    if w in ("加微信", "微信号", "威信", "V信", "加V", "vx", "扫码", "扫一扫", "二维码",
             "留电话", "留方式", "私信我", "加好友", "点击链接", "下载APP", "公众号搜"):
        return "诱导导流"
    if w in ("避税", "偷税", "逃税", "节税", "返税", "税收洼地", "包过", "保过",
             "必下款", "稳过", "内部渠道", "找关系", "搞定", "0元", "免费"):
        return "财税敏感"
    return "极限词"


# ------------------------------------------------------------------ 机械筛查
# 法条刑期白名单(2026-08-27): 广告法"最"字弱约束会误伤法律刑期引用(如"最高十年有期徒刑"),
# 财税口播常引法条 → 命中"最"且满足"最高/最重+N年"+法律语境时放行, 广告宣传场景照拦
_LAW_CTX = ("刑法", "有期徒刑", "徒刑", "罪", "罚金", "刑期", "拘役", "处罚", "判", "量刑", "刑")
_LAW_YEAR = re.compile(r"(?:[一二三四五六七八九十两\d]+年|\d+年)")

def _is_law_sentence_quota(match, text):
    """判断"最"字命中的是否为法律刑期引用(如'最高十年有期徒刑''最重可判X年')。"""
    s = match.start()
    tail = text[s:s + 10]
    if not tail.startswith(("最高", "最重")):
        return False
    if not _LAW_YEAR.search(tail[2:]):
        return False
    ctx = text[max(0, s - 30): s + 30]
    return any(k in ctx for k in _LAW_CTX)


def scan(text: str, platform: str | None = None) -> list[dict]:
    """扫描文本，返回命中列表。每条：word/level/platforms/suggest/note/context/pos/need_human"""
    hits: list[dict] = []
    for e in FORBIDDEN:
        if platform and e["platforms"] and platform not in e["platforms"]:
            continue
        w = e["word"]
        if e.get("exact", True):
            for m in re.finditer(re.escape(w), text):
                s, en = m.start(), m.end()
                ctx = text[max(0, s - 12): en + 12].replace("\n", " ")
                hits.append({**e, "context": ctx, "pos": s, "need_human": False})
        else:
            # 弱约束单字/词：仅提示，需人工核对语境
            for m in re.finditer(re.escape(w), text):
                # 法条刑期引用白名单(如"最高十年有期徒刑"): 放行, 不误报
                if w == "最" and _is_law_sentence_quota(m, text):
                    continue
                s, en = m.start(), m.end()
                ctx = text[max(0, s - 12): en + 12].replace("\n", " ")
                hits.append({**e, "context": ctx, "pos": s, "need_human": True})
    hits.sort(key=lambda h: (0 if h["level"] == "high" else 1, h["pos"]))
    return hits


def format_report(hits: list[dict], platform: str | None = None) -> str:
    if not hits:
        return "✅ 未发现违禁词风险（基于当前词库，仍建议人工通读）。"
    scope = f"（平台：{platform}）" if platform else "（全部平台）"
    out = [f"⚠️ 违禁词筛查报告 {scope}", "=" * 48]
    for h in hits:
        tag = "需人工核对" if h.get("need_human") else ("🔴 高危" if h["level"] == "high" else "🟡 中等")
        plats = "、".join(h["platforms"]) if h["platforms"] else "全平台"
        out.append(f"[{tag}] “{h['word']}”  命中平台：{plats}")
        out.append(f"   上下文：…{h['context']}…")
        out.append(f"   替换建议：{h.get('suggest','')}")
        out.append(f"   说明：{h.get('note','')}")
        out.append("")
    return "\n".join(out)


# ------------------------------------------------------------------ CLI
def main():
    import argparse
    ap = argparse.ArgumentParser(description="各大平台短视频违禁词筛查（财税口播专项）")
    ap.add_argument("target", help="要检查的文件路径，或直接传文本")
    ap.add_argument("--platform", choices=PLATFORMS, default=None, help="只检查某平台")
    args = ap.parse_args()

    p = Path(args.target)
    text = p.read_text(encoding="utf-8") if p.exists() and p.is_file() else args.target

    hits = scan(text, platform=args.platform)
    print(format_report(hits, platform=args.platform))
    # 高危命中则非零退出，便于接进流水线做门禁
    has_high = any(h["level"] == "high" and not h.get("need_human") for h in hits)
    sys.exit(1 if has_high else 0)


if __name__ == "__main__":
    main()
