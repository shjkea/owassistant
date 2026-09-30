# -*- coding: utf-8 -*-
import json
import unittest
from pathlib import Path

from owstats.game_catalog import (
    ASSETS_DIR,
    CATALOG_PATH,
    catalog_data,
    map_mode_asset,
    map_mode_key,
    resolve_hero,
    resolve_map,
)


class GameCatalogRegressionTests(unittest.TestCase):
    def test_neon_nexus_guid_name_mode_and_asset_target_are_joined(self):
        info = resolve_map("576460752303427628")
        self.assertEqual(info["map_name"], "霓虹枢纽")
        self.assertEqual(info["map_mode"], "hybrid")
        self.assertEqual(info["map_mode_zh"], "攻击/护送")
        self.assertEqual(info["asset_file"], "maps/neon-nexus.png")

    def test_known_map_guid_resolves_direct_local_asset(self):
        info = resolve_map("576460752303423700")
        self.assertEqual(info["map_name"], "国王大道")
        self.assertEqual(info["asset_file"], "maps/kings-row.jpg")
        self.assertTrue((ASSETS_DIR / info["asset_file"]).exists())

    def test_known_hero_guid_resolves_direct_local_asset(self):
        info = resolve_hero("207165582859043377")
        self.assertEqual(info["hero_name"], "雾子")
        self.assertTrue(info["asset_file"])
        self.assertTrue((ASSETS_DIR / info["asset_file"]).exists())

    def test_map_mode_chinese_aliases_resolve_to_declared_file(self):
        self.assertEqual(map_mode_key("攻击/护送"), "hybrid")
        self.assertEqual(map_mode_asset("攻击/护送"), "map_modes/hybrid.png")
        self.assertTrue((ASSETS_DIR / map_mode_asset("攻击/护送")).exists())
        self.assertEqual(map_mode_key("闪点作战"), "flashpoint")
        self.assertTrue((ASSETS_DIR / map_mode_asset("闪点作战")).exists())
        self.assertEqual(map_mode_key("攻防阵线"), "clash")
        self.assertTrue((ASSETS_DIR / map_mode_asset("攻防阵线")).exists())

    def test_catalog_has_no_guid_collision_between_different_records(self):
        data = catalog_data()
        for section in ("heroes", "maps"):
            seen = {}
            for key, entry in (data.get(section) or {}).items():
                for upstream_id in entry.get("ids") or ():
                    self.assertNotIn(
                        upstream_id,
                        seen,
                        f"{section} GUID {upstream_id} appears in both {seen.get(upstream_id)} and {key}",
                    )
                    seen[upstream_id] = key

    def test_catalog_json_keeps_id_name_file_together(self):
        raw = json.loads(Path(CATALOG_PATH).read_text(encoding="utf-8"))
        for section in ("heroes", "maps"):
            for key, entry in raw[section].items():
                self.assertTrue(entry.get("name_zh"), f"{section}:{key} has no Chinese name")
                self.assertTrue(entry.get("ids"), f"{section}:{key} has no upstream id")
                self.assertTrue(entry.get("file"), f"{section}:{key} has no asset file target")


if __name__ == "__main__":
    unittest.main()
