import sys, os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from model_providers import ensure_env
ensure_env()
from qwen_tts import synth
from make_scroll_video import EMOTION_PROSODY, annotate_emotions

MALE_VOICE = "cosyvoice-v3-plus-zhangc2-28a7c3541e1c45518a03046c11baeb1d"
MODEL = "cosyvoice-v3-plus"
FFMPEG = "ffmpeg"
SAMPLE = os.path.join(HERE, "_samples")
os.makedirs(SAMPLE, exist_ok=True)

# 一段含法规条文 + 重大风险定性句的财税口播（男声独白）
segs = [
    ("M", "老板们常问，甲方代付的材料款，到底能不能直接入账？"),
    ("M", "根据《中华人民共和国税收征收管理法》相关规定，虚构交易、套取资金的行为，属于违法。"),
    ("M", "一旦被认定为偷逃税款，不仅要补缴税款，还可能面临滞纳金和高额罚款。"),
    ("M", "所以正确做法，是做到三流合一，合同流、资金流、发票流保持一致。"),
    ("M", "这关把住了，税务风险就降了一大半。"),
]

print("=== 验证2: DeepSeek 自动情绪标注 ===")
emos = annotate_emotions(segs)
print("标注结果:", emos)
if "solemn" in emos:
    emos_B = emos
    print("[OK] 标注器已自动对法规/风险句标 solemn")
else:
    print("[WARN] 标注器未自动标 solemn，演示强制对句1/2用 solemn 以展示效果")
    emos_B = ["query", "solemn", "solemn", "emphasis", "ending"]

# A 组：旧行为（法规/风险句用 narrate，无慢速档）
emos_A = ["query", "narrate", "narrate", "emphasis", "ending"]

MALE_RATE, MALE_PITCH, MALE_VOL = 1.0, 0.95, 53


def synth_seq(tag, emo_list):
    wavs = []
    for i, (r, t) in enumerate(segs):
        prof = EMOTION_PROSODY[emo_list[i]]
        rel_sr, rel_pr, rel_vol = prof["rel"]
        sr = round(MALE_RATE * rel_sr, 3)
        pr = round(MALE_PITCH * rel_pr, 3)
        vol = int(round(MALE_VOL * rel_vol))
        out = os.path.join(SAMPLE, f"solemn_{tag}_{i}.wav")
        synth(t, MALE_VOICE, out, model=MODEL, speech_rate=sr, pitch_rate=pr, volume=vol)
        print(f"  [{tag}] 句{i} emo={emo_list[i]:8s} sr={sr} pr={pr} vol={vol:2d} pause={prof['pause']}")
        wavs.append(out)
    return wavs


def concat_with_pause(tag, wavs, emo_list):
    parts = []
    for i, w in enumerate(wavs):
        parts.append(w)
        if i < len(wavs) - 1:
            p = EMOTION_PROSODY[emo_list[i]]["pause"]
            gp = os.path.join(SAMPLE, f"solemn_{tag}_gap_{i}.wav")
            subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                            "-t", f"{p:.3f}", "-c:a", "pcm_s16le", gp],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            parts.append(gp)
    listf = os.path.join(SAMPLE, f"solemn_{tag}_list.txt")
    with open(listf, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p}'\n")
    out = os.path.join(SAMPLE, f"solemn_demo_{tag}.wav")
    rc = subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", listf,
                         "-c:a", "pcm_s16le", out],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return out if rc.returncode == 0 else None


print("=== 合成 A 组(旧:无慢速档, 法规句 narrate) ===")
wavs_A = synth_seq("A", emos_A)
print("=== 合成 B 组(新:含 solemn 慢速档) ===")
wavs_B = synth_seq("B", emos_B)

outA = concat_with_pause("A", wavs_A, emos_A)
outB = concat_with_pause("B", wavs_B, emos_B)
print("=== 完成 ===")
print("A(旧 无慢速):", outA)
print("B(新 含solemn):", outB)
for i in (1, 2):
    na = EMOTION_PROSODY[emos_A[i]]["rel"][0]
    nb = EMOTION_PROSODY[emos_B[i]]["rel"][0]
    pa = EMOTION_PROSODY[emos_A[i]]["pause"]
    pb = EMOTION_PROSODY[emos_B[i]]["pause"]
    print(f"  句{i} 语速倍率 {na}->;{nb} (慢{(1-nb/na)*100:.0f}%)  停顿 {pa}s->;{pb}s")
