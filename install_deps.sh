#!/bin/bash
# GPT-SoVITS 依赖安装（RTX 5060 / Blackwell 需 torch cu128）
# 直接用 embeddable python3.11 (D:/python311)，独立目录即隔离环境
set -e
PY=D:/python311/python.exe
cd D:/heygem_data/gpt_sovits

echo "=== 1) 升级 pip ==="
$PY -m pip install --upgrade pip -q

echo "=== 2) 安装 torch cu128 (Blackwell sm_120) ==="
$PY -m pip install torch==2.7.0 torchaudio==2.7.0 --index-url https://download.pytorch.org/whl/cu128

echo "=== 3) 固定 requirements 的 torchaudio 版本(避免 pip 拉新版 torch 覆盖 cu128) ==="
$PY - <<'PYEOF'
p='requirements.txt'
s=open(p,encoding='utf-8').read()
if '\ntorchaudio\n' in s:
    s=s.replace('\ntorchaudio\n','\ntorchaudio==2.7.0\n')
open(p,'w',encoding='utf-8').write(s)
print('patched requirements: torchaudio pinned')
PYEOF

echo "=== 4) 安装其余依赖(funasr/transformers/librosa 等,可能较慢) ==="
$PY -m pip install -r requirements.txt

echo "=== 5) 验证 torch cuda ==="
$PY -c "import torch; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'avail', torch.cuda.is_available())"

echo "=== DONE ==="
