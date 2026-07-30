# -*- coding: utf-8 -*-
"""
Storage 抽象层（阶段0 地基）
============================
对应《后端多租户存储设计草案》：统一虚拟 Key，物理落盘由实现决定。
阶段2多租户、阶段3上云对象存储都依赖此接口，故在此先行落地，业务代码后续零改切换。

Key 规则（与草案一致）：
  ws_{id}/<kind>/YYYY/MM/DD/{uuid}_{safe_name}.<ext>
  - id   : workspace_id（ws_id 前12位，全局唯一）
  - kind : video | audio | pkg | thumb
  - safe_name : 用户文件名做安全化（见 _safe_name）

扩展点（后续阶段只补实现，不破契约）：
  - 阶段3 上云：新增 S3Storage/OSSStorage(Storage)，save/get_url 改走对象存储，
    list/delete/exists 同样实现即可，业务层无感切换。
  - 阶段2 多租户：workspace_id 由请求上下文注入，本层不关心来源。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional
import re
import uuid


def _safe_name(name: str) -> str:
    """用户文件名安全化：去掉路径分隔与特殊字符，保留中文/字母/数字/._-。"""
    name = (name or "").strip()
    name = re.sub(r"[\\/:*?\"<>|]", "_", name)
    name = re.sub(r"\s+", "_", name)
    name = name[:120]  # 防超长
    return name or "untitled"


class Storage(ABC):
    """存储抽象。所有方法接受虚拟 key（不含前导/），返回可访问URL或本地路径。"""

    @abstractmethod
    def save(self, key: str, data: bytes) -> str:
        """落地数据，返回可访问 URL（本地 file/http，云 oss/s3）。"""

    @abstractmethod
    def get_url(self, key: str) -> str:
        """返回可访问 URL（用于前端 <video src> / <a href>）。"""

    @abstractmethod
    def read(self, key: str) -> bytes:
        """读取字节内容。"""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """删除，成功返回 True。"""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """是否存在。"""

    @abstractmethod
    def list(self, prefix: str = "") -> list[str]:
        """列举某前缀下的 key 列表。"""

    # —— 工具方法（非抽象，子类可复用）——
    def build_key(self, ws_id: str, kind: str, name: str,
                  date: Optional[datetime] = None, ext: str = "") -> str:
        """构造标准化虚拟 key。"""
        d = date or datetime.now()
        safe = _safe_name(name)
        if ext and not safe.lower().endswith("." + ext.lower().lstrip(".")):
            safe = f"{safe}.{ext.lstrip('.')}"
        uid = uuid.uuid4().hex[:8]
        return f"ws_{ws_id}/{kind}/{d:%Y/%m/%d}/{uid}_{safe}"


class LocalStorage(Storage):
    """本地磁盘实现：物理根目录 + 虚拟 key 直接映射为相对子路径。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # 防穿越：key 内禁止 ..，且必须以 ws_ 开头
        if not key or ".." in key or not key.startswith("ws_"):
            raise ValueError(f"invalid key: {key!r}")
        return self.root / key

    def save(self, key: str, data: bytes) -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return self.get_url(key)

    def get_url(self, key: str) -> str:
        # 本地实现返回相对 http 路径（由上层 web 服务映射 /files/ 前缀）
        return f"/files/{key}"

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> bool:
        p = self._path(key)
        if p.exists():
            p.unlink()
            return True
        return False

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def list(self, prefix: str = "") -> list[str]:
        base = self.root / prefix if prefix else self.root
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)).replace("\\", "/")
                      for p in base.rglob("*") if p.is_file())


__all__ = ["Storage", "LocalStorage", "LocalStorage as StorageBackend", "_safe_name"]
