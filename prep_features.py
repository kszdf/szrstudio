# -*- coding: utf-8 -*-
"""只跑特征抽取（切片+ASR 已在 prep_data.py 完成并落盘）。
复用 training_data/zhang/raw/*.wav + zhang_raw.list
"""
import os, subprocess

REPO = r'D:/heygem_data/gpt_sovits'
GPT_DIR = os.path.join(REPO, 'GPT_SoVITS')
EXP = 'zhang'
EXP_DIR = os.path.join(REPO, 'training_data', EXP)
RAW_DIR = os.path.join(EXP_DIR, 'raw')
PY = r'D:/python311/python.exe'
PRE = os.path.join(GPT_DIR, 'pretrained_models')
os.environ['CUDA_VISIBLE_DEVICES'] = '0'
list_path = os.path.join(EXP_DIR, f'{EXP}_raw.list')


def run_prep(script, extra):
    env = os.environ.copy()
    env['version'] = 'v2'
    env['is_half'] = 'True'
    env.update(extra)
    env['PYTHONPATH'] = GPT_DIR + os.pathsep + REPO + os.pathsep + os.environ.get('PYTHONPATH', '')
    print(f'[特征] 运行 {script}  cwd={GPT_DIR}')
    p = subprocess.Popen([PY, os.path.join(REPO, 'prep_runner.py'), os.path.join(GPT_DIR, script)], cwd=REPO, env=env)
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
    'pretrained_s2G': os.path.join(PRE, 'gsv-v2final-pretrained', 's2G2333k.pth'),
    's2config_path': os.path.join(GPT_DIR, 'configs', 's2.json'),
})
print('[完成] 数据准备就绪')
for f in ['2-name2text.txt', '6-name2semantic.tsv']:
    fp = os.path.join(EXP_DIR, f)
    print(f'  {f}:', 'OK' if os.path.exists(fp) else '缺失', f'({os.path.getsize(fp) if os.path.exists(fp) else 0}字节)')
for d in ['4-cnhubert', '5-wav32k']:
    dp = os.path.join(EXP_DIR, d)
    print(f'  {d}/:', 'OK' if os.path.isdir(dp) else '缺失', f'({len(os.listdir(dp)) if os.path.isdir(dp) else 0}项)')
