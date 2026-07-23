#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
短视频生产链 QC 质检脚本
逐项验证每条成品是否达到「正常短视频发布标准」，并确认配套素材包闭环。
运行: py310 python qc_check.py
"""
import subprocess, json, os, wave, sys
import numpy as np

FF = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin"
OUT = r"D:/heygem_data/output"
PKG = r"D:/heygem_data/gpt_sovits/qwen_out/batch1_pkg"

# 发布标准阈值
STD = {
    "w": 1080, "h": 1920,          # 竖屏 9:16
    "vcodec": "h264",
    "acodec": "aac",
    "dur_min": 7, "dur_max": 60,    # 知识口播合理时长
    "audio_streams": 1,             # 单音轨无双声
    "v_bitrate_min": 2000,          # 1080p 建议 >=2Mbps
    "xcorr_min": 0.85,              # 成品音轨=千问原音(无HEYGEM泄漏)
}

def ffprobe(p):
    r = subprocess.run([f"{FF}/ffprobe", "-v", "error", "-print_format", "json",
                        "-show_format", "-show_streams", p],
                       capture_output=True, text=True)
    return json.loads(r.stdout)

def rd_wav(p):
    w = wave.open(p, "rb"); n = w.getnframes()
    return np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64)

def xcorr_peak(a, b):
    m = min(len(a), len(b)); a, b = a[:m], b[:m]
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0: return 0.0
    return np.correlate(a, b, "full").max() / (na * nb)

def extract_audio(p, dst):
    subprocess.run([f"{FF}/ffmpeg", "-y", "-i", p, "-vn", "-ac", "1", "-ar", "22050", dst],
                   capture_output=True)
    return dst

def loudness(p):
    """粗略响度:RMS(归一化到-数据库) + 峰值, 用 ffmpeg volumedetect"""
    r = subprocess.run([f"{FF}/ffmpeg", "-i", p, "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True)
    out = r.stderr
    mean = peak = None
    for line in out.splitlines():
        if "mean_volume" in line: mean = float(line.split(":")[1].strip().split(" ")[0])
        if "max_volume" in line: peak = float(line.split(":")[1].strip().split(" ")[0])
    return mean, peak

rows = []
for d in ["001","002","003","004","005"]:
    vp = f"{OUT}/avatar_{d}.mp4"
    ap = f"{PKG}/{d}/audio.wav"
    sp = f"{PKG}/{d}/subtitle.ass"
    pp = f"{PKG}/{d}/publish.md"
    cp = f"{PKG}/{d}/cover_upload_here.txt"
    sc = f"{PKG}/{d}/script.md"
    row = {"id": d}
    if not os.path.exists(vp):
        row["存在"] = "❌缺失"; rows.append(row); continue
    row["存在"] = "✅"
    info = ffprobe(vp)
    fmt = info["format"]
    v = next(s for s in info["streams"] if s["codec_type"]=="video")
    auds = [s for s in info["streams"] if s["codec_type"]=="audio"]
    W, H = int(v["width"]), int(v["height"])
    dur = float(fmt["duration"])
    vb = int(fmt.get("bit_rate",0))//1000
    row["分辨率"] = f"{W}x{H}" + (" ✅" if (W,H)==(STD["w"],STD["h"]) else " ❌非标")
    row["编码"] = f"{v['codec_name']}/{auds[0]['codec_name'] if auds else '无'}" + \
                  (" ✅" if v["codec_name"]==STD["vcodec"] and auds and auds[0]["codec_name"]==STD["acodec"] else " ❌")
    row["时长"] = f"{dur:.1f}s" + (" ✅" if STD["dur_min"]<=dur<=STD["dur_max"] else " ❌")
    row["音轨数"] = f"{len(auds)}" + (" ✅" if len(auds)==STD["audio_streams"] else " ❌双声/缺音")
    row["视频码率"] = f"{vb}k" + (" ✅" if vb>=STD["v_bitrate_min"] else " ⚠️偏低")
    # 音频身份
    if os.path.exists(ap):
        tmp = f"{OUT}/_qc_{d}.wav"
        extract_audio(vp, tmp)
        if os.path.exists(tmp):
            xc = xcorr_peak(rd_wav(ap), rd_wav(tmp))
            row["音频身份"] = f"xcorr={xc:.2f}" + (" ✅=千问原音" if xc>=STD["xcorr_min"] else " ❌泄漏/不符")
            os.remove(tmp)
    else:
        row["音频身份"] = "❌无源音频"
    # 响度
    mean, peak = loudness(vp)
    if mean is not None:
        row["响度"] = f"mean={mean:.1f}dB peak={peak:.1f}dB" + (" ⚠️偏轻" if mean < -22 else " ✅")
    # 配套包
    row["字幕"] = "✅" if os.path.exists(sp) and os.path.getsize(sp)>0 else "❌"
    row["发布文案"] = "✅" if os.path.exists(pp) and os.path.getsize(pp)>0 else "❌"
    row["封面位"] = "✅" if os.path.exists(cp) else "❌"
    row["逐字稿"] = "✅" if os.path.exists(sc) and os.path.getsize(sc)>0 else "❌"
    rows.append(row)

# 打印
keys = ["id","存在","分辨率","编码","时长","音轨数","视频码率","音频身份","响度","字幕","发布文案","封面位","逐字稿"]
print("="*120)
print("短视频生产链 QC 质检报告  ", "生成时间:", subprocess.run(["date","+%H:%M:%S"],capture_output=True,text=True).stdout.strip())
print("="*120)
for row in rows:
    print(f"\n--- 视频 {row.get('id','?')} ---")
    for k in keys[1:]:
        if k in row:
            print(f"  {k:8s}: {row[k]}")
print("\n" + "="*120)
# 汇总判定
def all_pass():
    for row in rows:
        if row.get("存在")!="✅": return False
        for k in ["分辨率","编码","时长","音轨数","音频身份","字幕","发布文案","封面位","逐字稿"]:
            if k in row and "❌" in str(row[k]): return False
    return True
print("整体判定:", "✅ 全部通过(达到发布标准)" if all_pass() else "⚠️ 存在未达标项(见上)")
print("="*120)
