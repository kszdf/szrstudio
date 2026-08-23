# -*- coding: utf-8 -*-
"""行业包加载入口：按行业名加载对应 profile。
用法：
    from industry import load_profile
    p = load_profile("finance_tax")   # -> PROFILE dict
未来新增行业 = 新建 industry/xxx/profile.py，无需改主流程代码。
"""
import importlib


def load_profile(name: str) -> dict:
    """按行业目录名加载 PROFILE。"""
    mod = importlib.import_module(f"industry.{name}.profile")
    return getattr(mod, "PROFILE", {})
