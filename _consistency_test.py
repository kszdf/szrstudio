# -*- coding: utf-8 -*-
"""角色一致性测试: 固定角色描述模板, 生成 3 个分镜看是否同一人。"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\heygem_data\gpt_sovits")
from model_providers import ensure_env
ensure_env()
from pathlib import Path
import urllib.request
from dashscope import ImageSynthesis

OUT = Path(r"D:\heygem_data\gpt_sovits\cartoon_assets")
OUT.mkdir(parents=True, exist_ok=True)

# 固定角色描述（每个分镜完全相同, 保证一致性）
ROLE = ("Q版卡通卡通形象，一位中年中国男老板，深蓝色西装，红色领带，微胖圆脸，黑色短发，"
        "大圆眼睛，儿童绘本插画风格，线条清晰圆润，色彩明快，无文字无水印")

SHOTS = [
    ("分镜1_基准", ROLE + "，站在白色背景前，双手叉腰，微笑，全身像，竖版构图"),
    ("分镜2_办公室", ROLE + "，坐在办公桌前，桌上放着账本和计算器，神情严肃，半身像，竖版构图"),
    ("分镜3_惊吓", ROLE + "，看到账单惊讶地张大嘴，额头冒汗，夸张表情，白色背景，半身像，竖版构图"),
]

def gen(name, prompt):
    rsp = ImageSynthesis.call(model="wanx2.1-t2i-turbo", prompt=prompt,
                              size="720*1280", n=1, api_key=os.environ.get("DASHSCOPE_API_KEY"))
    if rsp.status_code != 200:
        print(f"{name} 失败: {rsp.message}")
        return None
    url = rsp.output.results[0].url
    dest = OUT / f"{name}.jpg"
    urllib.request.urlretrieve(url, dest)
    print(f"{name}: 完成 -> {dest.name}")
    return dest

for name, prompt in SHOTS:
    gen(name, prompt)

print("\n3 张分镜已生成, 请看:", OUT)
