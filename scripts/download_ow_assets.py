# -*- coding: utf-8 -*-
"""一次性素材获取脚本。

产出目录（运行期 UI 只读本地文件，不再联网取这些静态素材）：

    assets/
    ├─ heroes/        英雄头像（官方 CDN portrait）
    ├─ maps/          地图背景图
    ├─ ranks/         青铜~英杰段位图标
    ├─ roles/         重装 / 输出 / 支援 / 开放职责图标
    ├─ map_modes/     占领要点 / 运载目标 / 攻击护送 / 机动推进 / 闪点作战 / 攻防阵线…
    └─ ui/            通用占位图

用法：
    python scripts/download_ow_assets.py              # 缺失的才下载
    python scripts/download_ow_assets.py --force      # 刷新可联网素材，不覆盖内置游戏同款图标
    python scripts/download_ow_assets.py --icons-only # 只检查内置参考图与中性占位图

英雄头像与地图背景来自公开 OverFast API 返回的暴雪/OverFast 静态素材地址。
职责 / 经典地图类型 / 绝大多数段位图标不再使用“自绘近似图”。项目内置的是从游戏内同款
参考图提取的透明 PNG；下载器不会用第三方风格或自绘图覆盖它们。Flashpoint / Clash 等新增地图类型
仍会优先从 OverFast 返回的游戏资源下载。翡翠是 2026 年新增段位，会单独从 Blizzard 宣传图来源抓取
并提取；若网络失败会明确报缺失，不会悄悄生成一个假的翡翠图标。
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
HEROES_DIR = ASSETS / "heroes"
MAPS_DIR = ASSETS / "maps"
RANKS_DIR = ASSETS / "ranks"
ROLES_DIR = ASSETS / "roles"
MODES_DIR = ASSETS / "map_modes"
UI_DIR = ASSETS / "ui"
SOURCE_DIR = ASSETS / "_source"
ROLE_SOURCE_DIR = SOURCE_DIR / "roles"
MODE_SOURCE_DIR = SOURCE_DIR / "map_modes"

API_HEROES = "https://overfast-api.tekrop.fr/heroes"
API_MAPS = "https://overfast-api.tekrop.fr/maps"
API_ROLES = "https://overfast-api.tekrop.fr/roles"
API_GAMEMODES = "https://overfast-api.tekrop.fr/gamemodes"
EMERALD_PROMO_URL = "https://media.dotesports.com/wp-content/uploads/2026/08/overwatch-emerald-rank-1024x576.webp"
# OverFast can lag behind the CN/Dashen map catalogue.  Keep narrowly-scoped
# official/Dashen CDN fallbacks for maps that already appear in match history.
MAP_ASSET_EXTRAS: Dict[str, Dict[str, Any]] = {
    "neon-nexus": {
        "name_en": "Neon Nexus",
        "aliases": ("neon nexus", "霓虹枢纽"),
        "gamemodes": ("hybrid",),
        "source_url": "https://img.166.net/gameyw-misc/opd/squash/20260617/181740-39ev54w1oa.png",
        "suffix": ".png",
    },
}
USER_AGENT = "ow-drive-split-assets/1.0 (+local desktop app)"
TIMEOUT = 25

# 大神 / 本地化层使用的英雄 key（与 owstats.localization 英文名一一对应，小写去符号）。
HERO_ALIAS_EXTRA: Dict[str, Tuple[str, ...]] = {
    "soldier-76": ("soldier76", "soldier 76"),
    "wrecking-ball": ("wreckingball", "hammond"),
    "junker-queen": ("junkerqueen",),
    "lifeweaver": ("lifeweaver",),
    "dva": ("d.va", "d va"),
}

ROLE_ICON_SPECS = ("tank", "dps", "healer", "open")
REFERENCE_ROLE_KEYS = {"tank", "dps", "healer"}
REFERENCE_MODE_KEYS = {"assault", "control", "push", "escort", "hybrid"}
REFERENCE_RANK_KEYS = {"bronze", "silver", "gold", "platinum", "diamond", "master", "grandmaster", "champion"}
RANK_ORDER = (
    "bronze", "silver", "gold", "platinum", "emerald",
    "diamond", "master", "grandmaster", "champion", "unranked",
)
RANK_COLORS: Dict[str, Tuple[Tuple[int, int, int], Tuple[int, int, int]]] = {
    "bronze": ((0xC3, 0x7B, 0x48), (0x7A, 0x42, 0x1E)),
    "silver": ((0xD8, 0xDE, 0xE6), (0x8C, 0x97, 0xA5)),
    "gold": ((0xF2, 0xC9, 0x50), (0xB2, 0x84, 0x18)),
    "platinum": ((0xB7, 0xE0, 0xE6), (0x63, 0x96, 0xA5)),
    "emerald": ((0x5D, 0xE0, 0x9B), (0x1E, 0x7F, 0x55)),
    "diamond": ((0x9F, 0xC6, 0xFF), (0x3F, 0x6F, 0xD8)),
    "master": ((0xFF, 0xD1, 0x7A), (0xC2, 0x71, 0x1F)),
    "grandmaster": ((0xE8, 0x9A, 0xFF), (0x7B, 0x3F, 0xC4)),
    "champion": ((0xFF, 0xA2, 0x6B), (0xC0, 0x3A, 0x2E)),
    "unranked": ((0x9A, 0xA4, 0xB4), (0x4A, 0x52, 0x60)),
}
ROLE_COLORS: Dict[str, Tuple[Tuple[int, int, int], Tuple[int, int, int]]] = {
    "tank": ((0x8E, 0xC6, 0xFF), (0x2F, 0x63, 0xA8)),
    "dps": ((0xFF, 0xA8, 0x6B), (0xB0, 0x4E, 0x25)),
    "healer": ((0x8A, 0xE2, 0xA8), (0x2A, 0x82, 0x59)),
    "open": ((0xE2, 0xC2, 0x7A), (0x93, 0x73, 0x22)),
}
MODE_COLOR = ((0xD3, 0xDC, 0xEA), (0x78, 0x85, 0x99))

MAP_MODE_KEYS = (
    "control", "escort", "hybrid", "push", "flashpoint",
    "clash", "assault", "deathmatch", "elimination", "stadium", "unknown",
)


def _log(message: str) -> None:
    print(message, flush=True)


def _ensure_dirs() -> None:
    for path in (HEROES_DIR, MAPS_DIR, RANKS_DIR, ROLES_DIR, MODES_DIR, UI_DIR, ROLE_SOURCE_DIR, MODE_SOURCE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _fetch(url: str, *, retries: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read()
        except (urllib.error.URLError, OSError) as exc:  # 网络抖动重试
            last = exc
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"下载失败：{url}（{last}）")


def _fetch_json(url: str) -> Any:
    return json.loads(_fetch(url).decode("utf-8"))


# --------------------------------------------------------------------------- 英雄 / 地图
def download_heroes(force: bool) -> Dict[str, Any]:
    heroes: List[Dict[str, Any]] = list(_fetch_json(API_HEROES))
    index: Dict[str, Any] = {}
    for hero in heroes:
        key = str(hero.get("key") or "").strip().lower()
        portrait = str(hero.get("portrait") or "").strip()
        if not key or not portrait:
            continue
        target = HEROES_DIR / f"{key}.png"
        if force or not target.exists():
            try:
                target.write_bytes(_fetch(portrait))
                _log(f"[hero] {key}")
            except RuntimeError as exc:
                _log(f"[hero:skip] {key} {exc}")
                continue
        aliases = {key, key.replace("-", ""), key.replace("-", " "),
                   str(hero.get("name") or "").strip().lower()}
        aliases.update(HERO_ALIAS_EXTRA.get(key, ()))
        index[key] = {
            "file": f"heroes/{key}.png",
            "name_en": hero.get("name"),
            "role": hero.get("role"),
            "aliases": sorted(a for a in aliases if a),
            "source": "overfast_blizzard",
            "source_url": portrait,
        }
    return index


def download_maps(force: bool) -> Dict[str, Any]:
    # OverFast occasionally lags/fails independently of the CN/Dashen CDN.
    # Do not let that prevent narrowly-scoped CN extras (e.g. Neon Nexus)
    # from being downloaded.
    try:
        maps: List[Dict[str, Any]] = list(_fetch_json(API_MAPS))
    except (RuntimeError, json.JSONDecodeError) as exc:
        _log(f"[map:warn] OverFast 地图列表获取失败，继续补齐大神新增地图：{exc}")
        maps = []
    index: Dict[str, Any] = {}
    for item in maps:
        key = str(item.get("key") or "").strip().lower()
        shot = str(item.get("screenshot") or "").strip()
        if not key or not shot:
            continue
        target = MAPS_DIR / f"{key}.jpg"
        if force or not target.exists():
            try:
                target.write_bytes(_fetch(shot))
                _log(f"[map] {key}")
            except RuntimeError as exc:
                _log(f"[map:skip] {key} {exc}")
                continue
        name = str(item.get("name") or "").strip()
        aliases = {key, key.replace("-", " "), name.lower()}
        index[key] = {
            "file": f"maps/{key}.jpg",
            "name_en": name,
            "gamemodes": list(item.get("gamemodes") or []),
            "aliases": sorted(a for a in aliases if a),
            "source": "overfast_blizzard",
            "source_url": shot,
        }

    for key, extra in MAP_ASSET_EXTRAS.items():
        suffix = str(extra.get("suffix") or ".jpg")
        target = MAPS_DIR / f"{key}{suffix}"
        source_url = str(extra.get("source_url") or "").strip()
        if source_url and (force or not target.exists()):
            try:
                target.write_bytes(_fetch(source_url))
                _log(f"[map:extra] {key}")
            except RuntimeError as exc:
                _log(f"[map:extra:skip] {key} {exc}")
        if target.exists():
            index[key] = {
                "file": f"maps/{key}{suffix}",
                "name_en": str(extra.get("name_en") or key),
                "gamemodes": list(extra.get("gamemodes") or []),
                "aliases": sorted({key, *[str(v).strip().lower() for v in extra.get("aliases") or () if str(v).strip()]}),
                "source": "dashen_official_cdn",
                "source_url": source_url,
            }
    return index


def _image_bytes_to_png(data: bytes, target: Path, size: int) -> bool:
    """把 SVG / PNG / WEBP 等网络图像规范化为透明 PNG。"""
    blob = data.lstrip()
    if blob.startswith(b"<svg") or b"<svg" in blob[:512].lower():
        # PySide6 是主程序本身的依赖；用 QtSvg 渲染官方 SVG，避免额外引入 cairosvg。
        try:
            from PySide6.QtCore import QByteArray
            from PySide6.QtGui import QImage, QPainter
            from PySide6.QtSvg import QSvgRenderer

            renderer = QSvgRenderer(QByteArray(data))
            if not renderer.isValid():
                return False
            image = QImage(size, size, QImage.Format_ARGB32)
            image.fill(0)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            return bool(image.save(str(target), "PNG"))
        except Exception:
            return False

    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGBA")
            image.thumbnail((size, size), Image.LANCZOS)
            canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            x = (size - image.width) // 2
            y = (size - image.height) // 2
            canvas.alpha_composite(image, (x, y))
            canvas.save(target, format="PNG")
        return True
    except Exception:
        return False


def download_roles(force: bool) -> Dict[str, Any]:
    """下载 Blizzard 官方职责图标；API 只负责枚举官方资源 URL。"""
    roles: List[Dict[str, Any]] = list(_fetch_json(API_ROLES))
    index: Dict[str, Any] = {}
    key_alias = {"damage": "dps", "support": "healer", "tank": "tank"}
    for item in roles:
        api_key = str(item.get("key") or "").strip().lower()
        key = key_alias.get(api_key, api_key)
        url = str(item.get("icon") or "").strip()
        if key not in {"tank", "dps", "healer"} or not url:
            continue
        target = ROLES_DIR / f"{key}.png"
        source_target = ROLE_SOURCE_DIR / f"{key}.bin"
        # 三职责使用项目内置的游戏内同款参考图，不再让网络源/自绘图覆盖。
        if key in REFERENCE_ROLE_KEYS and target.exists():
            index[key] = {
                "file": f"roles/{key}.png",
                "name_en": item.get("name"),
                "source": "game_reference",
                "source_url": None,
            }
            continue
        should_download = force or not target.exists()
        # 旧版本的 roles/*.png 是本地绘制 fallback；即使存在，也允许首轮联网时替换为官方图。
        if not should_download:
            try:
                manifest = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
                old = (manifest.get("roles_meta") or {}).get(key) or {}
                should_download = old.get("source") != "blizzard_official"
            except Exception:
                should_download = True
        if should_download:
            try:
                data = _fetch(url)
                source_target.write_bytes(data)
                if not _image_bytes_to_png(data, target, 256):
                    raise RuntimeError("无法把官方职责图标转换为 PNG")
                _log(f"[role:official] {key}")
            except RuntimeError as exc:
                _log(f"[role:fallback] {key} {exc}")
                continue
        if target.exists():
            index[key] = {
                "file": f"roles/{key}.png",
                "name_en": item.get("name"),
                "source": "blizzard_official",
                "source_url": url,
            }
    return index


def download_gamemodes(force: bool) -> Dict[str, Any]:
    """下载地图类型图标；OverFast 的 gamemode 数据来自 Blizzard 页面。"""
    modes: List[Dict[str, Any]] = list(_fetch_json(API_GAMEMODES))
    index: Dict[str, Any] = {}
    for item in modes:
        key = str(item.get("key") or "").strip().lower()
        url = str(item.get("icon") or "").strip()
        if not key or not url:
            continue
        target = MODES_DIR / f"{key}.png"
        source_target = MODE_SOURCE_DIR / f"{key}.bin"
        # 经典五类使用用户给出的游戏内同款图标，避免下载器覆盖成不同风格。
        if key in REFERENCE_MODE_KEYS and target.exists():
            index[key] = {
                "file": f"map_modes/{key}.png",
                "name_en": item.get("name"),
                "source": "game_reference",
                "source_url": None,
            }
            continue
        should_download = force or not target.exists()
        if not should_download:
            try:
                manifest = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
                old = (manifest.get("map_modes_meta") or {}).get(key) or {}
                should_download = old.get("source") != "overfast_blizzard"
            except Exception:
                should_download = True
        if should_download:
            try:
                data = _fetch(url)
                source_target.write_bytes(data)
                if not _image_bytes_to_png(data, target, 192):
                    raise RuntimeError("无法把地图类型图标转换为 PNG")
                _log(f"[map_mode:official] {key}")
            except RuntimeError as exc:
                _log(f"[map_mode:fallback] {key} {exc}")
                continue
        if target.exists():
            index[key] = {
                "file": f"map_modes/{key}.png",
                "name_en": item.get("name"),
                "source": "overfast_blizzard",
                "source_url": url,
            }
    return index


def download_emerald_rank(force: bool) -> Dict[str, Any]:
    """下载 2026 新增的翡翠段位宣传图，并提取其中的游戏内段位徽章。

    源图为 Blizzard 宣传图的媒体转载；这里只在用户本机运行一次并保存透明 PNG。
    若提取失败，不生成“近似翡翠”冒充正式图标。
    """
    target = RANKS_DIR / "emerald.png"
    if target.exists() and not force:
        return {
            "emerald": {
                "file": "ranks/emerald.png",
                "source": "blizzard_promo_extracted",
                "source_url": EMERALD_PROMO_URL,
            }
        }
    try:
        from PIL import Image, ImageFilter
    except ImportError:
        raise RuntimeError("提取翡翠图标需要 Pillow")
    data = _fetch(EMERALD_PROMO_URL)
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:
        raise RuntimeError(f"翡翠宣传图无法解码：{exc}") from exc

    # 当前公开宣传图是 1024x576；按比例截取徽章所在上半部。
    w, h = image.size
    crop = image.crop((int(w * 0.28), int(h * 0.04), int(w * 0.72), int(h * 0.69)))
    rgb = crop.load()
    cw, ch = crop.size
    seed = Image.new("L", crop.size, 0)
    sp = seed.load()
    for y in range(ch):
        for x in range(cw):
            r, g, b = rgb[x, y]
            # 翡翠主体为绿/青绿色；背景主体为洋红/红色。
            greenish = (g > 65 and g > r * 1.05 and g > b * 0.78)
            cyan_highlight = (g > 150 and b > 115 and r < g * 0.95)
            if greenish or cyan_highlight:
                sp[x, y] = 255
    region = seed.filter(ImageFilter.MaxFilter(31))
    rp = region.load()
    alpha = Image.new("L", crop.size, 0)
    ap = alpha.load()
    for y in range(ch):
        for x in range(cw):
            if rp[x, y] == 0:
                continue
            r, g, b = rgb[x, y]
            bright = max(r, g, b)
            dark = bright < 105
            greenish = (g > 55 and g > r * 1.02 and g > b * 0.72)
            neutral_highlight = (bright > 175 and max(r, g, b) - min(r, g, b) < 65)
            strongly_magenta = (r > 105 and r > g * 1.20 and r > b * 1.03)
            if not strongly_magenta and (greenish or dark or neutral_highlight):
                ap[x, y] = 255
    alpha = alpha.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    bbox = alpha.getbbox()
    if not bbox:
        raise RuntimeError("未能从宣传图中识别翡翠徽章")
    rgba = crop.convert("RGBA")
    rgba.putalpha(alpha)
    rgba = rgba.crop(bbox)
    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    max_dim = 224
    scale = min(max_dim / rgba.width, max_dim / rgba.height)
    size = (max(1, round(rgba.width * scale)), max(1, round(rgba.height * scale)))
    rgba = rgba.resize(size, Image.Resampling.LANCZOS)
    canvas.alpha_composite(rgba, ((256 - size[0]) // 2, (256 - size[1]) // 2))
    canvas.save(target, "PNG")
    _log("[rank:official] emerald")
    return {
        "emerald": {
            "file": "ranks/emerald.png",
            "source": "blizzard_promo_extracted",
            "source_url": EMERALD_PROMO_URL,
        }
    }


# --------------------------------------------------------------------------- 本地绘制图标
def _pillow():
    try:
        from PIL import Image, ImageDraw
    except ImportError:  # pragma: no cover
        raise SystemExit("绘制图标需要 Pillow：python -m pip install Pillow")
    return Image, ImageDraw


def _canvas(size: int = 512):
    Image, ImageDraw = _pillow()
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image, "RGBA")


def _save(image, path: Path, size: int) -> None:
    Image, _ = _pillow()
    image.resize((size, size), Image.LANCZOS).save(path, format="PNG")


def _polygon(draw, points: Sequence[Tuple[float, float]], fill, outline=None, width: int = 0) -> None:
    draw.polygon(list(points), fill=fill, outline=outline, width=width)


def _shield_points(cx: float, cy: float, w: float, h: float) -> List[Tuple[float, float]]:
    top = cy - h / 2
    bottom = cy + h / 2
    half = w / 2
    points = [(cx - half, top), (cx + half, top), (cx + half, cy + h * 0.08)]
    steps = 26
    for i in range(steps + 1):
        t = i / steps
        x = cx + half * (1 - t) * math.cos(t * math.pi / 2)
        y = cy + h * 0.08 + (bottom - (cy + h * 0.08)) * math.sin(t * math.pi / 2)
        points.append((x, y))
    for i in range(steps + 1):
        t = 1 - i / steps
        x = cx - half * (1 - t) * math.cos(t * math.pi / 2)
        y = cy + h * 0.08 + (bottom - (cy + h * 0.08)) * math.sin(t * math.pi / 2)
        points.append((x, y))
    return points


def _draw_role_tank(draw, light, dark) -> None:
    # 重装：官方盾形轮廓 + 内嵌横向加固条。
    _polygon(draw, _shield_points(256, 250, 300, 340), fill=dark)
    _polygon(draw, _shield_points(256, 250, 230, 268), fill=light)
    draw.rectangle((150, 228, 362, 272), fill=dark)


def _draw_role_dps(draw, light, dark) -> None:
    # 输出：靶心 + 十字准星。
    draw.ellipse((96, 96, 416, 416), outline=dark, width=44)
    draw.ellipse((150, 150, 362, 362), outline=light, width=30)
    draw.ellipse((226, 226, 286, 286), fill=light)
    for box in ((238, 52, 274, 150), (238, 362, 274, 460),
                (52, 238, 150, 274), (362, 238, 460, 274)):
        draw.rectangle(box, fill=dark)


def _draw_role_healer(draw, light, dark) -> None:
    # 支援：官方十字 + 外环。
    draw.ellipse((80, 80, 432, 432), outline=dark, width=36)
    draw.rectangle((214, 130, 298, 382), fill=light)
    draw.rectangle((130, 214, 382, 298), fill=light)
    draw.rectangle((236, 152, 276, 360), fill=dark)
    draw.rectangle((152, 236, 360, 276), fill=dark)


def _draw_role_open(draw, light, dark) -> None:
    # 开放职责：三职责轮廓合一（盾 + 准星 + 十字的简化组合）。
    _polygon(draw, _shield_points(176, 214, 176, 210), fill=dark)
    draw.ellipse((238, 96, 430, 288), outline=dark, width=34)
    draw.ellipse((300, 158, 368, 226), fill=light)
    draw.rectangle((196, 318, 316, 368), fill=light)
    draw.rectangle((231, 283, 281, 403), fill=light)
    draw.rectangle((330, 300, 420, 396), outline=dark, width=30)


ROLE_PAINTERS: Dict[str, Callable[..., None]] = {
    "tank": _draw_role_tank,
    "dps": _draw_role_dps,
    "healer": _draw_role_healer,
    "open": _draw_role_open,
}


def _draw_rank(draw, key: str, light, dark) -> None:
    """段位图标：统一的双翼 + 中心宝石轮廓，按段位配色区分（与参考图小图标风格一致）。"""
    cx, cy = 256, 262
    wing = [
        (cx - 210, cy - 54), (cx - 92, cy - 96), (cx - 58, cy - 26),
        (cx - 96, cy + 30), (cx - 176, cy + 12),
    ]
    _polygon(draw, wing, fill=dark)
    _polygon(draw, [(x + 2 * (cx - x), y) for x, y in wing], fill=dark)
    inner = [
        (cx - 150, cy - 40), (cx - 96, cy - 62), (cx - 76, cy - 22),
        (cx - 104, cy + 8), (cx - 140, cy - 2),
    ]
    _polygon(draw, inner, fill=light)
    _polygon(draw, [(x + 2 * (cx - x), y) for x, y in inner], fill=light)
    gem = [(cx, cy - 122), (cx + 62, cy - 28), (cx, cy + 108), (cx - 62, cy - 28)]
    _polygon(draw, gem, fill=dark)
    _polygon(draw, [(cx, cy - 86), (cx + 38, cy - 24), (cx, cy + 66), (cx - 38, cy - 24)], fill=light)
    tier = RANK_ORDER.index(key) if key in RANK_ORDER else 0
    # 顶部小星数量随段位递增，便于弱视觉下也能区分层级。
    stars = 0 if key == "unranked" else min(3, 1 + tier // 4)
    for i in range(stars):
        offset = (i - (stars - 1) / 2) * 62
        sx, sy = cx + offset, cy - 168
        _polygon(draw, [(sx, sy - 26), (sx + 24, sy + 18), (sx - 24, sy + 18)], fill=light)


def _draw_mode_control(draw, light, dark) -> None:
    draw.ellipse((84, 84, 428, 428), outline=dark, width=40)
    draw.ellipse((156, 156, 356, 356), outline=light, width=28)
    draw.ellipse((222, 222, 290, 290), fill=light)


def _draw_mode_escort(draw, light, dark) -> None:
    draw.rectangle((110, 176, 402, 306), fill=dark)
    draw.rectangle((146, 206, 366, 276), fill=light)
    draw.ellipse((136, 296, 216, 376), fill=dark)
    draw.ellipse((296, 296, 376, 376), fill=dark)
    draw.ellipse((158, 318, 194, 354), fill=light)
    draw.ellipse((318, 318, 354, 354), fill=light)


def _draw_mode_hybrid(draw, light, dark) -> None:
    _polygon(draw, [(96, 256), (206, 146), (206, 366)], fill=dark)
    draw.rectangle((206, 206, 384, 306), fill=dark)
    draw.rectangle((238, 232, 356, 280), fill=light)
    draw.ellipse((226, 300, 290, 364), fill=dark)
    draw.ellipse((316, 300, 380, 364), fill=dark)


def _draw_mode_push(draw, light, dark) -> None:
    draw.rectangle((170, 130, 344, 344), fill=dark)
    draw.rectangle((202, 166, 312, 308), fill=light)
    draw.rectangle((150, 344, 364, 392), fill=dark)
    _polygon(draw, [(390, 200), (474, 256), (390, 312)], fill=dark)
    _polygon(draw, [(122, 200), (38, 256), (122, 312)], fill=dark)


def _draw_mode_flashpoint(draw, light, dark) -> None:
    _polygon(draw, [(256, 62), (322, 208), (470, 208), (350, 300),
                    (398, 450), (256, 360), (114, 450), (162, 300),
                    (42, 208), (190, 208)], fill=dark)
    draw.ellipse((202, 208, 310, 316), fill=light)


def _draw_mode_clash(draw, light, dark) -> None:
    draw.rectangle((70, 214, 200, 298), fill=dark)
    draw.rectangle((312, 214, 442, 298), fill=dark)
    _polygon(draw, [(200, 152), (284, 256), (200, 360)], fill=light)
    _polygon(draw, [(312, 152), (228, 256), (312, 360)], fill=light)


def _draw_mode_assault(draw, light, dark) -> None:
    _polygon(draw, _shield_points(256, 250, 296, 330), fill=dark)
    draw.rectangle((214, 158, 298, 344), fill=light)
    draw.rectangle((158, 214, 354, 288), fill=light)


def _draw_mode_deathmatch(draw, light, dark) -> None:
    for angle in (38, -38):
        rad = math.radians(angle)
        dx, dy = math.cos(rad), math.sin(rad)
        cx, cy = 256, 256
        length, half = 210, 22
        pts = [
            (cx - dx * length - dy * half, cy - dy * length + dx * half),
            (cx - dx * length + dy * half, cy - dy * length - dx * half),
            (cx + dx * length + dy * half, cy + dy * length - dx * half),
            (cx + dx * length - dy * half, cy + dy * length + dx * half),
        ]
        _polygon(draw, pts, fill=dark)
    draw.ellipse((222, 222, 290, 290), fill=light)


def _draw_mode_elimination(draw, light, dark) -> None:
    draw.ellipse((96, 96, 416, 416), outline=dark, width=40)
    _polygon(draw, [(178, 254), (238, 314), (346, 186), (382, 224),
                    (238, 386), (142, 290)], fill=light)


def _draw_mode_stadium(draw, light, dark) -> None:
    draw.ellipse((62, 132, 450, 380), outline=dark, width=42)
    draw.ellipse((146, 196, 366, 316), outline=light, width=28)
    draw.rectangle((236, 60, 276, 140), fill=dark)
    _polygon(draw, [(256, 30), (296, 62), (256, 94), (216, 62)], fill=light)


def _draw_mode_unknown(draw, light, dark) -> None:
    draw.ellipse((96, 96, 416, 416), outline=dark, width=40)
    draw.rectangle((236, 160, 276, 286), fill=light)
    draw.ellipse((230, 318, 282, 370), fill=light)


MODE_PAINTERS: Dict[str, Callable[..., None]] = {
    "control": _draw_mode_control,
    "escort": _draw_mode_escort,
    "hybrid": _draw_mode_hybrid,
    "push": _draw_mode_push,
    "flashpoint": _draw_mode_flashpoint,
    "clash": _draw_mode_clash,
    "assault": _draw_mode_assault,
    "deathmatch": _draw_mode_deathmatch,
    "elimination": _draw_mode_elimination,
    "stadium": _draw_mode_stadium,
    "unknown": _draw_mode_unknown,
}


def _draw_placeholder_portrait(draw) -> None:
    draw.rounded_rectangle((16, 16, 496, 496), radius=72, fill=(0x22, 0x28, 0x34, 255))
    draw.ellipse((176, 116, 336, 276), fill=(0x4A, 0x55, 0x68, 255))
    _polygon(draw, [(112, 470), (162, 330), (350, 330), (400, 470)], fill=(0x4A, 0x55, 0x68, 255))


def _draw_placeholder_map(path: Path) -> None:
    Image, ImageDraw = _pillow()
    image = Image.new("RGBA", (960, 320), (0x14, 0x18, 0x21, 255))
    draw = ImageDraw.Draw(image, "RGBA")
    for x in range(0, 960, 48):
        shade = 0x1B + (x // 48 % 3) * 5
        draw.rectangle((x, 0, x + 47, 320), fill=(shade, shade + 4, shade + 12, 255))
    draw.polygon([(0, 260), (220, 150), (430, 250), (660, 120), (960, 230), (960, 320), (0, 320)],
                 fill=(0x24, 0x2B, 0x38, 255))
    image.save(path, format="PNG")


def generate_icons(force: bool) -> None:
    """只生成没有可靠游戏内参考素材的兜底项。

    三职责、经典五类地图模式、青铜~英杰正式段位都不再自绘。
    """
    # 开放职责暂无用户给出的游戏内独立职责 glyph，保留中性组合图作为兜底。
    key = "open"
    target = ROLES_DIR / f"{key}.png"
    if force or not target.exists():
        image, draw = _canvas()
        light, dark = ROLE_COLORS[key]
        ROLE_PAINTERS[key](draw, light + (255,), dark + (255,))
        _save(image, target, 256)
        _log("[role:fallback] open")

    # 未定级不是竞技段位，不伪造为某个真实段位；仅保留中性占位图。
    key = "unranked"
    target = RANKS_DIR / f"{key}.png"
    if force or not target.exists():
        image, draw = _canvas()
        light, dark = RANK_COLORS[key]
        _draw_rank(draw, key, light + (255,), dark + (255,))
        _save(image, target, 256)
        _log("[rank:fallback] unranked")

    # 新模式若联网抓取失败，不用自绘“伪官方”图标；unknown 仅用于缺失占位。
    key = "unknown"
    target = MODES_DIR / f"{key}.png"
    if force or not target.exists():
        image, draw = _canvas()
        light, dark = MODE_COLOR
        MODE_PAINTERS[key](draw, light + (255,), dark + (255,))
        _save(image, target, 192)
        _log("[map_mode:fallback] unknown")

    portrait = UI_DIR / "portrait_placeholder.png"
    if force or not portrait.exists():
        image, draw = _canvas()
        _draw_placeholder_portrait(draw)
        _save(image, portrait, 256)
        _log("[ui] portrait_placeholder")

    map_placeholder = UI_DIR / "map_placeholder.png"
    if force or not map_placeholder.exists():
        _draw_placeholder_map(map_placeholder)
        _log("[ui] map_placeholder")


def write_manifest(
    heroes: Dict[str, Any],
    maps: Dict[str, Any],
    roles_meta: Dict[str, Any],
    map_modes_meta: Dict[str, Any],
) -> None:
    roles_meta = {
        **{k: {"file": f"roles/{k}.png", "source": "game_reference"} for k in REFERENCE_ROLE_KEYS if (ROLES_DIR / f"{k}.png").exists()},
        **roles_meta,
    }
    map_modes_meta = {
        **{k: {"file": f"map_modes/{k}.png", "source": "game_reference"} for k in REFERENCE_MODE_KEYS if (MODES_DIR / f"{k}.png").exists()},
        **map_modes_meta,
    }
    manifest = {
        "generated_at": int(time.time()),
        "heroes": heroes,
        "maps": maps,
        "roles": {k: f"roles/{k}.png" for k in ROLE_ICON_SPECS if (ROLES_DIR / f"{k}.png").exists()},
        "roles_meta": roles_meta,
        "ranks": {k: f"ranks/{k}.png" for k in RANK_ORDER if (RANKS_DIR / f"{k}.png").exists()},
        "ranks_meta": {
            **{k: {"file": f"ranks/{k}.png", "source": "game_reference"} for k in REFERENCE_RANK_KEYS if (RANKS_DIR / f"{k}.png").exists()},
            **({"emerald": {"file": "ranks/emerald.png", "source": "blizzard_promo_extracted", "source_url": EMERALD_PROMO_URL}} if (RANKS_DIR / "emerald.png").exists() else {}),
            **({"unranked": {"file": "ranks/unranked.png", "source": "generated_neutral_placeholder"}} if (RANKS_DIR / "unranked.png").exists() else {}),
        },
        "map_modes": {k: f"map_modes/{k}.png" for k in MAP_MODE_KEYS if (MODES_DIR / f"{k}.png").exists()},
        "map_modes_meta": map_modes_meta,
        "ui": {
            "portrait_placeholder": "ui/portrait_placeholder.png",
            "map_placeholder": "ui/map_placeholder.png",
        },
        "sources": {
            "heroes": API_HEROES,
            "maps": API_MAPS,
            "roles": API_ROLES,
            "gamemodes": API_GAMEMODES,
            "emerald_rank": EMERALD_PROMO_URL,
        },
    }
    path = ASSETS / "manifest.json"
    existing: Dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    # 增量合并：网络临时失败时不能把已有素材索引清空。
    for section in ("heroes", "maps", "roles_meta", "map_modes_meta"):
        merged = dict(existing.get(section) or {})
        merged.update(manifest[section])
        manifest[section] = merged
    for key in REFERENCE_ROLE_KEYS:
        if (ROLES_DIR / f"{key}.png").exists():
            manifest["roles_meta"][key] = {"file": f"roles/{key}.png", "source": "game_reference"}
    for key in REFERENCE_MODE_KEYS:
        if (MODES_DIR / f"{key}.png").exists():
            manifest["map_modes_meta"][key] = {"file": f"map_modes/{key}.png", "source": "game_reference"}
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _log(
        f"[manifest] {path.relative_to(ROOT)} "
        f"heroes={len(manifest['heroes'])} maps={len(manifest['maps'])} "
        f"roles={len(manifest['roles_meta'])} modes={len(manifest['map_modes_meta'])}"
    )


def verify_assets() -> int:
    """检查 UI 运行所需的本地文件是否存在。"""
    manifest_path = ASSETS / "manifest.json"
    if not manifest_path.exists():
        _log("[verify] 缺少 assets/manifest.json")
        return 2
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log(f"[verify] manifest 无法读取：{exc}")
        return 2

    missing: List[str] = []
    for section in ("heroes", "maps"):
        for key, entry in dict(manifest.get(section) or {}).items():
            rel = str((entry or {}).get("file") or "")
            if rel and not (ASSETS / rel).exists():
                missing.append(f"{section}:{key} -> {rel}")
    for section in ("roles", "ranks", "map_modes", "ui"):
        for key, rel in dict(manifest.get(section) or {}).items():
            if rel and not (ASSETS / str(rel)).exists():
                missing.append(f"{section}:{key} -> {rel}")

    required = {
        "roles": ("tank", "dps", "healer"),
        "ranks": ("bronze", "silver", "gold", "platinum", "emerald", "diamond", "master", "grandmaster", "champion"),
        "map_modes": ("assault", "control", "escort", "hybrid", "push", "flashpoint", "clash"),
    }
    for section, keys in required.items():
        mapping = dict(manifest.get(section) or {})
        for key in keys:
            rel = str(mapping.get(key) or "")
            if not rel or not (ASSETS / rel).exists():
                item = f"{section}:{key} -> {rel or '未建立索引'}"
                if item not in missing:
                    missing.append(item)

    # game_catalog.json is the authoritative ID/name/file join.  Validate it
    # as part of the existing asset check so an image can no longer silently
    # exist on disk while its upstream ID is disconnected (or vice versa).
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from owstats.game_catalog import catalog_diagnostics
        for kind, rows in catalog_diagnostics(require_files=True).items():
            for row in rows:
                item = f"catalog:{kind}:{row}"
                if item not in missing:
                    missing.append(item)
    except Exception as exc:
        missing.append(f"catalog:无法验证 game_catalog.json ({exc})")

    if missing:
        _log(f"[verify] 缺少 {len(missing)} 个文件/连接：")
        for item in missing[:50]:
            _log(f"  - {item}")
        if len(missing) > 50:
            _log(f"  ... 另外 {len(missing) - 50} 个")
        return 1

    _log(
        "[verify] OK："
        f"英雄 {len(manifest.get('heroes') or {})}，"
        f"地图 {len(manifest.get('maps') or {})}，"
        f"职责 {len(manifest.get('roles') or {})}，"
        f"段位 {len(manifest.get('ranks') or {})}，"
        f"地图类型 {len(manifest.get('map_modes') or {})}。"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="一次性准备 OW 战绩 UI 所需静态素材")
    parser.add_argument("--force", action="store_true", help="重新下载网络素材；不会覆盖内置的游戏内同款参考图标")
    parser.add_argument("--icons-only", action="store_true", help="只确保本地参考/占位图存在，不联网")
    parser.add_argument("--no-network", action="store_true", help="跳过联网，只确保内置参考图、中性占位图和 manifest 存在")
    parser.add_argument("--verify", action="store_true", help="只检查素材是否完整，不下载")
    args = parser.parse_args(list(argv) if argv is not None else None)

    _ensure_dirs()
    if args.verify:
        return verify_assets()

    if args.icons_only:
        generate_icons(force=True)
        write_manifest({}, {}, {}, {})
        _log("本地参考/占位图检查完成。")
        return 0

    heroes: Dict[str, Any] = {}
    maps: Dict[str, Any] = {}
    roles_meta: Dict[str, Any] = {}
    modes_meta: Dict[str, Any] = {}
    rank_meta: Dict[str, Any] = {}

    # 先尝试下载正式素材，随后只对缺失项生成 fallback，避免本地图标覆盖官方图标。
    if not args.no_network:
        try:
            heroes = download_heroes(force=args.force)
        except (RuntimeError, json.JSONDecodeError) as exc:
            _log(f"[warn] 英雄头像获取失败：{exc}")
        try:
            maps = download_maps(force=args.force)
        except (RuntimeError, json.JSONDecodeError) as exc:
            _log(f"[warn] 地图背景获取失败：{exc}")
        try:
            roles_meta = download_roles(force=args.force)
        except (RuntimeError, json.JSONDecodeError) as exc:
            _log(f"[warn] 职责资源枚举失败；内置三职责游戏同款图标仍保留：{exc}")
        try:
            modes_meta = download_gamemodes(force=args.force)
        except (RuntimeError, json.JSONDecodeError) as exc:
            _log(f"[warn] 新地图类型图标获取失败：{exc}")
        try:
            rank_meta = download_emerald_rank(force=args.force)
        except (RuntimeError, json.JSONDecodeError) as exc:
            _log(f"[warn] 翡翠段位图标获取失败（不会生成假图标）：{exc}")

    # --force 不删除内置的游戏内同款图标，只刷新可下载素材与中性占位图。
    if args.force:
        for name in ("portrait_placeholder.png", "map_placeholder.png"):
            try:
                (UI_DIR / name).unlink()
            except FileNotFoundError:
                pass
    generate_icons(force=False)
    write_manifest(heroes, maps, roles_meta, modes_meta)

    # Keep the upstream GUID / zh-CN name / local image join table in sync.
    # This is deliberately data-driven so newly released heroes/maps do not
    # require another Python GUID table.  A network failure never wipes the
    # existing catalogue.
    try:
        from sync_game_catalog import sync_catalog
        sync_catalog(allow_network=not args.no_network, force_download=False)
    except Exception as exc:
        _log(f"[warn] 大神素材目录同步失败；保留现有目录：{exc}")

    rc = verify_assets()
    if rc == 0:
        _log("素材准备完成。之后 UI 直接读取 assets/，不需要每次联网。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
