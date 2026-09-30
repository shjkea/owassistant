# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from owstats.service import OWStatsService


class FakeDashenClient:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    @staticmethod
    def _season(season):
        return 24 if season is None else int(season)

    def query_card(self, token):
        if token == "ctB":
            return {"code": 0, "data": {
                "name": "Enemy#2000", "bnetId": 2000, "icon": "https://img/b",
                "title": "对手", "level": 66, "gameTime": "400.0",
            }}
        return {"code": 0, "data": {
            "name": "Demo#1234", "bnetId": 42, "icon": "https://img",
            "title": "纸糊的支援", "level": 88, "gameTime": "833.28",
        }}

    def query_count_info(self, token, game_mode, *, season=None):
        s = self._season(season)
        prefix = f"s{s}-{game_mode}"
        data = {
            "matchList": [
                {"matchId": f"{prefix}-1", "matchRet": 1, "beginTs": s * 100000 + 5000, "gameMode": game_mode},
                {"matchId": f"{prefix}-2", "matchRet": 2, "beginTs": s * 100000 + 4000, "gameMode": game_mode},
            ],
            "presetsHeroUseSummaryList": [
                {"heroGuid": "ana", "heroName": "安娜", "gameTime": 12.5, "matchSum": 8, "winRate": "62.5"},
            ],
            "presetsHeroList": [
                {"heroGuid": "ana", "heroName": "安娜", "kill": 80, "assist": 120, "death": 20, "cure": 90000},
            ],
        }
        if game_mode == "sport":
            data["guideCountData"] = [
                {
                    "roleType": "healer",
                    "matchSum": 62,
                    "winRate": "53.23",
                    "lastRankInfo": {"rank_name": "Platinum", "rank_sub_tier": 5, "rankScore": 395},
                    "maxRankInfo": {"rank_name": "Diamond", "rank_sub_tier": 5, "rankScore": 450},
                }
            ]
        return {"code": 0, "data": data}

    def query_match_list(self, token, game_mode, *, page=1, season=None):
        s = self._season(season)
        prefix = f"s{s}-{game_mode}"
        if page == 1:
            rows = [
                {"matchId": f"{prefix}-1", "matchRet": 1, "beginTs": s * 100000 + 5000, "gameMode": game_mode},
                {"matchId": f"{prefix}-2", "matchRet": 2, "beginTs": s * 100000 + 4000, "gameMode": game_mode},
            ]
        elif page == 2:
            rows = [
                {"matchId": f"{prefix}-3", "matchRet": 1, "beginTs": s * 100000 + 3000, "gameMode": game_mode},
                {"matchId": f"{prefix}-4", "matchRet": 2, "beginTs": s * 100000 + 2000, "gameMode": game_mode},
            ]
        elif page == 3:
            rows = [
                {"matchId": f"{prefix}-5", "matchRet": 1, "beginTs": s * 100000 + 1000, "gameMode": game_mode},
            ]
        else:
            rows = []
        return {"code": 0, "data": {"matchList": rows}}

    def query_match_info(self, token, match_id):
        hero = {
            "heroGuid": "winston" if token == "ctB" else "ana",
            "statMap": {"kill": 12 if token == "ctB" else 3, "assist": 4 if token == "ctB" else 8},
        }
        return {"code": 0, "data": {
            "matchId": match_id,
            "mapName": "国王大道",
            "teamScore": 3,
            "opponentScore": 2,
            "teammateList": [
                {"name": "Demo#1234", "bnetId": 42, "customerToken": "ct", "heroName": "安娜",
                 "heroGuid": "ana", "roleType": "healer", "kill": 3, "assist": 8, "death": 2, "cure": 9000},
            ],
            "enemyList": [
                {"name": "Enemy#2000", "bnetId": 2000, "customerToken": "ctB", "heroName": "温斯顿",
                 "heroGuid": "winston", "roleType": "tank", "kill": 12, "assist": 4, "death": 3, "heroDamage": 7000},
            ],
            "heroList": [hero],
        }}

    def fight_query_count(self, token, *, season=None):
        s = self._season(season)
        return {"code": 0, "data": {
            "roleTypeCountData": [
                {
                    "roleType": "tank",
                    "matchSum": 21,
                    "winRate": "57.14",
                    "lastRankInfo": {"rank_name": "Gold", "rank_sub_tier": 2, "rankScore": 320},
                    "maxRankInfo": {"rank_name": "Platinum", "rank_sub_tier": 5, "rankScore": 350},
                }
            ],
            "heroUseSummaryList": [
                {"heroGuid": "reinhardt", "heroName": "莱因哈特", "matchSum": 12, "winRate": 60},
            ],
            "seasonEcho": s,
        }}

    def fight_query_match_list(self, token, game_mode="SportFight", *, page=1, season=None):
        s = self._season(season)
        prefix = f"s{s}-{game_mode}"
        rows = [] if page > 1 else [
            {"matchId": f"{prefix}-1", "matchRet": 1, "beginTs": s * 100000 + 800, "gameMode": game_mode},
            {"matchId": f"{prefix}-2", "matchRet": 2, "beginTs": s * 100000 + 700, "gameMode": game_mode},
        ]
        return {"code": 0, "data": {"matchList": rows}}

    def fight_query_match_info(self, token, match_id):
        return {"code": 0, "data": {
            "matchId": match_id,
            "roundCountList": [{
                "teammateList": [{"name": "Demo#1234", "bnetId": 42, "kill": 5, "death": 2}],
                "enemyList": [{"name": "Enemy#2000", "bnetId": 2000, "kill": 4, "death": 3}],
            }],
        }}

    def query_province_rank(self, province, role_type):
        return {"code": 0, "data": {"rankList": [
            {"rankNum": 1, "userName": "TopTank", "matchSum": 20, "winRate": 70, "rankScore": 4550},
        ]}}

    def get_hero_billboard(self, province, game_mode, hero_guid):
        return {"code": 0, "data": {"itemList": [
            {"rankNum": 2, "userName": "Tracer", "matchSum": 18, "winRate": 66.6, "rankedLevel": 4500, "sourceMode": game_mode},
        ]}}

    def get_billboard_user(self, customer_token):
        return {"code": 0, "data": {"billboardList": [
            {"rankNum": 9, "userName": "Demo", "matchSum": 12, "winRate": 58.3},
        ]}}


class CompleteBackendTests(unittest.TestCase):
    def make_service(self):
        service = OWStatsService(current_season=24)
        service._new_client = lambda: FakeDashenClient()  # type: ignore[method-assign]
        return service

    def test_stadium_seasons_exposes_roles_heroes_and_raw(self):
        service = self.make_service()
        result = service.stadium_seasons(customer_token="ct", season_count=3)
        self.assertEqual(len(result["seasons"]), 3)
        first = result["seasons"][0]
        self.assertTrue(first["has_stadium"])
        self.assertEqual(first["stadium"]["roles"][0]["role_type"], "tank")
        self.assertEqual(first["stadium"]["heroes"][0]["hero_guid"], "reinhardt")
        self.assertEqual(first["stadium"]["raw"]["data"]["seasonEcho"], 24)

    def test_three_season_complete_pagination(self):
        service = self.make_service()
        result = service.three_season_matches(customer_token="ct")
        self.assertTrue(result["complete"])
        self.assertEqual(result["season_range"], {"start": 22, "end": 24, "count": 3})
        # 3 seasons * (2 standard modes * 5 + 3 fight modes * 2)
        self.assertEqual(result["count"], 48)
        self.assertEqual(result["season_summaries"][0]["modes"]["sport"]["pages"], 4)
        ids = {row["match_id"] for row in result["matches"]}
        self.assertIn("s24-sport-5", ids)
        self.assertIn("s22-leisure-5", ids)
        self.assertIn("s24-SportFight-2", ids)
        self.assertEqual(result["season_summaries"][0]["modes"]["SportFight"]["pages"], 2)
        self.assertEqual(result["included_modes"]["fight"], ["QuickFight", "LeisureFight", "SportFight"])
        self.assertTrue(all(row["season"] in {22, 23, 24} for row in result["matches"]))

    def test_player_seasons_contains_profile_rank_and_hero_data(self):
        service = self.make_service()
        result = service.player_seasons(customer_token="ct", season_count=3)
        self.assertEqual(result["profile_card"]["data"]["name"], "Demo#1234")
        self.assertEqual(len(result["seasons"]), 3)
        role = result["seasons"][0]["competitive"]["roles"][0]
        self.assertEqual(role["role_type"], "healer")
        self.assertEqual(role["current"]["rank_name"], "白金")
        self.assertEqual(role["peak"]["rank_name"], "钻石")
        heroes = result["seasons"][0]["competitive"]["hero_stats"]["heroes"]
        self.assertTrue(any(h["hero_guid"] == "ana" for h in heroes))

    def test_rank_history_arbitrary_range(self):
        service = self.make_service()
        result = service.rank_history(customer_token="ct", start_season=20, end_season=24)
        self.assertEqual([x["season"] for x in result["seasons"]], [24, 23, 22, 21, 20])
        self.assertEqual(result["seasons"][0]["competitive"]["roles"][0]["peak"]["rank_score"], 450)
        self.assertTrue(result["seasons"][0]["has_stadium"])
        self.assertEqual(result["seasons"][0]["stadium"]["roles"][0]["current"]["rank_name"], "黄金")

    def test_match_detail_exposes_raw_and_all_players(self):
        service = self.make_service()
        result = service.match_detail("m1", customer_token="ct")
        self.assertIn("detail", result)
        self.assertEqual(result["normalized"]["player_count"], 2)
        self.assertEqual(result["normalized"]["players"][0]["name"], "Demo#1234")

    def test_all_players_detail_fetches_each_player_hero_list(self):
        service = self.make_service()
        result = service.match_all_players_detail("m1", customer_token="ct", max_workers=2)
        self.assertTrue(result["all_players_supported"])
        self.assertTrue(result["all_players_complete"])
        self.assertEqual(result["player_count"], 2)
        by_token = {row["customer_token"]: row for row in result["players"]}
        self.assertEqual(by_token["ct"]["heroes"][0]["hero_guid"], "ana")
        self.assertEqual(by_token["ctB"]["heroes"][0]["hero_guid"], "winston")
        self.assertEqual(by_token["ctB"]["heroes"][0]["stat_map"]["kill"], 12)

    def test_fight_detail_uses_fight_endpoint_and_does_not_fake_all_players(self):
        service = self.make_service()
        source = {"matchId": "fight-1", "gameMode": "SportFight"}
        detail = service.match_detail("fight-1", customer_token="ct", source_match=source)
        self.assertEqual(detail["match_kind"], "fight")
        self.assertEqual(len(detail["scoreboard_players"]), 2)
        full = service.match_all_players_detail("fight-1", customer_token="ct", source_match=source)
        self.assertFalse(full["all_players_supported"])
        self.assertEqual(full["players"], [])

    def test_leaderboard_interfaces(self):
        service = self.make_service()
        province = service.province_rank("浙江", "输出")
        self.assertEqual(province["role"], "dps")
        self.assertEqual(province["entries"][0]["user_name"], "TopTank")
        hero = service.hero_leaderboard("浙江", "tracer-guid", mode="preset")
        self.assertEqual(hero["mode"], "preset")
        self.assertEqual(hero["source_mode"], "SportPreset")
        self.assertEqual(hero["entries"][0]["rank_num"], 2)
        self.assertEqual(hero["entries"][0]["raw"]["sourceMode"], "SportPreset")
        user = service.user_billboard(customer_token="ct")
        self.assertEqual(user["entries"][0]["rank_num"], 9)

    def test_sameplay_intersection(self):
        service = self.make_service()
        original = service.match_history

        def fake_history(battletag=None, *, customer_token=None, **kwargs):
            tag = battletag or customer_token
            if tag == "p1":
                rows = [
                    {"matchId": "a", "matchRet": 1, "beginTs": 300, "gameMode": "SportPreset", "_dashenSeason": 24},
                    {"matchId": "b", "matchRet": 2, "beginTs": 200, "gameMode": "leisure", "_dashenSeason": 24},
                ]
            else:
                rows = [
                    {"matchId": "a", "matchRet": 2, "beginTs": 300, "gameMode": "SportPreset", "_dashenSeason": 24},
                    {"matchId": "c", "matchRet": 1, "beginTs": 100, "gameMode": "leisure", "_dashenSeason": 24},
                ]
            return {"resolved": {"query": tag}, "count": len(rows), "complete": True, "warnings": [], "raw_matches": rows}

        service.match_history = fake_history  # type: ignore[method-assign]
        result = service.sameplay_records("p1", "p2")
        service.match_history = original  # type: ignore[method-assign]
        self.assertEqual(result["summary"]["total_common_count"], 1)
        self.assertEqual(result["matches"][0]["match_id"], "a")
        self.assertEqual(result["summary"]["competitive_count"], 1)


if __name__ == "__main__":
    unittest.main()

class AutoSeasonDetectionTests(unittest.TestCase):
    class FakeDashenClient25(FakeDashenClient):
        @staticmethod
        def _season(season):
            return 25 if season is None else int(season)

    def test_default_service_detects_next_season_and_rolls_three_seasons(self):
        service = OWStatsService()
        service._new_client = lambda: self.FakeDashenClient25()  # type: ignore[method-assign]
        result = service.player_seasons(customer_token="ct", season_count=3)
        self.assertEqual(result["current_season"], 25)
        self.assertEqual(result["season_source"], "matched_current_payload")
        self.assertEqual([row["season"] for row in result["seasons"]], [25, 24, 23])
        self.assertEqual(service.season_status()["current_season"], 25)
