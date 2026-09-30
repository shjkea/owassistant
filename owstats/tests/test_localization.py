# -*- coding: utf-8 -*-
import unittest

from owstats.localization import (
    localize_game_mode, localize_hero, localize_instance_type,
    localize_map, localize_rank, localize_role,
)
from owstats.parsers import normalize_match, normalize_rank_info
from owstats.presentation import detail_view


class LocalizationTests(unittest.TestCase):
    def test_rank_names(self):
        self.assertEqual(localize_rank("Platinum"), "白金")
        self.assertEqual(localize_rank("Grandmaster 2"), "宗师 2")
        self.assertEqual(localize_rank("Champion IV"), "英杰 IV")
        self.assertEqual(localize_rank("Emerald"), "翡翠")
        self.assertEqual(localize_rank("Legend"), "传奇")

    def test_maps_modes_roles(self):
        self.assertEqual(localize_map("Colosseo"), "斗兽场")
        self.assertEqual(localize_map("Runasapi"), "鲁纳塞彼")
        self.assertEqual(localize_map("Hanaoka"), "花冈")
        self.assertEqual(localize_map("Aatlis"), "阿特利斯")
        self.assertEqual(localize_game_mode("SportPreset"), "竞技 · 预设职责")
        self.assertEqual(localize_game_mode("SportFight"), "角斗竞技")
        self.assertEqual(localize_instance_type("Flashpoint"), "闪点作战")
        self.assertEqual(localize_role("healer"), "支援")

    def test_heroes(self):
        self.assertEqual(localize_hero("Kiriko"), "雾子")
        self.assertEqual(localize_hero("Wuyang"), "无漾")
        self.assertEqual(localize_hero("Freja"), "弗蕾娅")
        self.assertEqual(localize_hero("D.Mon"), "D.Mon")

    def test_normalized_match_prefers_chinese_display_fields(self):
        row = normalize_match({
            "matchId": "m1", "matchRet": 1,
            "gameMode": "sport", "instanceType": "Push",
            "mapName": "Colosseo", "heroName": "Kiriko", "roleType": "healer",
        })
        self.assertEqual(row["map_name"], "斗兽场")
        self.assertEqual(row["map_name_raw"], "Colosseo")
        self.assertEqual(row["hero_name"], "雾子")
        self.assertEqual(row["hero_name_raw"], "Kiriko")
        self.assertEqual(row["role_name_zh"], "支援")
        self.assertEqual(row["game_mode_zh"], "竞技")
        self.assertEqual(row["instance_type_zh"], "机动推进")

    def test_rank_info_keeps_raw(self):
        rank = normalize_rank_info({"rank_name": "Platinum", "rank_sub_tier": 5})
        self.assertEqual(rank["rank_name"], "白金")
        self.assertEqual(rank["rank_name_raw"], "Platinum")
        self.assertEqual(rank["rank_sub_tier"], 5)

    def test_detail_presentation_uses_chinese(self):
        detail = detail_view({
            "match_id": "m1",
            "source_match": {"mapName": "King's Row", "instanceType": "Hybrid"},
            "detail": {"data": {}},
        })
        self.assertEqual(detail["map_name"], "国王大道")
        self.assertEqual(detail["mode"], "攻击/护送")



    def test_detail_role_accepts_numeric_role_guid(self):
        from owstats.parsers import normalize_detail_player

        row = normalize_detail_player({
            "name": "role-guid-only",
            "rankInfo": {"roleGuid": "972777519512029264"},
        })
        self.assertEqual(row["role_type"], "tank")
        self.assertEqual(row["role_name_zh"], "重装")

if __name__ == "__main__":
    unittest.main()

class MapGuidAndDetailRoleRegressionTests(unittest.TestCase):
    def test_dashen_numeric_map_guid_is_resolved(self):
        from owstats.parsers import normalize_match
        row = normalize_match({
            "matchId": "x",
            "mapGuid": "576460752303425374",
            "instanceType": "IT_RANKED",
            "gameMode": "SportPreset",
            "matchRet": 1,
        })
        self.assertEqual(row["map_name"], "暴雪世界")
        self.assertEqual(row["map_mode_zh"], "攻击/护送")
        self.assertEqual(row["map_mode"], "hybrid")
        self.assertEqual(row["instance_type"], "IT_RANKED")

    def test_detail_role_falls_back_to_rank_info(self):
        from owstats.parsers import normalize_detail_player
        row = normalize_detail_player({
            "name": "player#1234",
            "rankInfo": {"roleType": "healer", "rank_name": "Platinum", "rank_sub_tier": 2},
        })
        self.assertEqual(row["role_type"], "healer")
        self.assertEqual(row["role_name_zh"], "支援")
