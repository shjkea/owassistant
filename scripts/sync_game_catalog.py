# -*- coding: utf-8 -*-
"""Synchronise Dashen/Overstats IDs, Chinese names and local static assets.

The desktop UI must never need separate hand-written tables for:

    upstream GUID -> Chinese name -> local image file

``assets/game_catalog.json`` is the join table.  This tool refreshes that join
from the public Overstats/Dashen ``query_tool.json`` and downloads only missing
hero/map images.  Existing canonical local filenames are preserved; genuinely
new content receives a deterministic filename, so adding support for a new
hero/map does not require changing Python code.

Map *type* icons are slightly different: Dashen's mapList exposes the Chinese
mode name but not a mode-glyph URL.  Unknown future mode names therefore get a
stable catalogue key and an expected local filename.  Once the matching PNG is
put at that path (or a future source provides it), every UI lookup connects to
it automatically without another code mapping.

Usage:
    python scripts/sync_game_catalog.py
    python scripts/sync_game_catalog.py --no-network   # relink/validate only
    python scripts/sync_game_catalog.py --verify       # no changes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
CATALOG_PATH = ASSETS / "game_catalog.json"
MANIFEST_PATH = ASSETS / "manifest.json"
QUERY_TOOL_URL = "https://raw.githubusercontent.com/AddOneSecondL/Overstats/master/res/query_tool.json"
USER_AGENT = "ow-cn-helper-game-catalog/1.0"
TIMEOUT = 25

ROLE_MAP = {
    "tank": "tank",
    "dps": "damage",
    "damage": "damage",
    "healer": "support",
    "support": "support",
}

# Stable keys for modes already represented by project assets/UI.
KNOWN_MODE_KEYS = {
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


def _log(message: str) -> None:
    print(message, flush=True)


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"[\s_]+", " ", text).strip()


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _fetch_bytes(url: str, *, retries: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                return response.read()
        except (urllib.error.URLError, OSError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"下载失败：{url} ({last})")


def _fetch_json(url: str) -> Dict[str, Any]:
    try:
        data = json.loads(_fetch_bytes(url).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"JSON 解析失败：{url} ({exc})") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"JSON 顶层不是对象：{url}")
    return data


def _safe_suffix(url: str, default: str) -> str:
    match = re.search(r"\.(png|jpe?g|webp)(?:\?|$)", str(url or ""), flags=re.I)
    if not match:
        return default
    suffix = match.group(1).lower()
    if suffix == "jpeg":
        suffix = "jpg"
    return "." + suffix


def _stable_mode_key(name_zh: str) -> str:
    direct = KNOWN_MODE_KEYS.get(str(name_zh or "").strip())
    if direct:
        return direct
    digest = hashlib.sha1(str(name_zh or "").encode("utf-8")).hexdigest()[:10]
    return f"dashen-mode-{digest}"


def _records_by_id(records: Mapping[str, Mapping[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, entry in records.items():
        for raw in entry.get("ids") or ():
            value = str(raw or "").strip()
            if value:
                out[value] = key
    return out


def _records_by_name(records: Mapping[str, Mapping[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, entry in records.items():
        names = [key, entry.get("name_zh", ""), *(entry.get("aliases") or ())]
        for raw in names:
            token = _norm(raw)
            if token:
                out.setdefault(token, key)
    return out


def _manifest_file_candidates(manifest_section: Mapping[str, Any]) -> Dict[str, str]:
    """Index manifest keys/names/aliases -> existing relative file path."""
    out: Dict[str, str] = {}
    for key, raw in manifest_section.items():
        if isinstance(raw, str):
            rel = raw
            entry: Mapping[str, Any] = {}
        else:
            entry = raw if isinstance(raw, Mapping) else {}
            rel = str(entry.get("file") or "")
        if not rel or not (ASSETS / rel).exists():
            continue
        names = [key, entry.get("name_en", ""), *(entry.get("aliases") or ())]
        for name in names:
            token = _norm(name)
            if token:
                out.setdefault(token, rel)
    return out


def _add_alias(entry: MutableMapping[str, Any], value: Any) -> None:
    text = str(value or "").strip()
    if not text:
        return
    aliases = [str(v).strip() for v in entry.get("aliases") or () if str(v).strip()]
    seen = {_norm(v) for v in aliases}
    if _norm(text) not in seen:
        aliases.append(text)
    entry["aliases"] = aliases


def _download_missing(rel: str, url: str, *, force: bool = False) -> bool:
    if not rel or not url:
        return False
    target = ASSETS / rel
    if target.exists() and not force:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    data = _fetch_bytes(url)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    return True


def _mode_entry(modes: MutableMapping[str, Dict[str, Any]], mode_zh: str) -> str:
    text = str(mode_zh or "").strip()
    if not text:
        return "unknown"
    # Prefer a pre-existing alias/name so user overrides remain authoritative.
    by_name = _records_by_name(modes)
    existing = by_name.get(_norm(text))
    if existing:
        return existing
    key = _stable_mode_key(text)
    expected = f"map_modes/{key}.png"
    entry = dict(modes.get(key) or {})
    entry.setdefault("name_zh", text)
    entry.setdefault("file", expected if (ASSETS / expected).exists() else "")
    entry.setdefault("aliases", [key, text])
    _add_alias(entry, text)
    modes[key] = entry
    if not entry.get("file"):
        _log(f"[new-mode] {text} -> {key}; 未发现模式图标。若有 PNG，请放到 assets/{expected} 后重新运行同步。")
    return key


def sync_catalog(*, allow_network: bool = True, force_download: bool = False) -> Dict[str, Any]:
    catalog = _read_json(CATALOG_PATH)
    catalog.setdefault("schema_version", 1)
    heroes: Dict[str, Dict[str, Any]] = {str(k): dict(v or {}) for k, v in dict(catalog.get("heroes") or {}).items()}
    maps: Dict[str, Dict[str, Any]] = {str(k): dict(v or {}) for k, v in dict(catalog.get("maps") or {}).items()}
    modes: Dict[str, Dict[str, Any]] = {str(k): dict(v or {}) for k, v in dict(catalog.get("map_modes") or {}).items()}

    manifest = _read_json(MANIFEST_PATH)
    hero_manifest = _manifest_file_candidates(dict(manifest.get("heroes") or {}))
    map_manifest = _manifest_file_candidates(dict(manifest.get("maps") or {}))

    upstream: Dict[str, Any] = {}
    if allow_network:
        upstream = _fetch_json(QUERY_TOOL_URL)

    hero_by_id = _records_by_id(heroes)
    hero_by_name = _records_by_name(heroes)
    map_by_id = _records_by_id(maps)
    map_by_name = _records_by_name(maps)

    new_heroes = new_maps = downloaded = 0

    for raw in upstream.get("heroList") or ():
        if not isinstance(raw, Mapping):
            continue
        guid = str(raw.get("heroGuid") or "").strip()
        name_zh = str(raw.get("name") or "").strip()
        if not guid or not name_zh:
            continue
        key = hero_by_id.get(guid) or hero_by_name.get(_norm(name_zh))
        if not key:
            key = f"dashen-hero-{guid}"
            new_heroes += 1
        entry = dict(heroes.get(key) or {})
        entry["name_zh"] = name_zh
        entry["role"] = ROLE_MAP.get(str(raw.get("roleType") or "").strip().lower(), str(entry.get("role") or ""))
        ids = [str(v).strip() for v in entry.get("ids") or () if str(v).strip()]
        if guid not in ids:
            ids.append(guid)
        entry["ids"] = ids
        _add_alias(entry, key)
        _add_alias(entry, name_zh)
        url = str(raw.get("icon") or raw.get("smallIconUrl") or "").strip()
        if url:
            entry["source_url"] = url
        rel = str(entry.get("file") or "").strip()
        if not rel:
            # A local asset may already exist under a manifest key/alias.
            rel = hero_manifest.get(_norm(key)) or hero_manifest.get(_norm(name_zh)) or ""
        if not rel:
            rel = f"heroes/{key}{_safe_suffix(url, '.png')}"
        entry["file"] = rel
        heroes[key] = entry
        hero_by_id[guid] = key
        hero_by_name[_norm(name_zh)] = key
        if allow_network and url and (force_download or not (ASSETS / rel).exists()):
            try:
                if _download_missing(rel, url, force=force_download):
                    downloaded += 1
                    _log(f"[hero] {name_zh} -> {rel}")
            except RuntimeError as exc:
                _log(f"[hero:warn] {name_zh}: {exc}")

    for raw in upstream.get("mapList") or ():
        if not isinstance(raw, Mapping):
            continue
        guid = str(raw.get("guid") or "").strip()
        name_zh = str(raw.get("name") or "").strip()
        if not guid or not name_zh:
            continue
        key = map_by_id.get(guid) or map_by_name.get(_norm(name_zh))
        if not key:
            key = f"dashen-map-{guid}"
            new_maps += 1
        entry = dict(maps.get(key) or {})
        entry["name_zh"] = name_zh
        mode_zh = str(raw.get("mode") or entry.get("mode_zh") or "").strip()
        mode_key = _mode_entry(modes, mode_zh) if mode_zh else str(entry.get("mode") or "unknown")
        entry["mode"] = mode_key
        entry["mode_zh"] = mode_zh or str((modes.get(mode_key) or {}).get("name_zh") or "")
        ids = [str(v).strip() for v in entry.get("ids") or () if str(v).strip()]
        if guid not in ids:
            ids.append(guid)
        entry["ids"] = ids
        _add_alias(entry, key)
        _add_alias(entry, name_zh)
        url = str(raw.get("icon") or "").strip()
        if url:
            entry["source_url"] = url
        rel = str(entry.get("file") or "").strip()
        if not rel:
            rel = map_manifest.get(_norm(key)) or map_manifest.get(_norm(name_zh)) or ""
        if not rel:
            rel = f"maps/{key}{_safe_suffix(url, '.jpg')}"
        entry["file"] = rel
        maps[key] = entry
        map_by_id[guid] = key
        map_by_name[_norm(name_zh)] = key
        if allow_network and url and (force_download or not (ASSETS / rel).exists()):
            try:
                if _download_missing(rel, url, force=force_download):
                    downloaded += 1
                    _log(f"[map] {name_zh} -> {rel}")
            except RuntimeError as exc:
                _log(f"[map:warn] {name_zh}: {exc}")

    # If a mode icon has since been dropped at its deterministic path, connect it
    # without another code edit.
    for key, raw in list(modes.items()):
        entry = dict(raw or {})
        if not str(entry.get("file") or "").strip():
            expected = ASSETS / "map_modes" / f"{key}.png"
            if expected.exists():
                entry["file"] = f"map_modes/{key}.png"
                modes[key] = entry

    catalog["heroes"] = heroes
    catalog["maps"] = maps
    catalog["map_modes"] = modes
    catalog["source"] = QUERY_TOOL_URL
    catalog["updated_at"] = int(time.time())
    _write_json_atomic(CATALOG_PATH, catalog)

    # Keep manifest compatibility for old callers.  The catalogue remains the
    # authoritative join table; these entries simply prevent legacy code/tools
    # from losing newly downloaded files.
    manifest.setdefault("heroes", {})
    manifest.setdefault("maps", {})
    manifest.setdefault("map_modes", {})
    for key, entry in heroes.items():
        rel = str(entry.get("file") or "")
        if rel and (ASSETS / rel).exists():
            old = dict((manifest["heroes"].get(key) or {}) if isinstance(manifest["heroes"].get(key), Mapping) else {})
            old.update({"file": rel, "aliases": list(entry.get("aliases") or ()), "source_url": entry.get("source_url") or ""})
            manifest["heroes"][key] = old
    for key, entry in maps.items():
        rel = str(entry.get("file") or "")
        if rel and (ASSETS / rel).exists():
            old = dict((manifest["maps"].get(key) or {}) if isinstance(manifest["maps"].get(key), Mapping) else {})
            old.update({"file": rel, "aliases": list(entry.get("aliases") or ()), "source_url": entry.get("source_url") or ""})
            manifest["maps"][key] = old
    for key, entry in modes.items():
        rel = str(entry.get("file") or "")
        if rel and (ASSETS / rel).exists():
            manifest["map_modes"][key] = rel
    manifest.setdefault("sources", {})["dashen_catalog"] = QUERY_TOOL_URL
    _write_json_atomic(MANIFEST_PATH, manifest)

    _log(f"[catalog] heroes={len(heroes)} maps={len(maps)} modes={len(modes)} new_heroes={new_heroes} new_maps={new_maps} downloaded={downloaded}")
    return catalog


def verify_catalog() -> int:
    # Import only after any sync has completed so the module sees the fresh JSON.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from owstats.game_catalog import catalog_diagnostics

    issues = catalog_diagnostics(require_files=True)
    if not issues:
        _log("[catalog:verify] OK：ID / 中文名 / 本地素材连接完整。")
        return 0
    count = sum(len(v) for v in issues.values())
    _log(f"[catalog:verify] 发现 {count} 个问题：")
    for kind, rows in issues.items():
        for row in rows:
            _log(f"  - {kind}: {row}")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同步大神英雄/地图 GUID、中文名与本地素材目录")
    parser.add_argument("--no-network", action="store_true", help="不访问上游，只重连本地目录并检查")
    parser.add_argument("--force", action="store_true", help="重新下载上游英雄/地图图片")
    parser.add_argument("--verify", action="store_true", help="只检查 game_catalog.json 当前连接")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.verify:
        return verify_catalog()
    try:
        sync_catalog(allow_network=not args.no_network, force_download=args.force)
    except RuntimeError as exc:
        _log(f"[catalog:warn] 上游同步失败：{exc}")
        if not args.no_network:
            _log("[catalog:warn] 保留现有 game_catalog.json；仍执行本地完整性检查。")
    return verify_catalog()


if __name__ == "__main__":
    raise SystemExit(main())
