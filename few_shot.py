# GPT-SoVITS few-shot 推理测试：启动 api_v2 -> 用参考音频克隆音色 -> 合成测试音频
import subprocess, time, requests, sys, os

ROOT = 'D:/heygem_data/gpt_sovits'
PY = 'D:/python311/python.exe'  # embeddable python3.11
API = 'http://127.0.0.1:9880'
REF = 'D:/heygem_data/gpt_sovits/ref_zhang_best8s.wav'
# 参考音频实际念的内容(BGZSP20260721最干净8秒, ASR识别)，作为音色提示文本
PROMPT_TEXT = '5万以上现金存取对公转个人20万以上个人账户平凡大额收付这些都会'
OUT = 'D:/heygem_data/gpt_sovits/output_fewshot.wav'

# 待合成的新文本（内容与参考无关，只借用音色）
TEST_TEXT = '老板们注意了，发票有五类风险，今天老张给大家拆开讲清楚，别等税务找上门才后悔。'

def wait_api(timeout=180):
    for i in range(timeout):
        try:
            r = requests.get(API + '/docs', timeout=3)
            if r.status_code == 200:
                print(f'[ok] api 就绪 ({i}s)')
                return True
        except Exception:
            pass
        time.sleep(1)
    return False

if __name__ == '__main__':
    p = subprocess.Popen([PY, 'api_v2.py', '-a', '127.0.0.1', '-p', '9880',
                          '-c', 'GPT_SoVITS/configs/tts_infer.yaml'], cwd=ROOT)
    try:
        if not wait_api():
            print('[fail] api 未就绪，退出'); sys.exit(1)
        r = requests.post(API + '/tts', json={
            'text': TEST_TEXT,
            'text_lang': 'zh',
            'ref_audio_path': REF,
            'prompt_text': PROMPT_TEXT,
            'prompt_lang': 'zh',
            'temperature': 0.3,
            'top_k': 15,
            'top_p': 1.0,
            'speed_factor': 1.0,
            'media_type': 'wav',
        }, timeout=120)
        if r.status_code == 200:
            with open(OUT, 'wb') as f:
                f.write(r.content)
            print(f'[ok] 生成测试音频: {OUT} ({len(r.content)} bytes)')
        else:
            print(f'[fail] 状态码 {r.status_code}: {r.text[:300]}')
    finally:
        p.terminate()
