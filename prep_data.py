# -*- coding: utf-8 -*-
"""GPT-SoVITS 数据准备流水线（头部驱动版）
1) 切片干净音频 -> 3~8s 片段
2) funasr 逐段 ASR -> 生成 .list
3) 顺序运行 1-get-text / 2-get-hubert / 3-get-semantic 抽特征
数据落在 training_data/zhang/ 下，供 s2_train / s1_train 读取
"""
import os, sys, subprocess, wave, numpy as np

REPO = r'D:/heygem_data/gpt_sovits'
GPT_DIR = os.path.join(REPO, 'GPT_SoVITS')
EXP = 'zhang'
EXP_DIR = os.path.join(REPO, 'training_data', EXP)
RAW_DIR = os.path.join(EXP_DIR, 'raw')
os.makedirs(RAW_DIR, exist_ok=True)
PY = r'D:/python311/python.exe'
PRE = os.path.join(GPT_DIR, 'pretrained_models')
os.environ['CUDA_VISIBLE_DEVICES'] = '0'

# ---------- 1) 切片（官方 slicer2） ----------
os.chdir(GPT_DIR)
for p in (REPO, GPT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)
from tools.slicer2 import Slicer

SRC = os.path.join(REPO, 'ref_yp_raw.wav')  # 27s 纯净自录
w = wave.open(SRC, 'rb'); fs = w.getframerate(); n = w.getnframes()
raw = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0; w.close()
print(f'[切片] 源 {SRC} sr={fs} 时长={len(raw)/fs:.1f}s')

slicer = Slicer(sr=fs, threshold=-40, min_length=4000, min_interval=300, hop_size=20, max_sil_kept=4000)
segs = slicer.slice(raw)
print(f'[切片] 原始段数={len(segs)}')

seg_paths = []
for i, item in enumerate(segs):
    seg = item[0] if isinstance(item, (tuple, list)) else item
    seg = np.asarray(seg, dtype=np.float32)
    if len(seg) < fs * 3:  # 丢弃 <3s
        continue
    p = os.path.join(RAW_DIR, f'seg_{i:04d}.wav')
    with wave.open(p, 'wb') as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(fs)
        wf.writeframes((seg * 32768).clip(-32768, 32767).astype(np.int16).tobytes())
    seg_paths.append(p)
print(f'[切片] 有效片段={len(seg_paths)}')

# ---------- 2) ASR ----------
from funasr import AutoModel
asr = AutoModel(model='paraformer-zh', model_revision='v2.0.4', device='cuda:0')
lines = []
for p in seg_paths:
    ww = wave.open(p, 'rb'); fr = ww.getframerate(); d = ww.readframes(ww.getnframes()); ww.close()
    a = np.frombuffer(d, dtype=np.int16).astype(np.float32) / 32768
    r = asr.generate(input=a, fs=fr)
    txt = (r[0]['text'] if r else '').replace(' ', '').strip()
    if not txt:
        print(f'  [跳过空] {os.path.basename(p)}')
        continue
    lines.append(f'{os.path.basename(p)}|{EXP}|zh|{txt}')
    print(f'  {os.path.basename(p)}: {txt}')
list_path = os.path.join(EXP_DIR, f'{EXP}_raw.list')
with open(list_path, 'w', encoding='utf8') as f:
    f.write('\n'.join(lines) + '\n')
print(f'[ASR] 写入 {list_path} 共 {len(lines)} 条')

# ---------- 3) 抽特征 ----------
def run_prep(script, extra):
    env = os.environ.copy()
    env['version'] = 'v2'
    env['is_half'] = 'True'
    env.update(extra)
    env['PYTHONPATH'] = GPT_DIR + os.pathsep + os.environ.get('PYTHONPATH', '')
    print(f'[特征] 运行 {script}  cwd={GPT_DIR}')
    p = subprocess.Popen([PY, script], cwd=GPT_DIR, env=env)
    p.wait()
    if p.returncode != 0:
        raise RuntimeError(f'{script} 退出码 {p.returncode}')

run_prep('prepare_datasets/1-get-text.py', {
    'inp_text': list_path, 'inp_wav_dir': RAW_DIR, 'exp_name': EXP,
    'i_part': '0', 'all_parts': '1', 'opt_dir': EXP_DIR,
    'bert_pretrained_dir': os.path.join(PRE, 'chinese-roberta-wwm-ext-large'),
})
run_prep('prepare_datasets/2-get-hubert-wav32k.py', {
    'inp_text': list_path, 'inp_wav_dir': RAW_DIR, 'exp_name': EXP,
    'i_part': '0', 'all_parts': '1', 'opt_dir': EXP_DIR,
    'cnhubert_base_dir': os.path.join(PRE, 'chinese-hubert-base'),
})
run_prep('prepare_datasets/3-get-semantic.py', {
    'inp_text': list_path, 'exp_name': EXP,
    'i_part': '0', 'all_parts': '1', 'opt_dir': EXP_DIR,
})
print(f'[完成] 数据准备就绪 -> {EXP_DIR}')
for f in ['2-name2text.txt', '6-name2semantic.tsv']:
    fp = os.path.join(EXP_DIR, f)
    print(f'  {f}:', 'OK' if os.path.exists(fp) else '缺失', f'({os.path.getsize(fp) if os.path.exists(fp) else 0}字节)')
