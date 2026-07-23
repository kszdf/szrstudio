#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""快速 QC: 仅 ffprobe + 抽音频做互相关, 不做 volumedetect 全解码(慢)"""
import subprocess, json, os, wave
import numpy as np

FF = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin"
OUT = r"D:/heygem_data/output"
PKG = r"D:/heygem_data/gpt_sovits/qwen_out/batch1_pkg"

def ffprobe(p):
    r = subprocess.run([f"{FF}/ffprobe","-v","error","-print_format","json",
                        "-show_format","-show_streams",p],capture_output=True,text=True)
    return json.loads(r.stdout)

def rd_wav(p):
    w=wave.open(p,"rb"); n=w.getnframes()
    return np.frombuffer(w.readframes(n),dtype=np.int16).astype(np.float64)

def xcorr(a,b):
    m=min(len(a),len(b)); a,b=a[:m],b[:m]
    na,nb=np.linalg.norm(a),np.linalg.norm(b)
    if na==0 or nb==0: return 0.0
    return np.correlate(a,b,"full").max()/(na*nb)

print("="*100)
print("快速 QC 报告 (硬指标)")
print("="*100)
allok=True
for d in ["001","002","003","004","005"]:
    vp=f"{OUT}/avatar_{d}.mp4"; ap=f"{PKG}/{d}/audio.wav"
    sp=f"{PKG}/{d}/subtitle.ass"; pp=f"{PKG}/{d}/publish.md"
    cp=f"{PKG}/{d}/cover_upload_here.txt"; sc=f"{PKG}/{d}/script.md"
    if not os.path.exists(vp):
        print(f"\n[{d}] ❌ 成品缺失"); allok=False; continue
    info=ffprobe(vp); fmt=info["format"]
    v=next(s for s in info["streams"] if s["codec_type"]=="video")
    auds=[s for s in info["streams"] if s["codec_type"]=="audio"]
    W,H=int(v["width"]),int(v["height"]); dur=float(fmt["duration"])
    vb=int(fmt.get("bit_rate",0))//1000
    res_ok=(W,H)==(1080,1920)
    enc_ok=v["codec_name"]=="h264" and auds and auds[0]["codec_name"]=="aac"
    dur_ok=7<=dur<=60
    a_ok=len(auds)==1
    # 音频身份
    xc=0.0; xc_ok=False
    if os.path.exists(ap):
        tmp=f"{OUT}/_qc{d}.wav"
        subprocess.run([f"{FF}/ffmpeg","-y","-i",vp,"-vn","-ac","1","-ar","22050",tmp],
                       capture_output=True)
        if os.path.exists(tmp):
            xc=xcorr(rd_wav(ap),rd_wav(tmp)); xc_ok=xc>=0.85; os.remove(tmp)
    pkg_ok=all(os.path.exists(x) and os.path.getsize(x)>0 for x in [sp,pp,sc]) and os.path.exists(cp)
    vid = (res_ok and enc_ok and dur_ok and a_ok and vb>=2000 and xc_ok and pkg_ok)
    if not (res_ok and enc_ok and dur_ok and a_ok and xc_ok and pkg_ok): allok=False
    print(f"\n[{d}] {'✅发布级' if vid else '⚠️需复核'}")
    print(f"  分辨率 {W}x{H} {'✅' if res_ok else '❌'} | 编码 {v['codec_name']}/{auds[0]['codec_name'] if auds else '无'} {'✅' if enc_ok else '❌'}")
    print(f"  时长 {dur:.1f}s {'✅' if dur_ok else '❌'} | 音轨 {len(auds)} {'✅' if a_ok else '❌'} | 码率 {vb}k {'✅' if vb>=2000 else '⚠️偏低'}")
    print(f"  音频身份 xcorr={xc:.2f} {'✅=千问原音' if xc_ok else '❌'}")
    print(f"  素材包 {'✅' if pkg_ok else '❌'} (字幕/文案/逐字稿/封面位)")
print("\n"+"="*100)
print("整体:", "✅ 5条全部达发布硬标准" if allok else "⚠️ 见上, 有项需复核/修复")
print("="*100)
