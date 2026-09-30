# -*- coding: utf-8 -*-
"""Single-source Overwatch/Dashen game catalogue.

The project used to maintain three separate mappings for the same object:

* Dashen numeric GUID -> Chinese display name (backend)
* Chinese/English name -> local asset key (UI)
* asset key -> image file (assets/manifest.json)

That split is exactly how a newly-added hero/map can have a perfectly valid image
on disk but still render as a placeholder: one of the independent mappings was
not updated.  ``assets/game_catalog.json`` is now the authoritative join table.
Each record keeps the upstream id(s), canonical key, zh-CN name and local asset
file together.  UI and backend both resolve through this module.

The catalogue is deliberately data-driven.  New content should be added/synced
as a JSON record; application code should not need another hard-coded GUID table.
``scripts/sync_game_catalog.py`` can merge the current Overstats/Dashen
``query_tool.json`` into this file and download missing hero/map pictures.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Mapping

from .localization import localize_hero, localize_map

ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT / "assets"
CATALOG_PATH = ASSETS_DIR / "game_catalog.json"


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"[\s_]+", " ", text)
    return text.strip()


def _looks_numeric_id(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and re.fullmatch(r"(?:0x[0-9a-fA-F]+|\d{12,})", text))


def _load_catalog() -> Dict[str, Any]:
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


_CATALOG: Dict[str, Any] = _load_catalog()
_HEROES: Dict[str, Dict[str, Any]] = {
    str(k): dict(v or {}) for k, v in dict(_CATALOG.get("heroes") or {}).items()
}
_MAPS: Dict[str, Dict[str, Any]] = {
    str(k): dict(v or {}) for k, v in dict(_CATALOG.get("maps") or {}).items()
}
_MODES: Dict[str, Dict[str, Any]] = {
    str(k): dict(v or {}) for k, v in dict(_CATALOG.get("map_modes") or {}).items()
}


def _build_index(records: Mapping[str, Mapping[str, Any]]) -> tuple[Dict[str, str], Dict[str, str]]:
    by_id: Dict[str, str] = {}
    by_name: Dict[str, str] = {}
    for key, entry in records.items():
        for upstream_id in entry.get("ids") or ():
            token = str(upstream_id or "").strip()
            if token:
                by_id[token] = key
        names = {key, entry.get("name_zh", "")}
        names.update(str(v) for v in entry.get("aliases") or ())
        for name in names:
            token = _norm(name)
            if token:
                by_name.setdefault(token, key)
    return by_id, by_name


_HERO_BY_ID, _HERO_BY_NAME = _build_index(_HEROES)
_MAP_BY_ID, _MAP_BY_NAME = _build_index(_MAPS)
_MODE_BY_ID, _MODE_BY_NAME = _build_index(_MODES)

# Backward-compatible views used by existing tests/callers.  They are generated
# from the JSON catalogue instead of being a second hand-maintained source.
_ROLE_TO_LEGACY = {"damage": "dps", "support": "healer", "tank": "tank"}
_DASHEN_HEROES: Dict[str, tuple[str, str]] = {}
for _guid, _key in _HERO_BY_ID.items():
    _entry = _HEROES.get(_key) or {}
    _DASHEN_HEROES[_guid] = (_key, _ROLE_TO_LEGACY.get(str(_entry.get("role") or ""), str(_entry.get("role") or "")))

_DASHEN_MAPS: Dict[str, tuple[str, str]] = {}
for _guid, _key in _MAP_BY_ID.items():
    _entry = _MAPS.get(_key) or {}
    _DASHEN_MAPS[_guid] = (str(_entry.get("name_zh") or ""), str(_entry.get("mode_zh") or ""))


def catalog_data() -> Dict[str, Any]:
    """Return the loaded catalogue (read-only by convention)."""
    return _CATALOG


def hero_catalog_entry(hero_guid: Any = "", hero_name: Any = "") -> tuple[str, Dict[str, Any]]:
    guid = str(hero_guid or "").strip()
    if guid and guid in _HERO_BY_ID:
        key = _HERO_BY_ID[guid]
        return key, dict(_HEROES.get(key) or {})
    for raw in (hero_name, hero_guid):
        token = _norm(raw)
        if token and token in _HERO_BY_NAME:
            key = _HERO_BY_NAME[token]
            return key, dict(_HEROES.get(key) or {})
    return "", {}


def map_catalog_entry(map_guid: Any = "", map_name: Any = "") -> tuple[str, Dict[str, Any]]:
    guid = str(map_guid or "").strip()
    if guid and guid in _MAP_BY_ID:
        key = _MAP_BY_ID[guid]
        return key, dict(_MAPS.get(key) or {})
    for raw in (map_name, map_guid):
        token = _norm(raw)
        if token and token in _MAP_BY_NAME:
            key = _MAP_BY_NAME[token]
            return key, dict(_MAPS.get(key) or {})
    return "", {}


def map_mode_catalog_entry(value: Any) -> tuple[str, Dict[str, Any]]:
    raw = str(value or "").strip()
    if raw in _MODES:
        return raw, dict(_MODES.get(raw) or {})
    token = _norm(raw)
    key = _MODE_BY_NAME.get(token, "")
    if key:
        return key, dict(_MODES.get(key) or {})
    # Keep the old permissive substring fallback for compound UI strings such
    # as "竞技 · 预设职责 / 机动推进" while still returning a canonical key.
    for alias, candidate in _MODE_BY_NAME.items():
        if alias and alias in token:
            return candidate, dict(_MODES.get(candidate) or {})
    return "", {}


def resolve_hero(hero_guid: Any = "", hero_name: Any = "") -> Dict[str, str]:
    """Resolve upstream id/name to one canonical hero record."""
    guid = str(hero_guid or "").strip()
    raw_name = str(hero_name or "").strip()
    key, entry = hero_catalog_entry(guid, raw_name)
    if entry:
        role = str(entry.get("role") or "")
        role_type = _ROLE_TO_LEGACY.get(role, role)
        return {
            "hero_guid": guid,
            "hero_key": key,
            "hero_name": str(entry.get("name_zh") or localize_hero(key)),
            "role_type": role_type,
            "asset_file": str(entry.get("file") or ""),
        }

    name = localize_hero(raw_name) if raw_name and not _looks_numeric_id(raw_name) else ""
    return {
        "hero_guid": guid,
        "hero_key": "",
        "hero_name": "" if _looks_numeric_id(name) else name,
        "role_type": "",
        "asset_file": "",
    }


def map_mode_key(mode_zh_or_key: Any) -> str:
    key, _ = map_mode_catalog_entry(mode_zh_or_key)
    return key


def map_mode_asset(mode_zh_or_key: Any) -> str:
    _, entry = map_mode_catalog_entry(mode_zh_or_key)
    return str(entry.get("file") or "") if entry else ""


def resolve_map(map_guid: Any = "", map_name: Any = "") -> Dict[str, str]:
    guid = str(map_guid or "").strip()
    raw_name = str(map_name or "").strip()
    key, entry = map_catalog_entry(guid, raw_name)
    if entry:
        mode_key = str(entry.get("mode") or "") or map_mode_key(entry.get("mode_zh"))
        return {
            "map_guid": guid,
            "map_key": key,
            "map_name": str(entry.get("name_zh") or "未知地图"),
            "map_mode": mode_key,
            "map_mode_zh": str(entry.get("mode_zh") or ((_MODES.get(mode_key) or {}).get("name_zh") or "")),
            "asset_file": str(entry.get("file") or ""),
        }

    name = localize_map(raw_name) if raw_name and not _looks_numeric_id(raw_name) else ""
    if not name and guid and not _looks_numeric_id(guid):
        name = localize_map(guid)
    if _looks_numeric_id(name):
        name = ""
    # Name-only data may still correspond to a mode if the catalogue knows it.
    _, named_entry = map_catalog_entry("", name)
    mode_key = str(named_entry.get("mode") or "") if named_entry else ""
    return {
        "map_guid": guid,
        "map_key": "",
        "map_name": name or "未知地图",
        "map_mode": mode_key,
        "map_mode_zh": str(named_entry.get("mode_zh") or "") if named_entry else "",
        "asset_file": "",
    }


def catalog_diagnostics(*, require_files: bool = True) -> Dict[str, list[str]]:
    """Validate the join table and report broken ID/name/file connections.

    This is intentionally usable without Qt so CI and the asset downloader can
    fail early before the UI ever renders a placeholder.
    """
    issues: Dict[str, list[str]] = {"heroes": [], "maps": [], "map_modes": []}

    def check(kind: str, records: Mapping[str, Mapping[str, Any]], *, require_id: bool) -> None:
        for key, entry in records.items():
            if not str(entry.get("name_zh") or "").strip():
                issues[kind].append(f"{key}: missing name_zh")
            if require_id and not [v for v in entry.get("ids") or () if str(v or "").strip()]:
                issues[kind].append(f"{key}: missing upstream id")
            rel = str(entry.get("file") or "").strip()
            if require_files and rel and not (ASSETS_DIR / rel).exists():
                issues[kind].append(f"{key}: missing file {rel}")

    check("heroes", _HEROES, require_id=True)
    check("maps", _MAPS, require_id=True)
    # Some non-core modes intentionally have no custom glyph yet; only validate
    # mode files that are explicitly declared.
    check("map_modes", _MODES, require_id=False)

    for key, entry in _MAPS.items():
        mode = str(entry.get("mode") or "")
        if mode and mode not in _MODES:
            issues["maps"].append(f"{key}: unknown mode key {mode}")
    return {k: v for k, v in issues.items() if v}


__all__ = [
    "CATALOG_PATH",
    "_DASHEN_HEROES",
    "_DASHEN_MAPS",
    "catalog_data",
    "catalog_diagnostics",
    "hero_catalog_entry",
    "map_catalog_entry",
    "map_mode_catalog_entry",
    "map_mode_key",
    "map_mode_asset",
    "resolve_hero",
    "resolve_map",
]
