# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.sync_game_catalog as sync


class GameCatalogSyncTests(unittest.TestCase):
    def test_new_hero_map_and_future_mode_keep_id_name_file_joined(self):
        upstream = {
            "heroList": [
                {
                    "heroGuid": "207165582859099999",
                    "roleType": "healer",
                    "name": "测试新英雄",
                    "icon": "https://img.166.net/future-hero.png",
                }
            ],
            "mapList": [
                {
                    "guid": "576460752303499999",
                    "name": "测试新地图",
                    "mode": "测试新地图类型",
                    "icon": "https://img.166.net/future-map.jpg",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            assets = root / "assets"
            (assets / "heroes").mkdir(parents=True)
            (assets / "maps").mkdir(parents=True)
            (assets / "map_modes").mkdir(parents=True)
            mode_key = sync._stable_mode_key("测试新地图类型")
            # Simulate the future mode icon having been added independently.
            (assets / "map_modes" / f"{mode_key}.png").write_bytes(b"mode")
            (assets / "game_catalog.json").write_text(
                json.dumps({"schema_version": 1, "heroes": {}, "maps": {}, "map_modes": {}}, ensure_ascii=False),
                encoding="utf-8",
            )
            (assets / "manifest.json").write_text(
                json.dumps({"heroes": {}, "maps": {}, "map_modes": {}}, ensure_ascii=False),
                encoding="utf-8",
            )

            def fake_download(rel, url, *, force=False):
                target = assets / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(("downloaded:" + url).encode("utf-8"))
                return True

            with mock.patch.object(sync, "ASSETS", assets), \
                 mock.patch.object(sync, "CATALOG_PATH", assets / "game_catalog.json"), \
                 mock.patch.object(sync, "MANIFEST_PATH", assets / "manifest.json"), \
                 mock.patch.object(sync, "_fetch_json", return_value=upstream), \
                 mock.patch.object(sync, "_download_missing", side_effect=fake_download):
                result = sync.sync_catalog(allow_network=True)

            hero = result["heroes"]["dashen-hero-207165582859099999"]
            self.assertEqual(hero["name_zh"], "测试新英雄")
            self.assertEqual(hero["ids"], ["207165582859099999"])
            self.assertEqual(hero["role"], "support")
            self.assertTrue((assets / hero["file"]).exists())

            game_map = result["maps"]["dashen-map-576460752303499999"]
            self.assertEqual(game_map["name_zh"], "测试新地图")
            self.assertEqual(game_map["ids"], ["576460752303499999"])
            self.assertEqual(game_map["mode"], mode_key)
            self.assertTrue((assets / game_map["file"]).exists())

            mode = result["map_modes"][mode_key]
            self.assertEqual(mode["name_zh"], "测试新地图类型")
            self.assertEqual(mode["file"], f"map_modes/{mode_key}.png")


if __name__ == "__main__":
    unittest.main()
