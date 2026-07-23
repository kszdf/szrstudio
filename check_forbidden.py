#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
违禁词筛查 CLI（友好入口）
======================
用法：
  python check_forbidden.py <文件或文本> [--platform 抖音]

退出码：发现"高危且确定命中"的违禁词 -> 1（可接进流水线做门禁）；否则 0。
词库与逻辑见 forbidden_words.py。
"""
import sys
from forbidden_words import main

if __name__ == "__main__":
    main()
