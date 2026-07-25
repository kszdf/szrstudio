# -*- coding: utf-8 -*-
"""特征抽取脚本的统一启动器：先把 GPT_SoVITS 与仓库根注入 sys.path，
再用 runpy 以 __main__ 身份运行目标脚本（目标脚本靠 os.environ 读取参数）。
解决 prepare_datasets 子目录下 `from text.cleaner` / `from tools.my_utils` 找不到模块的问题。
"""
import sys, os, runpy

ROOT = r'D:/heygem_data/gpt_sovits'
GPT = os.path.join(ROOT, 'GPT_SoVITS')
for p in (GPT, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

target = sys.argv[1]
runpy.run_path(target, run_name='__main__')
