# 分片下载 GPT-SoVITS 大文件（绕过单文件限流/断流），用 modelscope API 取精确大小做校验
import urllib.request, os, sys, time
from modelscope.hub.api import HubApi

api = HubApi()
fs = api.get_model_files('AI-ModelScope/GPT-SoVITS')
size_map = {f['Path']: f['Size'] for f in fs}
BASE = 'https://modelscope.cn/models/AI-ModelScope/GPT-SoVITS/resolve/master/'
OUT = 'D:/heygem_data/gpt_sovits/GPT_SoVITS/pretrained_models/'
CHUNK = 100 * 1024 * 1024  # 每片 100MB，规避限流
FILES = [
    'gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt',
    'gsv-v2final-pretrained/s2G2333k.pth',
    'chinese-roberta-wwm-ext-large/pytorch_model.bin',
    'chinese-hubert-base/pytorch_model.bin',
    'models--nvidia--bigvgan_v2_24khz_100band_256x/bigvgan_generator.pt',
]

def fetch(url, start, end):
    req = urllib.request.Request(url, headers={
        'Range': f'bytes={start}-{end}',
        'User-Agent': 'Mozilla/5.0',
    })
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()

for rel in FILES:
    total = size_map.get(rel)
    if not total:
        print('SKIP(no size)', rel, flush=True); continue
    out = os.path.join(OUT, rel)
    if os.path.exists(out) and os.path.getsize(out) == total:
        print(f'OK skip {rel} ({total})', flush=True); continue
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print(f'=== download {rel} total={total} ===', flush=True)
    with open(out, 'wb') as fo:
        pos = 0
        while pos < total:
            end = min(pos + CHUNK - 1, total - 1)
            ok = False
            for att in range(10):
                try:
                    data = fetch(BASE + rel, pos, end)
                    if data:
                        fo.write(data); fo.flush(); ok = True; break
                except Exception as e:
                    print(f'  retry {pos}-{end} a{att}: {e}', flush=True)
                    time.sleep(2)
            if not ok:
                print(f'FAILED {rel} @ {pos}', flush=True); sys.exit(1)
            pos = end + 1
            print(f'  {pos*100//total}%', flush=True)
    print(f'DONE {rel} -> {os.path.getsize(out)}', flush=True)

print('=== ALL DONE ===', flush=True)
