#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""finalize 乱码修复冒烟测试：合成视频 + 含 emoji 字幕，跑通并验证字幕烧录正常。"""
import subprocess, sys
from pathlib import Path
from PIL import Image

BASE = Path("D:/heygem_data/gpt_sovits")
TMP = BASE / "_tmp_pil"
TMP.mkdir(parents=True, exist_ok=True)
FF = "D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"

# 1) 合成 3s 纯色视频 720x1280
vid = TMP / "synth.mp4"
subprocess.run([FF, "-y", "-f", "lavfi", "-i", "color=c=0x1d4ed8:s=720x1280:d=3",
                "-pix_fmt", "yuv420p", str(vid)], check=True, capture_output=True)

# 2) 含 emoji + 中文的字幕（模拟真实财税二创稿）
ass = TMP / "test.ass"
ass.write_text(
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
    "Outline, Shadow, Bold, Alignment, MarginL, MarginR, MarginV\n"
    "Style: Default,SimHei,64,&H00FFFFFF,&H00000000,4,1,0,2,60,60,80\n\n"
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Dialogue: 0,0.30,2.70,Default,,0,0,0,,老板注意✅这3点🔥避免税务风险💡虚开发票❌\n",
    encoding="utf-8")

# 3) 跑 finalize（不带 --replace-audio / --no-intro 简化依赖）
out = TMP / "out_smoke.mp4"
r = subprocess.run([sys.executable, str(BASE / "finalize_v2_pil.py"),
                    "--video", str(vid), "--ass", str(ass),
                    "--out", str(out), "--no-intro"],
                   capture_output=True, text=True)
print("=== finalize returncode:", r.returncode)
print(r.stdout[-1500:])
if r.stderr.strip():
    print("STDERR:\n", r.stderr[-1500:])

if not out.exists():
    print("❌ 成品未生成")
    sys.exit(1)

# 4) 抽中间帧，统计字幕区高亮像素（白字/黑边）证明字幕烧上、未崩溃
fr = TMP / "check.png"
subprocess.run([FF, "-y", "-ss", "1.5", "-i", str(out), "-frames:v", "1", str(fr)],
               check=True, capture_output=True)
im = Image.open(fr).convert("RGB")
px = im.load()
W, H = im.size
white = black = 0
for y in range(int(H * 0.7), H, 1):
    for x in range(0, W, 3):
        R, G, B = px[x, y]
        if R > 200 and G > 200 and B > 200:
            white += 1
        elif R < 60 and G < 60 and B < 60:
            black += 1
print(f"✅ 成品已生成: {out} ({out.stat().st_size//1024} KB)")
print(f"字幕区白字像素={white}  黑边像素={black}  (均>0 说明中文/符号烧录正常、无 tofu 崩溃)")
