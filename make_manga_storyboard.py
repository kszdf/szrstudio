# -*- coding: utf-8 -*-
"""AI 漫剧分镜生成器: 内容 → 类型判断 → LLM 分镜(场景式/讲解式) → 分镜JSON。

成熟度把关:
  - 场景剧情类 → 场景式漫剧(角色演绎)
  - 流程/概念类 → 讲解式(角色讲解, 可配图示)
  - 法条政策类 → 标记 form=lecture(口播, 不漫剧化, 由上游改走幕后音/数字人)
"""
import json
import os
import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\heygem_data\gpt_sovits")
from model_providers import get_text_config, deepseek_chat

ROLE = ("Q版卡通形象，一位中年中国男老板，深蓝色西装，红色领带，微胖圆脸，黑色短发，"
        "大圆眼睛，儿童绘本插画风格，线条清晰圆润，色彩明快，无文字无水印")


def classify(text):
    """规则判断内容类型(不靠LLM猜, 稳定可控)。"""
    import re
    # 法条类: 用正则精确匹配「第X条」等法条特征词, 避免裸"第"字误伤"第一步/第二步"
    lecture_pat = [
        r"第\s*[一二三四五六七八九十百0-9]+\s*条",  # 第二百零五条 / 第3条
        r"刑法", r"罚金", r"有期徒刑", r"拘役", r"规定", r"依照", r"依据",
        r"政策", r"条例", r"办法", r"通知", r"免征", r"减征", r"税率",
    ]
    scene_kw = ["被查", "稽查", "翻车", "被抓", "判刑", "罚款", "出事", "踩坑", "案例", "老板栽了",
                "真实案例", "结局", "完了", "惊", "吓", "查到了", "被盯上"]
    flow_kw = ["步骤", "流程", "怎么", "如何", "三步", "四步", "操作", "路径", "顺序", "先", "然后", "最后",
               "第一步", "第二步", "第三步", "第四步", "第五步"]
    if sum(1 for p in lecture_pat if re.search(p, text)) >= 2:
        return "lecture"
    if any(k in text for k in scene_kw):
        return "scene"
    if any(k in text for k in flow_kw):
        return "explain"
    return "explain"


SCENE_TEMPLATE = """你是财税漫剧分镜导演。把下面的财税故事改写成「场景剧」分镜(角色演绎剧情, 有起承转合)。
角色固定: {role}。要求:
- 3~5 幕, 每幕 = 画面描述(含角色+场景+情绪) + 旁白(口语化, 一~二句, 讲清这幕要点)
- 画面描述要具体可生图: 场景(办公室/税局/家里…)、角色动作、表情、环境细节
- 情绪: 正常/疑惑/惊恐/无奈/开心 选一
- 每幕额外输出画面信息层: tag=顶部知识点标签(≤8字, 如"公转私风险")、card=中部信息卡主文字(≤18字, 该幕核心要点)、num=关键数字或词(≤8字, 如"补税2万", 无则空串)
- 只输出 JSON 数组: [{{"emotion":"...","shot":"画面描述(中文)","narration":"旁白","tag":"...","card":"...","num":"..."}}]
内容: {text}"""

EXPLAIN_TEMPLATE = """你是财税科普漫剧分镜导演。把下面的财税知识点改写成「讲解式」分镜(角色像老师一样讲解, 配图示)。
角色固定: {role}。要求:
- 3~5 幕, 每幕 = 画面描述 + 旁白
- 讲解式: 角色面对观众讲解, 可配黑板/图示/箭头/卡片等视觉元素, 表情随内容(轻松→严肃→肯定)
- 旁白口语化, 讲清逻辑(是什么→为什么→怎么办)
- 额外输出 steps: 整个知识点的核心步骤清单(2~5 条, 每条≤10字), 供画面逐步展示
- 每幕额外输出画面信息层: tag=顶部知识点标签(≤8字, 如"第一步：账结清")、card=中部信息卡主文字(≤18字, 该幕讲解要点)、num=关键数字或词(≤8字, 如"45天", 无则空串)
- 只输出 JSON 对象: {{"steps": ["第1步: 账结清", ...], "shots": [{{"emotion":"...","shot":"画面描述(中文)","narration":"旁白","tag":"...","card":"...","num":"..."}}]}}
内容: {text}"""


def generate(text):
    ctype = classify(text)
    if ctype == "lecture":
        return {"form": "lecture", "reason": "法条/政策类: 保持口播精确呈现, 不漫剧化", "shots": [], "steps": []}
    prompt = (SCENE_TEMPLATE if ctype == "scene" else EXPLAIN_TEMPLATE).format(role=ROLE, text=text)
    cfg = get_text_config()
    raw = deepseek_chat(prompt, cfg["model"], cfg["key"], cfg.get("base_url"), timeout=90)
    content = (raw or "").strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content[:4].lower() == "json":
            content = content[4:]
    data = json.loads(content) if (content.startswith("[") or content.startswith("{")) else None
    if isinstance(data, list):
        arr = data
        steps = []
    elif isinstance(data, dict):
        arr = data.get("shots") or []
        steps = data.get("steps") or []
    else:
        raise ValueError(f"分镜解析失败: {content[:200]}")
    shots = [{"emotion": s.get("emotion", "normal"), "shot": s.get("shot", ""),
              "narration": s.get("narration", ""),
              "tag": s.get("tag", ""), "card": s.get("card", ""), "num": s.get("num", "")}
             for s in arr if s.get("shot")]
    return {"form": "scene" if ctype == "scene" else "explain",
            "reason": "场景剧情类→场景剧" if ctype == "scene" else "流程/概念类→讲解式",
            "shots": shots, "steps": steps}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("用法: make_manga_storyboard.py \"内容\"")
    r = generate(sys.argv[1])
    print(json.dumps(r, ensure_ascii=False, indent=2))
