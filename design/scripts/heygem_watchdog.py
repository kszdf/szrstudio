#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HEYGEM 守护脚本（看门狗 / Watchdog）
====================================
作用（大白话）：
    每隔一段时间检查「数字人出片服务 HEYGEM」是不是还活着。
    - 容器没跑  -> 自动拉起来
    - 容器在跑但端口不通（服务卡死）-> 自动重启容器
    - 都正常    -> 什么都不做，记一笔"正常"
    全程写日志，不用人半夜爬起来救。

设计：
    本脚本是「单次检查」模式，跑完就退出。
    由 Windows 任务计划程序 每 1~2 分钟 调用一次（见同目录 HEYGEM自愈方案.md）。
    只用到 Python 标准库，无需安装任何第三方包。
"""

import subprocess
import socket
import time
import os

# ========== 可改的配置 ==========
CONTAINER = "heygem-gen-video"   # Docker 容器名（用 docker ps 能看到）
HOST = "127.0.0.1"
PORT = 8383                      # HEYGEM 出片服务端口
START_WAIT = 8                   # 启动后等待秒数
RESTART_WAIT = 10                # 重启后等待秒数
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "heygem_watchdog.log")
# =================================


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def docker(*args):
    """调用 docker 命令，返回 (returncode, stdout, stderr)。"""
    try:
        r = subprocess.run(["docker"] + list(args),
                           capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return 1, "", "docker 命令不存在（Docker Desktop 可能没装/没启动）"
    except subprocess.TimeoutExpired:
        return 1, "", "docker 命令超时"


def is_container_running():
    rc, out, _ = docker("inspect", "-f", "{{.State.Running}}", CONTAINER)
    return rc == 0 and out == "true"


def port_alive():
    try:
        with socket.create_connection((HOST, PORT), timeout=3):
            return True
    except Exception:
        return False


def mem_usage():
    """顺手记一下内存占用，方便判断是不是「撑爆了」(OOM)。"""
    rc, out, _ = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", CONTAINER)
    return out if rc == 0 else "n/a"


def main():
    log("== 守护检查开始 ==")

    # 第一层：容器是否在运行
    if not is_container_running():
        log(f"⚠ 容器 [{CONTAINER}] 未运行，尝试启动...")
        docker("start", CONTAINER)
        time.sleep(START_WAIT)
        if is_container_running():
            log("✅ 已自动启动容器")
        else:
            log("❌ 启动失败：请确认 Docker Desktop 正在运行、且已登录")
            return

    # 第二层：容器在跑，但出片服务端口通不通（防「假活」卡死）
    if not port_alive():
        log(f"⚠ 容器在跑，但端口 {PORT} 无响应，重启容器以恢复服务...")
        docker("restart", CONTAINER)
        time.sleep(RESTART_WAIT)
        if port_alive():
            log("✅ 重启后端口已恢复，HEYGEM 可用")
        else:
            log("❌ 重启后仍无响应，可能需人工检查（多半是内存不足 OOM）")
            log(f"   崩溃前内存占用: {mem_usage()}")
    else:
        log(f"✅ HEYGEM 正常（端口 {PORT} 可达）")

    log("-- 守护检查结束 --\n")


if __name__ == "__main__":
    main()
