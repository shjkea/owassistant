# -*- coding: utf-8 -*-
"""游戏素材访问层。

职责划分：
- 静态素材（英雄头像、地图背景、段位/职责/地图类型图标）全部来自本地 ``assets/``，
  由 ``scripts/download_ow_assets.py`` 一次性准备，运行期不联网。
- 玩家头像属于动态网络资源：内存缓存 + 磁盘缓存 + 占位图，下载在 Qt 线程池执行。
- 所有缩放结果都按 (key, w, h, dpr) 缓存，避免 paintEvent 里反复解码/缩放。
- HiDPI：取图接口的尺寸一律是逻辑像素，按「逻辑尺寸 × dpr」一次缩放到物理像素，
  并 setDevicePixelRatio(dpr)；调用方拿到的 pixmap 逻辑尺寸不变、Qt 不会二次放大。

本模块不含任何业务解释逻辑，只做“名字 → 图”的查找。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sys
import unicodedata
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap

from owstats.credential_store import default_config_dir
from owstats.game_catalog import (
    catalog_data,
    map_mode_asset,
    map_mode_key as catalog_map_mode_key,
    resolve_hero,
    resolve_map,
)
from owstats.localization import localize_hero, localize_map, localize_rank, localize_role

from .async_worker import submit

LOG = logging.getLogger("ui.assets")

ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT / "assets"
MANIFEST = ASSETS_DIR / "manifest.json"
# 源码开发时保持项目内 cache/；冻结发布后写入用户目录。onefile 每次启动
# 都会解压到临时目录，因此运行缓存不能放在 ROOT 下。
AVATAR_CACHE_DIR = (
    default_config_dir() / "cache" / "avatars"
    if getattr(sys, "frozen", False)
    else ROOT / "cache" / "avatars"
)
# 程序应用图标（窗口标题栏 / 任务栏 / 以后打包 exe 共用这一份）。
APP_ICON_PATH = ASSETS_DIR / "app" / "ow_assistant_icon.ico"

# 统一占位图。缺素材时只用这两张，不自绘、不用 Emoji、不显示 GUID 数字。
PLACEHOLDER_PORTRAIT = "ui/portrait_placeholder.png"
PLACEHOLDER_MAP = "ui/map_placeholder.png"

# 地图类型（instanceType）→ 图标 key。后端返回的中文展示名也走同一张表。
MAP_MODE_KEYS = {
    "control": "control",
    "escort": "escort",
    "hybrid": "hybrid",
    "push": "push",
    "flashpoint": "flashpoint",
    "clash": "clash",
    "assault": "assault",
    "deathmatch": "deathmatch",
    "team deathmatch": "deathmatch",
    "elimination": "elimination",
    "capture the flag": "elimination",
    "stadium": "stadium",
    "占领要点": "control",
    "运载目标": "escort",
    "攻击/护送": "hybrid",
    "攻击护送": "hybrid",
    "机动推进": "push",
    "闪点作战": "flashpoint",
    "攻防阵线": "clash",
    "攻防作战": "assault",
    "死斗": "deathmatch",
    "团队死斗": "deathmatch",
    "决斗先锋": "elimination",
    "勇夺锦旗": "elimination",
    "角斗领域": "stadium",
}

RANK_KEYS = {
    "bronze": "bronze",
    "silver": "silver",
    "gold": "gold",
    "platinum": "platinum",
    "emerald": "emerald",
    "diamond": "diamond",
    "master": "master",
    "grandmaster": "grandmaster",
    "champion": "champion",
    "青铜": "bronze",
    "白银": "silver",
    "黄金": "gold",
    "白金": "platinum",
    "翡翠": "emerald",
    "钻石": "diamond",
    "大师": "master",
    "宗师": "grandmaster",
    "英杰": "champion",
    # 角斗领域赛阶暂沿用核心段位配色图标，避免出现空白位。
    "新秀": "bronze",
    "斗士": "silver",
    "精英": "gold",
    "专家": "platinum",
    "全明星": "diamond",
    "传奇": "champion",
    "500强": "champion",
}

# 逻辑职责键。严格定义：
#   tank    -> 预设职责 · 重装
#   damage  -> 预设职责 · 输出
#   support -> 预设职责 · 支援
#   flex    -> 仅「开放职责」队列
# flex 绝不作为未知职责 / 解析失败 / 默认职责的 fallback，
# 也不代表预设职责里的「任意」。
ROLE_KEYS = {
    "tank": "tank",
    "dps": "damage",
    "damage": "damage",
    "healer": "support",
    "support": "support",
    "open": "flex",
    "重装": "tank",
    "坦克": "tank",
    "输出": "damage",
    "支援": "support",
    "辅助": "support",
    "治疗": "support",
    "开放职责": "flex",
    "开放": "flex",
}

# 逻辑键 -> 实际候选文件名（按优先级）。
# 素材目录历史上用 dps/healer/open 命名，新素材用 damage/support/flex；
# 这里一次性兼容，避免各 QWidget 自己猜文件名。
# ``flex.png.png`` 是素材包里出现过的双后缀文件名，一并兜住，不去重命名用户资源。
ROLE_FILES: Dict[str, Tuple[str, ...]] = {
    "tank": ("roles/tank.png",),
    "damage": ("roles/damage.png", "roles/dps.png"),
    "support": ("roles/support.png", "roles/healer.png"),
    "flex": ("roles/flex.png", "roles/flex.png.png", "roles/open.png"),
}


def normalize_dpr(value: Any) -> float:
    """把 devicePixelRatio 规范到 0.01 精度，避免浮点误差产生重复缓存项。"""
    try:
        dpr = float(value)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(dpr) or dpr <= 0:
        return 1.0
    return min(8.0, max(0.25, round(dpr * 100) / 100.0))


def _dpr_key(dpr: float) -> int:
    return int(round(normalize_dpr(dpr) * 100))


def physical_px(logical: int, dpr: float) -> int:
    """逻辑像素 → backing store 物理像素（四舍五入，至少 1）。"""
    return max(1, int(math.floor(float(logical) * normalize_dpr(dpr) + 0.5)))


def norm_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"[\s_]+", " ", text)
    return text.strip()


class AssetLibrary(QObject):
    """静态素材索引 + QPixmap 缓存。"""

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._lock = RLock()
        self._pixmaps: Dict[str, QPixmap] = {}
        self._scaled: Dict[Tuple[str, int, int, int], QPixmap] = {}
        self._manifest: Dict[str, Any] = {}
        self._hero_index: Dict[str, str] = {}
        self._map_index: Dict[str, str] = {}
        self._mode_index: Dict[str, str] = {}
        # 缺失素材记录：kind -> 已请求但无本地文件的原始键集合。
        self._missing: Dict[str, set[str]] = {}
        self._load_manifest()

    # ---------------- 索引 ----------------
    def _load_manifest(self) -> None:
        try:
            self._manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._manifest = {}
        for key, entry in dict(self._manifest.get("heroes") or {}).items():
            rel = str((entry or {}).get("file") or f"heroes/{key}.png")
            names = {key, key.replace("-", " "), key.replace("-", "")}
            names.update(str(a) for a in (entry or {}).get("aliases") or [])
            name_en = str((entry or {}).get("name_en") or "")
            if name_en:
                names.add(name_en)
                names.add(localize_hero(name_en))
            for name in names:
                token = norm_key(name)
                if token:
                    self._hero_index.setdefault(token, rel)
        for key, entry in dict(self._manifest.get("maps") or {}).items():
            rel = str((entry or {}).get("file") or f"maps/{key}.jpg")
            names = {key, key.replace("-", " ")}
            names.update(str(a) for a in (entry or {}).get("aliases") or [])
            name_en = str((entry or {}).get("name_en") or "")
            if name_en:
                names.add(name_en)
                names.add(localize_map(name_en))
            for name in names:
                token = norm_key(name)
                if token:
                    self._map_index.setdefault(token, rel)

        # Authoritative ID/name/file join table.  This is deliberately loaded in
        # addition to manifest.json: manifest describes downloaded files, while
        # game_catalog.json binds Dashen ids + zh-CN names to those files.
        # Keeping the join in data (not QWidget code) prevents the old failure
        # mode where a new image existed but its GUID/name never reached it.
        catalog = catalog_data()
        for key, entry in dict(catalog.get("heroes") or {}).items():
            rel = str((entry or {}).get("file") or "")
            names = {key, (entry or {}).get("name_zh") or ""}
            names.update(str(a) for a in (entry or {}).get("aliases") or [])
            names.update(str(i) for i in (entry or {}).get("ids") or [])
            for name in names:
                token = norm_key(name)
                if token and rel:
                    self._hero_index[token] = rel
        for key, entry in dict(catalog.get("maps") or {}).items():
            rel = str((entry or {}).get("file") or "")
            names = {key, (entry or {}).get("name_zh") or ""}
            names.update(str(a) for a in (entry or {}).get("aliases") or [])
            names.update(str(i) for i in (entry or {}).get("ids") or [])
            for name in names:
                token = norm_key(name)
                if token and rel:
                    self._map_index[token] = rel
        for key, entry in dict(catalog.get("map_modes") or {}).items():
            rel = str((entry or {}).get("file") or "")
            names = {key, (entry or {}).get("name_zh") or ""}
            names.update(str(a) for a in (entry or {}).get("aliases") or [])
            for name in names:
                token = norm_key(name)
                if token:
                    self._mode_index[token] = rel
        # 大神部分地图名与官方 key 不完全一致，这里补一层业务侧同义词。
        for token, rel_key in (
            ("斗兽场", "colosseo"),
            ("新皇后街", "new-queen-street"),
            ("国王大道", "kings-row"),
            ("阿努比斯王座", "throne-of-anubis"),
            ("阿努比斯神殿", "anubis"),
            ("沃斯卡娅工业区", "volskaya"),
            ("地平线月球基地", "horizon"),
            ("监测站：直布罗陀", "watchpoint-gibraltar"),
            ("漓江塔", "lijiang-tower"),
            ("漓江塔夜市", "lijiang-tower"),
            ("新渣客城", "new-junk-city"),
            ("埃斯佩兰萨", "esperanca"),
            ("帕拉伊苏", "paraiso"),
            ("吉拉德堡", "chateau-guillard"),
            ("生态监测站：南极洲", "ecopoint-antarctica"),
            ("南极半岛", "antarctic-peninsula"),
            ("香巴里寺院", "shambali-monastery"),
            ("皇家赛道", "circuit-royal"),
            ("66号公路", "route-66"),
            ("墓园", "necropolis"),
        ):
            candidate = f"maps/{rel_key}.jpg"
            if (ASSETS_DIR / candidate).exists():
                self._map_index.setdefault(norm_key(token), candidate)

    def ready(self) -> bool:
        return bool(self._hero_index or self._map_index)

    # ---------------- 缺失素材记录 ----------------
    def _note_missing(self, kind: str, key: Any) -> None:
        token = str(key or "").strip()
        if not token:
            return
        with self._lock:
            bucket = self._missing.setdefault(kind, set())
            if token in bucket:
                return
            bucket.add(token)
        LOG.warning("缺少本地素材：%s=%r，已使用统一占位图", kind, token)

    def missing_assets(self) -> Dict[str, List[str]]:
        """返回本次运行中确实找不到本地文件的素材键，供诊断/验收使用。"""
        with self._lock:
            return {kind: sorted(keys) for kind, keys in self._missing.items() if keys}

    # ---------------- 基础加载 ----------------
    def _first_existing(self, candidates: Sequence[str]) -> str:
        """返回第一个真实存在的相对路径。素材命名有历史差异时统一在这里兜住。"""
        for rel in candidates:
            if rel and (ASSETS_DIR / rel).exists():
                return rel
        return ""

    def _load(self, rel_path: str) -> Optional[QPixmap]:
        if not rel_path:
            return None
        with self._lock:
            cached = self._pixmaps.get(rel_path)
            if cached is not None:
                return None if cached.isNull() else cached
            path = ASSETS_DIR / rel_path
            pix = QPixmap(str(path)) if path.exists() else QPixmap()
            self._pixmaps[rel_path] = pix
            return None if pix.isNull() else pix

    def _scaled_pixmap(
        self,
        rel_path: str,
        width: int,
        height: int,
        *,
        mode: Qt.AspectRatioMode = Qt.KeepAspectRatioByExpanding,
        dpr: float = 1.0,
    ) -> Optional[QPixmap]:
        """按「逻辑尺寸 × dpr」的物理像素从原图一次缩放，并标记 devicePixelRatio。

        返回的 pixmap 逻辑尺寸（deviceIndependentSize）≈ 请求的逻辑尺寸，
        backing store 与屏幕物理像素 1:1，Qt 不会再二次放大。
        缓存 key 含规范化后的 dpr，不同缩放的屏幕互不复用。
        """
        if width <= 0 or height <= 0:
            return None
        dpr = normalize_dpr(dpr)
        key = (rel_path, int(width), int(height), int(mode.value if hasattr(mode, "value") else mode),
               _dpr_key(dpr))
        with self._lock:
            cached = self._scaled.get(key)
            if cached is not None:
                return cached
        source = self._load(rel_path)
        if source is None:
            return None
        scaled = source.scaled(physical_px(width, dpr), physical_px(height, dpr), mode, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        with self._lock:
            # 简单容量保护：缓存过大时整体丢弃重建，避免长时间运行无限增长。
            if len(self._scaled) > 900:
                self._scaled.clear()
            self._scaled[key] = scaled
        return scaled

    # ---------------- 业务查找 ----------------
    def hero_path(self, *candidates: Any) -> str:
        for candidate in candidates:
            token = norm_key(candidate)
            if not token:
                continue
            rel = self._hero_index.get(token)
            if rel:
                return rel
            rel = self._hero_index.get(norm_key(localize_hero(candidate)))
            if rel:
                return rel
            # Dashen often returns only a numeric heroGuid. Resolve it to the
            # canonical local asset key before falling back to fuzzy matching.
            hero_meta = resolve_hero(candidate)
            direct = str(hero_meta.get("asset_file") or "")
            if direct and (ASSETS_DIR / direct).exists():
                return direct
            if hero_meta.get("hero_key"):
                rel = self._hero_index.get(norm_key(hero_meta["hero_key"]))
                if rel and (ASSETS_DIR / rel).exists():
                    return rel
            compact = token.replace(".", "").replace(":", "").replace("-", "").replace(" ", "")
            for key, value in self._hero_index.items():
                if key.replace(".", "").replace(":", "").replace("-", "").replace(" ", "") == compact:
                    return value
        return ""

    def map_path(self, *candidates: Any) -> str:
        for candidate in candidates:
            token = norm_key(candidate)
            if not token:
                continue
            rel = self._map_index.get(token)
            if rel and (ASSETS_DIR / rel).exists():
                return rel
            rel = self._map_index.get(norm_key(localize_map(candidate)))
            if rel and (ASSETS_DIR / rel).exists():
                return rel
            # Numeric Dashen mapGuid and new canonical names resolve through the
            # same catalogue as the backend, including a direct asset_file.
            meta = resolve_map(candidate)
            direct = str(meta.get("asset_file") or "")
            if direct and (ASSETS_DIR / direct).exists():
                return direct
            key = str(meta.get("map_key") or "")
            rel = self._map_index.get(norm_key(key)) if key else ""
            if rel and (ASSETS_DIR / rel).exists():
                return rel
        return ""

    def role_key(self, *candidates: Any) -> str:
        """把职责机器值 / 中文名归一到 tank / damage / support / flex。"""
        for candidate in candidates:
            token = norm_key(candidate)
            if not token:
                continue
            key = ROLE_KEYS.get(token) or ROLE_KEYS.get(norm_key(localize_role(candidate)))
            if key:
                return key
        return ""

    def role_path(self, *candidates: Any) -> str:
        key = self.role_key(*candidates)
        if not key:
            return ""
        return self._first_existing(ROLE_FILES.get(key) or ())

    def rank_key(self, rank_name: Any) -> str:
        token = norm_key(rank_name)
        key = RANK_KEYS.get(token)
        if key is None:
            # "翡翠 IV" / "Platinum 3" 这类带子段位的字符串取首段。
            head = re.split(r"[\s·]+", str(rank_name or "").strip())
            if head and head[0]:
                key = RANK_KEYS.get(norm_key(head[0])) or RANK_KEYS.get(norm_key(localize_rank(head[0])))
        return key or ""

    def map_mode_key(self, instance_type: Any) -> str:
        # First use the shared catalogue; keep the legacy table only as a
        # compatibility fallback for compound strings from old cached rows.
        key = catalog_map_mode_key(instance_type)
        if key:
            return key
        token = norm_key(instance_type)
        key = MAP_MODE_KEYS.get(token)
        if key is None:
            for name, value in MAP_MODE_KEYS.items():
                if name and name in token:
                    key = value
                    break
        return key or ""

    # ---------------- 公共取图接口 ----------------
    # 所有取图接口的 size 都是**逻辑像素**；dpr 由调用方控件的 devicePixelRatioF() 传入，
    # 默认 1.0 保持旧行为。
    def hero_icon(self, size: int, *candidates: Any, dpr: float = 1.0) -> QPixmap:
        """英雄头像。找不到本地文件时统一返回占位图，并记录缺失。"""
        rel = self.hero_path(*candidates)
        pix = self._scaled_pixmap(rel, size, size, dpr=dpr) if rel else None
        if pix is None:
            self._note_missing("hero", next((c for c in candidates if str(c or "").strip()), ""))
            pix = self._scaled_pixmap(PLACEHOLDER_PORTRAIT, size, size, dpr=dpr)
        return pix if pix is not None else QPixmap()

    # 兼容既有调用名。
    hero_portrait = hero_icon

    def map_background(self, width: int, height: int, *candidates: Any, dpr: float = 1.0) -> QPixmap:
        """地图背景。缺图时用统一占位图，绝不显示 mapGuid 数字或自绘假图。"""
        rel = self.map_path(*candidates)
        pix = self._scaled_pixmap(rel, width, height, dpr=dpr) if rel else None
        if pix is None:
            self._note_missing("map", next((c for c in candidates if str(c or "").strip()), ""))
            pix = self._scaled_pixmap(PLACEHOLDER_MAP, width, height, dpr=dpr)
        return pix if pix is not None else QPixmap()

    map_banner = map_background

    def has_map(self, *candidates: Any) -> bool:
        return bool(self.map_path(*candidates))

    def role_icon(self, role: Any, size: int, *extra: Any, dpr: float = 1.0) -> QPixmap:
        """职责图标。

        映射严格：tank/damage/support 对应预设三职责，flex 只代表开放职责。
        职责未知（解析失败 / 空值）时返回空图，调用方自行显示中性未知状态，
        绝不退回 flex.png。这属于正常的「职责未知」，不记为缺失素材。
        """
        key = self.role_key(role, *extra)
        if not key:
            return QPixmap()
        rel = self._first_existing(ROLE_FILES.get(key) or ())
        if not rel:
            self._note_missing("role", role)
            return QPixmap()
        pix = self._scaled_pixmap(rel, size, size, mode=Qt.KeepAspectRatio, dpr=dpr)
        return pix if pix is not None else QPixmap()

    def rank_icon(self, rank_name: Any, size: int, *, dpr: float = 1.0) -> QPixmap:
        """段位图标。未定级走中性 unranked 图，绝不退回青铜。"""
        key = self.rank_key(rank_name) or "unranked"
        rel = f"ranks/{key}.png"
        pix = self._scaled_pixmap(rel, size, size, mode=Qt.KeepAspectRatio, dpr=dpr)
        if pix is None:
            self._note_missing("rank", rank_name)
            pix = self._scaled_pixmap("ranks/unranked.png", size, size, mode=Qt.KeepAspectRatio, dpr=dpr)
        return pix if pix is not None else QPixmap()

    def map_mode_icon(self, instance_type: Any, size: int, *, dpr: float = 1.0) -> QPixmap:
        key = self.map_mode_key(instance_type) or "unknown"
        rel = map_mode_asset(key) or self._mode_index.get(norm_key(key)) or f"map_modes/{key}.png"
        pix = self._scaled_pixmap(rel, size, size, mode=Qt.KeepAspectRatio, dpr=dpr)
        if pix is None:
            self._note_missing("map_mode", instance_type)
            pix = self._scaled_pixmap("map_modes/unknown.png", size, size, mode=Qt.KeepAspectRatio, dpr=dpr)
        return pix if pix is not None else QPixmap()

    def placeholder_portrait(self, size: int, *, dpr: float = 1.0) -> QPixmap:
        pix = self._scaled_pixmap(PLACEHOLDER_PORTRAIT, size, size, dpr=dpr)
        return pix if pix is not None else QPixmap()


class AvatarCache(QObject):
    """玩家头像：内存 + 磁盘缓存，下载走后台线程，绝不在主线程发请求。"""

    loaded = Signal(str)  # url

    def __init__(self, loader: Callable[[str], bytes], parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._loader = loader
        self._lock = RLock()
        self._memory: Dict[str, QPixmap] = {}
        # key = (url, 逻辑尺寸, dpr_key)：不同缩放的屏幕各自生成、互不复用。
        self._scaled: Dict[Tuple[str, int, int], QPixmap] = {}
        self._pending: set[str] = set()
        self._failed: set[str] = set()
        try:
            AVATAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    @staticmethod
    def _disk_path(url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:40]
        return AVATAR_CACHE_DIR / f"{digest}.png"

    def peek(self, url: str, size: int, *, dpr: float = 1.0) -> Optional[QPixmap]:
        """返回已缓存的圆形头像；没有则触发后台加载并返回 None。

        ``size`` 为逻辑像素，``dpr`` 为目标控件的 devicePixelRatioF()。
        """
        url = str(url or "").strip()
        if not url:
            return None
        dpr = normalize_dpr(dpr)
        key = (url, int(size), _dpr_key(dpr))
        with self._lock:
            cached = self._scaled.get(key)
            if cached is not None:
                return cached
            base = self._memory.get(url)
        if base is None:
            base = self._load_from_disk(url)
        if base is None:
            self._request(url)
            return None
        rounded = self._round(base, int(size), dpr)
        with self._lock:
            if len(self._scaled) > 600:
                self._scaled.clear()
            self._scaled[key] = rounded
        return rounded

    def _load_from_disk(self, url: str) -> Optional[QPixmap]:
        path = self._disk_path(url)
        if not path.exists():
            return None
        pix = QPixmap(str(path))
        if pix.isNull():
            return None
        with self._lock:
            self._memory[url] = pix
        return pix

    def _request(self, url: str) -> None:
        with self._lock:
            if url in self._pending or url in self._failed:
                return
            self._pending.add(url)

        def task() -> bytes:
            return self._loader(url)

        submit(
            self,
            task,
            on_result=lambda data, u=url: self._store(u, data),
            on_error=lambda msg, u=url: self._fail(u, msg),
        )

    def _store(self, url: str, data: Any) -> None:
        with self._lock:
            self._pending.discard(url)
        if not isinstance(data, (bytes, bytearray)):
            self._fail(url, f"下载结果不是图片字节（{type(data).__name__}）")
            return
        pix = QPixmap()
        if not pix.loadFromData(bytes(data)):
            self._fail(url, f"QPixmap 解码失败（{len(bytes(data))} 字节）")
            return
        with self._lock:
            self._memory[url] = pix
        try:
            # 缓存目录可能刚被“清理缓存”删掉，这里按需重建。
            AVATAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self._disk_path(url).write_bytes(bytes(data))
        except OSError:
            pass
        self.loaded.emit(url)

    def _fail(self, url: str, reason: Any = "") -> None:
        with self._lock:
            self._pending.discard(url)
            self._failed.add(url)
        # 头像失败必须留下明确原因，不再静默吞掉。
        text = str(reason or "").strip() or "未知错误"
        LOG.warning("玩家头像下载失败:\n  URL=%s\n  原因=%s", url, text)

    def clear_memory(self) -> None:
        """磁盘头像缓存被清理后丢弃内存缓存，下次需要时重新下载。"""
        with self._lock:
            self._memory.clear()
            self._scaled.clear()
            self._failed.clear()

    @staticmethod
    def _round(source: QPixmap, size: int, dpr: float = 1.0) -> QPixmap:
        """圆形裁切。全程在物理像素坐标系里绘制，完成后再标记 devicePixelRatio。"""
        dpr = normalize_dpr(dpr)
        phys = physical_px(max(8, int(size)), dpr)
        scaled = source.scaled(phys, phys, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        result = QPixmap(phys, phys)
        result.fill(QColor(0, 0, 0, 0))
        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing, True)
        clip = QPainterPath()
        clip.addEllipse(0, 0, phys, phys)
        painter.setClipPath(clip)
        x = (phys - scaled.width()) // 2
        y = (phys - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()
        result.setDevicePixelRatio(dpr)
        return result


_library: Optional[AssetLibrary] = None


def library() -> AssetLibrary:
    """进程内共享的素材库。"""
    global _library
    if _library is None:
        _library = AssetLibrary()
    return _library


# ---------------- 模块级快捷函数 ----------------
# 各 QWidget 统一走这里，不再自己维护任何路径映射表。
def role_icon(role: Any, size: int, *extra: Any, dpr: float = 1.0) -> QPixmap:
    return library().role_icon(role, size, *extra, dpr=dpr)


def rank_icon(rank_name: Any, size: int, *, dpr: float = 1.0) -> QPixmap:
    return library().rank_icon(rank_name, size, dpr=dpr)


def map_mode_icon(instance_type: Any, size: int, *, dpr: float = 1.0) -> QPixmap:
    return library().map_mode_icon(instance_type, size, dpr=dpr)


def hero_icon(size: int, *candidates: Any, dpr: float = 1.0) -> QPixmap:
    return library().hero_icon(size, *candidates, dpr=dpr)


def map_background(width: int, height: int, *candidates: Any, dpr: float = 1.0) -> QPixmap:
    return library().map_background(width, height, *candidates, dpr=dpr)


def missing_assets() -> Dict[str, List[str]]:
    return library().missing_assets()


__all__ = [
    "ASSETS_DIR",
    "PLACEHOLDER_MAP",
    "PLACEHOLDER_PORTRAIT",
    "ROLE_FILES",
    "ROLE_KEYS",
    "AssetLibrary",
    "AvatarCache",
    "hero_icon",
    "library",
    "map_background",
    "map_mode_icon",
    "missing_assets",
    "norm_key",
    "normalize_dpr",
    "physical_px",
    "rank_icon",
    "role_icon",
]
