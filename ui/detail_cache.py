# -*- coding: utf-8 -*-
"""单局完整详情缓存。

历史比赛详情结束后基本不会变化，因此第一次完整抓取后同时保存在内存和磁盘；
再次打开同一玩家视角下的同一 match_id 时直接复用，除非以后显式加入“刷新详情”。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

_CACHE_VERSION = 1


def default_cache_root() -> Path:
    """单局详情磁盘缓存目录。缓存管理页复用同一个定义，不另拼路径。"""
    if os.name == "nt":
        root = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if root:
            return Path(root) / "OWDriveSplit" / "cache" / "match_details"
    xdg = os.getenv("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "ow_drive_split" / "match_details"
    return Path.home() / ".cache" / "ow_drive_split" / "match_details"


# 兼容原内部名称。
_default_root = default_cache_root


class MatchDetailCache:
    """以 ``customer_token + match_id`` 为键的两级缓存。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_cache_root()
        self._memory: Dict[str, Dict[str, Any]] = {}

    def clear_memory(self) -> None:
        """磁盘缓存被清理后同步丢弃内存缓存；已打开页面的数据不受影响。"""
        self._memory.clear()

    @staticmethod
    def _key(customer_token: str, match_id: str) -> str:
        raw = f"{customer_token.strip()}\n{match_id.strip()}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def get(self, customer_token: str, match_id: str) -> Optional[Dict[str, Any]]:
        if not customer_token or not match_id:
            return None
        key = self._key(customer_token, match_id)
        cached = self._memory.get(key)
        if cached is not None:
            return dict(cached)

        path = self.root / f"{key}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != _CACHE_VERSION:
            return None
        if str(payload.get("match_id") or "") != str(match_id):
            return None
        detail = payload.get("detail")
        if not isinstance(detail, dict):
            return None
        self._memory[key] = dict(detail)
        return dict(detail)

    def put(self, customer_token: str, match_id: str, detail: Dict[str, Any]) -> None:
        if not customer_token or not match_id or not isinstance(detail, dict):
            return
        key = self._key(customer_token, match_id)
        snapshot = dict(detail)
        self._memory[key] = snapshot
        payload = {
            "version": _CACHE_VERSION,
            "match_id": str(match_id),
            "saved_at": int(time.time()),
            "detail": snapshot,
        }
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{key}.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            tmp.replace(path)
        except (OSError, TypeError, ValueError):
            # 磁盘缓存失败不影响本次内存缓存与详情展示。
            return


__all__ = ["MatchDetailCache", "default_cache_root"]
