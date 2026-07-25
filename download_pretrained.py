# 用 ModelScope(AI-ModelScope/GPT-SoVITS) 国内源拉取 GPT-SoVITS v2final 预训练权重
# 文件落在 GPT_SoVITS/pretrained_models/ 下，正好匹配 tts_infer.yaml 的路径
from modelscope.hub.snapshot_download import snapshot_download

LOCAL = 'D:/heygem_data/gpt_sovits/GPT_SoVITS/pretrained_models'
PATTERNS = [
    'chinese-hubert-base/*',               # HuBERT 音色编码器
    'chinese-roberta-wwm-ext-large/*',     # BERT 中文拼音前端
    'gsv-v2final-pretrained/*',            # v2final GPT + SoVITS 权重
]

print('=== 开始下载 GPT-SoVITS v2final 预训练权重 ===', flush=True)
snapshot_download(
    'AI-ModelScope/GPT-SoVITS',
    allow_file_pattern=PATTERNS,
    local_dir=LOCAL,
)
print('=== ALL DONE ===', flush=True)
