# -*- coding: utf-8 -*-
"""生成 AI 漫剧分镜（固定角色 + 剧情场景, 4 幕: 公转私）。"""
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

ROLE = ("Q版卡通卡通形象，一位中年中国男老板，深蓝色西装，红色领带，微胖圆脸，黑色短发，"
        "大圆眼睛，儿童绘本插画风格，线条清晰圆润，色彩明快，无文字无水印")

SHOTS = [
    ("s1_公司账", ROLE + "，站在公司财务室，背后是文件柜和账本堆，双手抱胸微笑自信，全身像，竖版构图"),
    ("s2_个人卡", ROLE + "，手里拿着一张银行卡，神情严肃皱眉，白色背景，半身像，竖版构图"),
    ("s3_无限责任", ROLE + "，看到一份写着警示的文件，惊恐地瞪大眼张大嘴，额头冒汗，背景变暗红色调，半身像，竖版构图"),
    ("s4_合规", ROLE + "，开心地竖起大拇指，笑容灿烂，背景明亮阳光，半身像，竖版构图"),
]

for name, prompt in SHOTS:
    rsp = ImageSynthesis.call(model="wanx2.1-t2i-turbo", prompt=prompt,
                              size="720*1280", n=1, api_key=os.environ.get("DASHSCOPE_API_KEY"))
    if rsp.status_code != 200:
        print(f"{name} 失败: {rsp.message}")
        continue
    url = rsp.output.results[0].url
    dest = OUT / f"{name}.jpg"
    urllib.request.urlretrieve(url, dest)
    print(f"{name}: 完成 -> {dest.name}")
print("\n分镜已生成:", OUT)
