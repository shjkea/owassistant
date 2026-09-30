# -*- coding: utf-8 -*-
"""临时诊断：用 OverFast 各英雄的全部图片 URL 哈希反查大神 heroIcon -> hero key。"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
UA = {"User-Agent": "ow-probe/1.0"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.load(r)


def cache_root() -> Path:
    base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
    return Path(base) / "OWDriveSplit" / "cache" / "match_details"


def collect_hashes(node, out, key):
    if isinstance(node, dict):
        for v in node.values():
            collect_hashes(v, out, key)
    elif isinstance(node, list):
        for v in node:
            collect_hashes(v, out, key)
    elif isinstance(node, str) and "cloudfront.net/overwatch/" in node:
        m = re.search(r"/([0-9a-f]{32,})\.", node)
        if m:
            out.setdefault(m.group(1), key)


def main() -> None:
    heroes = get("https://overfast-api.tekrop.fr/heroes")
    hash_to_key = {}
    for hero in heroes:
        key = str(hero.get("key") or "")
        collect_hashes(hero, hash_to_key, key)
        try:
            detail = get(f"https://overfast-api.tekrop.fr/heroes/{key}")
        except Exception as exc:  # noqa: BLE001
            print(f"  detail fail {key}: {exc}")
            continue
        collect_hashes(detail, hash_to_key, key)
    print(f"known cdn hashes: {len(hash_to_key)}")

    pairs = {}
    for path in sorted(cache_root().glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))

        def walk(node):
            if isinstance(node, dict):
                guid = node.get("heroGuid") or node.get("heroId")
                icon = node.get("heroIcon")
                if guid and isinstance(icon, str) and icon:
                    pairs[str(guid)] = icon
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(payload)

    hits = 0
    for guid, icon in sorted(pairs.items()):
        m = re.search(r"/([0-9a-f]{32,})\.", icon)
        token = m.group(1) if m else ""
        key = hash_to_key.get(token, "")
        hits += 1 if key else 0
        print(f"  {guid} -> {key or '(MISS) ' + token[:16]}")
    print(f"\nmatched: {hits}/{len(pairs)}")


if __name__ == "__main__":
    main()
