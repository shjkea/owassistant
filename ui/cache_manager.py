# -*- coding: utf-8 -*-
"""运行缓存的集中封装：占用统计 + 安全清理。

设计约束（与需求一致）：
- 缓存路径全部复用项目已有定义：``ui.detail_cache`` 的详情缓存目录与
  ``ui.assets.AVATAR_CACHE_DIR`` 的头像缓存目录。本模块不自己拼接新路径，
  也不根据当前工作目录 / 项目路径字符串猜测删除目标。
- 只允许删除「可重新生成」的运行缓存。``assets/``、大神授权凭证、
  默认关联账号、用户设置（QSettings）、竞逐记录、项目源码全部在白名单之外，
  任何递归删除前都会先校验目标属于允许的缓存目录。
- 目录不存在时按 0 B 处理，清理操作同样静默成功，不抛异常。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

from .assets import ASSETS_DIR, AVATAR_CACHE_DIR
from .detail_cache import default_cache_root as _detail_cache_root

# 项目内的运行缓存根目录（cache/），头像缓存就在它下面。
PROJECT_CACHE_DIR = AVATAR_CACHE_DIR.parent

# 缓存分类 -> 目录。清理与统计都只在这张表覆盖的范围内进行。
CACHE_KIND_DETAIL = "detail"
CACHE_KIND_AVATAR = "avatar"
CACHE_KIND_OTHER = "other"

KIND_LABELS = {
    CACHE_KIND_DETAIL: "对局详情",
    CACHE_KIND_AVATAR: "玩家头像",
    CACHE_KIND_OTHER: "其他缓存",
}


def detail_cache_dir() -> Path:
    """单局详情磁盘缓存目录（复用 MatchDetailCache 的默认根）。"""
    return _detail_cache_root()


def avatar_cache_dir() -> Path:
    """运行时下载的玩家头像 / 网络图片缓存目录。"""
    return AVATAR_CACHE_DIR


def allowed_roots() -> List[Path]:
    """允许递归删除的缓存根目录白名单。"""
    roots: List[Path] = []
    for path in (detail_cache_dir(), PROJECT_CACHE_DIR):
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            continue
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _is_protected(path: Path) -> bool:
    """assets/ 及其子目录永远不是缓存。"""
    try:
        target = path.resolve(strict=False)
        assets = ASSETS_DIR.resolve(strict=False)
    except OSError:
        return True
    return target == assets or assets in target.parents


def is_safe_cache_path(path: Path) -> bool:
    """目标必须位于白名单缓存根之内（或正好等于某个根），且不属于 assets/。"""
    if _is_protected(path):
        return False
    try:
        target = path.resolve(strict=False)
    except OSError:
        return False
    # 不允许删除 cache/ 的父目录或项目根。
    for root in allowed_roots():
        if target == root or root in target.parents:
            return True
    return False


def dir_size(path: Path) -> int:
    """递归统计目录占用；目录不存在或不可读时返回 0。"""
    total = 0
    try:
        if not path.exists():
            return 0
        if path.is_file():
            return path.stat().st_size
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def format_size(num_bytes: int) -> str:
    value = float(max(0, int(num_bytes)))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} GB"


def _other_cache_dirs() -> List[Path]:
    """cache/ 下除头像以外的运行缓存子目录/文件。"""
    out: List[Path] = []
    try:
        if not PROJECT_CACHE_DIR.exists():
            return out
        avatars = avatar_cache_dir().resolve(strict=False)
        for entry in PROJECT_CACHE_DIR.iterdir():
            try:
                if entry.resolve(strict=False) == avatars:
                    continue
            except OSError:
                continue
            out.append(entry)
    except OSError:
        return out
    return out


@dataclass
class CacheUsage:
    """各类缓存占用（字节）。"""

    detail: int = 0
    avatar: int = 0
    other: int = 0
    paths: Dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.detail + self.avatar + self.other

    def rows(self) -> List[Tuple[str, str, int]]:
        return [
            (CACHE_KIND_DETAIL, KIND_LABELS[CACHE_KIND_DETAIL], self.detail),
            (CACHE_KIND_AVATAR, KIND_LABELS[CACHE_KIND_AVATAR], self.avatar),
            (CACHE_KIND_OTHER, KIND_LABELS[CACHE_KIND_OTHER], self.other),
        ]


def collect_usage() -> CacheUsage:
    """统计缓存占用。纯磁盘遍历，调用方应放到后台线程，避免卡住 UI。"""
    detail_dir = detail_cache_dir()
    avatar_dir = avatar_cache_dir()
    usage = CacheUsage(
        detail=dir_size(detail_dir),
        avatar=dir_size(avatar_dir),
        other=sum(dir_size(p) for p in _other_cache_dirs()),
        paths={
            CACHE_KIND_DETAIL: str(detail_dir),
            CACHE_KIND_AVATAR: str(avatar_dir),
            CACHE_KIND_OTHER: str(PROJECT_CACHE_DIR),
        },
    )
    return usage


def _purge(path: Path) -> int:
    """删除单个缓存目录/文件的内容，返回释放的字节数。"""
    if not is_safe_cache_path(path):
        return 0
    freed = dir_size(path)
    try:
        if not path.exists():
            return 0
        if path.is_file():
            path.unlink(missing_ok=True)
            return freed
        # 只清空目录内容，保留目录本身，后续写缓存不需要重建父级。
        for entry in path.iterdir():
            if not is_safe_cache_path(entry):
                continue
            try:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        return freed
    return freed


def clear_detail_cache() -> int:
    """清理单局详情磁盘缓存。删除后重新打开对应比赛会自动重新请求并生成。"""
    return _purge(detail_cache_dir())


def clear_avatar_cache() -> int:
    """清理运行时下载的玩家头像 / 网络图片缓存。下次显示时自动重新下载。

    只删除 cache/avatars/ 里的临时文件，assets/ 下的英雄 / 地图 / 段位 /
    职责正式素材不在范围内。
    """
    return _purge(avatar_cache_dir())


def clear_all_runtime_cache() -> int:
    """清理全部可重新生成的运行缓存：详情缓存 + cache/ 内的运行缓存。

    不会递归项目根目录，不删除 cache/ 的父目录，也不把 assets/ 当缓存。
    """
    freed = clear_detail_cache()
    freed += clear_avatar_cache()
    for path in _other_cache_dirs():
        freed += _purge(path)
    return freed


__all__ = [
    "CACHE_KIND_AVATAR",
    "CACHE_KIND_DETAIL",
    "CACHE_KIND_OTHER",
    "KIND_LABELS",
    "PROJECT_CACHE_DIR",
    "CacheUsage",
    "allowed_roots",
    "avatar_cache_dir",
    "clear_all_runtime_cache",
    "clear_avatar_cache",
    "clear_detail_cache",
    "collect_usage",
    "detail_cache_dir",
    "dir_size",
    "format_size",
    "is_safe_cache_path",
]
