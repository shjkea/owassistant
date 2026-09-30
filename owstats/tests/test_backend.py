# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from owstats.credential_store import CredentialStore
from owstats.drive_bridge import matches_to_drive_records
from owstats.service import OWStatsService
from owstats.parsers import extract_match_entries, normalize_match, normalize_rank_info, resolve_player_payload
from owstats.presentation import detail_view, profile_view


class BackendParsingTests(unittest.TestCase):
    def test_resolve_player(self):
        p = resolve_player_payload(
            "DemoPlayer#5678",
            {
                "code": 0,
                "data": {
                    "customerToken": "ctoken",
                    "bnetId": "424242420",
                    "name": "DemoPlayer#5678",
                    "icon": "https://example/icon.png",
                },
            },
        )
        self.assertEqual(p.customer_token, "ctoken")
        self.assertEqual(p.bnet_id, "424242420")

    def test_extract_and_normalize_match(self):
        payload = {
            "code": 0,
            "data": {
                "matchList": [
                    {
                        "matchId": "m1",
                        "matchRet": 1,
                        "instanceType": "IT_RANKED",
                        "gameMode": "SportPreset",
                        "kill": 9,
                        "death": 2,
                        "beginTs": 1000,
                    }
                ]
            },
        }
        rows = extract_match_entries(payload)
        self.assertEqual(len(rows), 1)
        n = normalize_match(rows[0])
        self.assertEqual(n["result"], "win")
        self.assertTrue(n["competitive"])
        self.assertEqual(n["kills"], 9)


    def test_profile_presentation_roles(self):
        view = profile_view({
            "resolved": {"full_id": "Demo#1234", "bnet_id": "42"},
            "profile_card": {"data": {"name": "Demo#1234", "level": 88}},
            "sport": {"data": {"guideCountData": [
                {"roleType": "tank", "rankName": "钻石", "rankLevel": 2, "matchCount": 20, "winRate": 0.6},
                {"roleType": "damage", "rankName": "大师", "rankLevel": 5, "matchCount": 10, "winRate": 50},
                {"roleType": "support", "rankName": "宗师", "rankLevel": 3, "winCount": 8, "loseCount": 2},
            ]}},
            "logical_season": 24,
        })
        self.assertEqual(view["ranks"]["tank"]["rank"], "钻石 2")
        self.assertEqual(view["ranks"]["tank"]["win_rate"], 60.0)
        self.assertEqual(view["ranks"]["support"]["games"], 10)

    def test_unranked_hidden_mmr_is_not_converted_to_bronze(self):
        info = normalize_rank_info({"rankScore": 118, "mmr": 118})
        self.assertFalse(info["is_ranked"])
        self.assertEqual(info["rank_name"], "未定级")
        self.assertEqual(info["display_rank"], "未定级")
        self.assertEqual(info["rank_status"], "unranked_hidden_mmr")
        self.assertEqual(info["hidden_mmr"], 118)
        self.assertIsNone(info["rank_sub_tier"])

        view = profile_view({
            "resolved": {"full_id": "Placement#1234"},
            "sport": {"data": {"guideCountData": [
                {"roleType": "tank", "rankScore": 118, "rankLevel": 1, "matchSum": 3}
            ]}},
            "logical_season": 24,
        })
        self.assertEqual(view["ranks"]["tank"]["rank"], "未定级")

    def test_explicit_bronze_remains_bronze(self):
        info = normalize_rank_info({"rank_name": "Bronze", "rank_sub_tier": 2, "rankScore": 118})
        self.assertTrue(info["is_ranked"])
        self.assertEqual(info["display_rank"], "青铜 2")
        self.assertEqual(info["rank_name"], "青铜")
        self.assertIsNone(info["hidden_mmr"])

    def test_profile_presentation_real_dashen_rank_shape(self):
        view = profile_view({
            "resolved": {"full_id": "DemoPlayer#5678", "bnet_id": "424242420"},
            "profile_card": {"data": {"name": "DemoPlayer#5678"}},
            "sport": {"data": {"guideCountData": [
                {
                    "roleType": "healer",
                    "lastRankInfo": {
                        "rank_name": "Platinum",
                        "rank_sub_tier": 5,
                        "rankScore": 395,
                    },
                    "matchSum": 62,
                    "winRate": "53.23",
                }
            ]}},
            "logical_season": 24,
        })
        self.assertEqual(view["ranks"]["support"]["rank"], "白金 5")
        self.assertEqual(view["ranks"]["support"]["games"], 62)
        self.assertAlmostEqual(view["ranks"]["support"]["win_rate"], 53.23)

    def test_detail_presentation_players(self):
        view = detail_view({
            "match_id": "m1",
            "source_match": {"mapName": "国王大道"},
            "detail": {"data": {"playerList": [
                {"playerName": "A", "heroName": "安娜", "roleType": "support", "kill": 3, "assist": 8, "death": 2, "cure": 9000},
                {"playerName": "B", "heroName": "温斯顿", "roleType": "tank", "kill": 12, "assist": 4, "death": 3, "heroDamage": 7000},
            ]}},
        })
        self.assertEqual(view["player_count"], 2)
        self.assertEqual(view["players"][0]["name"], "A")

    def test_reload_credentials_after_external_auth_save(self):
        with tempfile.TemporaryDirectory() as td:
            store = CredentialStore(Path(td) / "cred.json")
            service = OWStatsService(credential_store=store)
            self.assertFalse(service.auth_status()["configured"])
            store.save(store.normalize(123456, "token-value"))
            status = service.reload_credentials()
            self.assertTrue(status["configured"])
            self.assertEqual(service.credential().role_id, 123456)

    def test_drive_bridge_old_to_new(self):
        newest_first = [
            {"matchId": "3", "matchRet": 1, "gameMode": "sport"},
            {"matchId": "2", "matchRet": 0, "gameMode": "sport"},
            {"matchId": "1", "matchRet": 2, "instanceType": "IT_RANKED"},
            {"matchId": "q", "matchRet": 1, "gameMode": "leisure"},
        ]
        records = matches_to_drive_records(newest_first)
        self.assertEqual(records, [
            {"result": "L", "grouped": False, "mode": "normal"},
            {"result": "W", "grouped": False, "mode": "normal"},
        ])


if __name__ == "__main__":
    unittest.main()
