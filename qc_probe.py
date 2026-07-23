#!/usr/bin/env python3
import subprocess, json, os
FF=r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin"
OUT=r"D:/heygem_data/output"
print("="*96); print("纯 ffprobe 瞬时 QC (硬指标红线)"); print("="*96)
allok=True
for d in ["001","002","003","004","005"]:
    vp=f"{OUT}/avatar_{d}.mp4"
    if not os.path.exists(vp):
        print(f"[{d}] ❌缺失"); allok=False; continue
    r=subprocess.run([f"{FF}/ffprobe","-v","error","-print_format","json",
                      "-show_format","-show_streams",vp],capture_output=True,text=True)
    info=json.loads(r.stdout); fmt=info["format"]
    v=next(s for s in info["streams"] if s["codec_type"]=="video")
    auds=[s for s in info["streams"] if s["codec_type"]=="audio"]
    W,H=int(v["width"]),int(v["height"]); dur=float(fmt["duration"])
    vb=int(fmt.get("bit_rate",0))//1000
    res_ok=(W,H)==(1080,1920); enc_ok=v["codec_name"]=="h264" and auds and auds[0]["codec_name"]=="aac"
    dur_ok=7<=dur<=60; a_ok=len(auds)==1; vb_ok=vb>=2000
    if not(res_ok and enc_ok and dur_ok and a_ok): allok=False
    print(f"\n[{d}] {'✅' if (res_ok and enc_ok and dur_ok and a_ok) else '⚠️需修'}")
    print(f"  分辨率 {W}x{H} {'✅' if res_ok else '❌非标'} | 编码 {v['codec_name']}/{auds[0]['codec_name'] if auds else '无'} {'✅' if enc_ok else '❌'}")
    print(f"  时长 {dur:.1f}s {'✅' if dur_ok else '❌'} | 音轨 {len(auds)} {'✅' if a_ok else '❌双声/缺'} | 码率 {vb}k {'✅' if vb_ok else '⚠️偏低(<2M)'}")
print("\n"+"="*96)
print("硬指标:", "✅ 5条全部达标" if allok else "⚠️ 有项未达标(见上)")
print("="*96)
