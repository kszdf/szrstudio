# 用张老师自录 YP20260721 的 6秒干净切片作参考，prompt_text 与切片时长严格对齐，稳定参数合成
import socket, subprocess, time, requests, sys, os

ROOT = 'D:/heygem_data/gpt_sovits'
PY = 'D:/python311/python.exe'
API = 'http://127.0.0.1:9880'
# 6秒干净切片（从27秒自录中切出，开头连续朗读段）
REF = os.path.join(ROOT, 'ref_yp_6s.wav')
# prompt_text 严格对应 6秒切片里实际念的内容（前28字，时长匹配，避免文字比声音长导致啰嗦）
PROMPT_TEXT = '在中华人民共和国境内销售货物、服务、无形资产、不动产以及进口货物'
# 测试文本：3句，验证多句拼接连贯性（用户核心痛点）
TEST_TEXT = ('老板们注意了，公转私超过二十万，税务系统就会预警。'
             '发票风险绝对不能碰，一旦被查补税罚款少不了。'
             '今天老张就把这几个坑，给大家讲透。')
OUT = os.path.join(ROOT, 'output_yp_multi.wav')


def port_open():
    s = socket.socket(); s.settimeout(1)
    try:
        s.connect(('127.0.0.1', 9880)); return True
    except Exception:
        return False
    finally:
        s.close()


if not port_open():
    print('[启动] api_v2 未运行，拉起...')
    subprocess.Popen([PY, 'api_v2.py', '-a', '127.0.0.1', '-p', '9880',
                      '-c', 'GPT_SoVITS/configs/tts_infer.yaml'], cwd=ROOT)
    for i in range(180):
        if port_open():
            print(f'[ok] api 就绪 ({i}s)'); break
        time.sleep(1)
    else:
        print('[fail] api 启动失败'); sys.exit(1)
else:
    print('[ok] api 已在运行')

r = requests.post(API + '/tts', json={
    'text': TEST_TEXT,
    'text_lang': 'zh',
    'ref_audio_path': REF,
    'prompt_text': PROMPT_TEXT,
    'prompt_lang': 'zh',
    'temperature': 0.5,
    'top_k': 8,
    'top_p': 0.85,
    'speed_factor': 1.0,
    'repetition_penalty': 1.5,
    'text_split_method': 'cut5',
    'media_type': 'wav',
}, timeout=180)

if r.status_code == 200:
    with open(OUT, 'wb') as f:
        f.write(r.content)
    print(f'[ok] 合成成功: {OUT} ({len(r.content)} bytes)')
else:
    print(f'[fail] 状态码 {r.status_code}: {r.text[:400]}')
