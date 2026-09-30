# -*- coding: utf-8 -*-
"""本轮增量的纯逻辑测试（无 Qt 依赖）。

覆盖：
- 当前赛季最近最多 20 场（预设竞技 + 开放竞技 + 快速合并）的候选、排序、截取、胜率；
- 竞技队列分类（预设 / 开放）与筛选；
- 详情队内排序（重装恒第一 + 组排连续）；
- 本人识别（稳定账号 ID 优先，BattleTag 仅兜底）；
- 缓存路径安全与占用统计。

运行：python -m pytest tests -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui import owstats_data as D  # noqa: E402


def mk(
    match_id: str,
    season: int,
    begin_ts: int,
    filter_key: str,
    result: str,
    *,
    game_mode: str = "",
    queue_kind: str = "preset",
    role_type: str = "dps",
):
    """构造一条已归一化的 match_view（字段与 D.match_view 输出一致的子集）。"""
    return {
        "match_id": match_id,
        "season": season,
        "begin_ts": begin_ts,
        "filter_key": filter_key,
        "result": result,
        "game_mode": game_mode or filter_key,
        "game_mode_zh": "",
        "instance_type": "",
        "instance_type_zh": "",
        "source_mode": "",
        "queue_kind": queue_kind,
        "role_type": role_type,
    }


class RecentFormTest(unittest.TestCase):
    def test_takes_newest_20_of_30(self):
        rows = [
            mk(f"m{i}", 24, 1000 + i, "sport" if i % 2 else "leisure", "win" if i % 3 else "loss")
            for i in range(30)
        ]
        summary = D.recent_form_summary(rows, 24)
        self.assertEqual(summary["available"], 30)
        self.assertEqual(summary["total"], 20)
        self.assertEqual(
            [m["match_id"] for m in summary["matches"]],
            [f"m{i}" for i in range(10, 30)],
        )

    def test_uses_actual_count_when_fewer_than_limit(self):
        rows = [mk(f"m{i}", 24, 1000 + i, "sport", "win" if i < 8 else "loss") for i in range(13)]
        summary = D.recent_form_summary(rows, 24)
        self.assertEqual(summary["total"], 13)
        self.assertEqual(summary["available"], 13)
        self.assertEqual(summary["wins"], 8)
        self.assertEqual(summary["losses"], 5)
        self.assertAlmostEqual(summary["win_rate"], 8 * 100.0 / 13)
        self.assertEqual(D.recent_form_record_text(summary), "8胜 · 5负")
        self.assertEqual(D.recent_form_rate_text(summary), "61.5%")

    def test_preset_open_and_quick_all_enter_candidates(self):
        rows = [
            mk("p", 24, 10, "sport", "win", game_mode="SportPreset", queue_kind="preset"),
            mk("o", 24, 11, "sport", "loss", game_mode="SportOpen", queue_kind="open",
               role_type="open"),
            mk("q", 24, 12, "leisure", "win", game_mode="leisure", queue_kind=""),
        ]
        summary = D.recent_form_summary(rows, 24)
        self.assertEqual(summary["total"], 3)
        self.assertEqual([m["match_id"] for m in summary["matches"]], ["p", "o", "q"])

    def test_quick_between_competitive_participates_normally(self):
        rows = [
            mk("c1", 24, 100, "sport", "win"),
            mk("q1", 24, 150, "leisure", "loss"),
            mk("c2", 24, 200, "sport", "win"),
        ]
        summary = D.recent_form_summary(rows, 24)
        self.assertEqual([m["match_id"] for m in summary["matches"]], ["c1", "q1", "c2"])
        self.assertEqual(summary["total"], 3)
        self.assertAlmostEqual(summary["win_rate"], 200.0 / 3)

    def test_custom_and_irrelevant_modes_excluded(self):
        rows = [
            mk("c1", 24, 100, "sport", "win"),
            mk("custom", 24, 110, "other", "win", game_mode="CustomGame"),
            mk("train", 24, 120, "other", "win", game_mode="Training"),
            mk("arcade", 24, 130, "other", "win", game_mode="Arcade"),
            mk("fight", 24, 140, "SportFight", "win", game_mode="SportFight"),
            mk("fightq", 24, 145, "fight_quick", "win", game_mode="QuickFight"),
            mk("q1", 24, 150, "leisure", "loss"),
        ]
        summary = D.recent_form_summary(rows, 24)
        self.assertEqual([m["match_id"] for m in summary["matches"]], ["c1", "q1"])

    def test_never_borrow_from_previous_season(self):
        rows = [mk(f"cur{i}", 24, 1000 + i, "sport", "win") for i in range(10)]
        rows += [mk(f"old{i}", 23, 500 + i, "sport", "win") for i in range(50)]
        summary = D.recent_form_summary(rows, 24)
        self.assertEqual(summary["total"], 10)
        self.assertTrue(all(m["season"] == 24 for m in summary["matches"]))

    def test_draw_counts_in_denominator(self):
        rows = [mk(f"w{i}", 24, 100 + i, "sport", "win") for i in range(12)]
        rows += [mk(f"l{i}", 24, 200 + i, "leisure", "loss") for i in range(7)]
        rows += [mk("d0", 24, 300, "sport", "draw")]
        summary = D.recent_form_summary(rows, 24)
        self.assertEqual(summary["total"], 20)
        self.assertEqual(summary["draws"], 1)
        self.assertAlmostEqual(summary["win_rate"], 60.0)
        self.assertEqual(D.recent_form_record_text(summary), "12胜 · 7负 · 1平")

    def test_no_zero_draw_text(self):
        rows = [mk(f"w{i}", 24, 100 + i, "sport", "win") for i in range(13)]
        rows += [mk(f"l{i}", 24, 200 + i, "sport", "loss") for i in range(7)]
        summary = D.recent_form_summary(rows, 24)
        self.assertEqual(D.recent_form_record_text(summary), "13胜 · 7负")
        self.assertNotIn("0平", D.recent_form_record_text(summary))

    def test_display_order_is_oldest_to_newest(self):
        rows = [mk(f"m{i}", 24, 1000 - i, "sport", "win") for i in range(25)]
        summary = D.recent_form_summary(rows, 24)
        ts = [m["begin_ts"] for m in summary["matches"]]
        self.assertEqual(ts, sorted(ts))
        # 最右一格必须是时间最新的一场
        self.assertEqual(summary["matches"][-1]["begin_ts"], max(r["begin_ts"] for r in rows))

    def test_empty_never_reports_zero_percent(self):
        summary = D.recent_form_summary([], 24)
        self.assertEqual(summary["total"], 0)
        self.assertIsNone(summary["win_rate"])
        self.assertEqual(D.recent_form_rate_text(summary), "")
        self.assertEqual(D.recent_form_record_text(summary), "")

    def test_unknown_season_yields_nothing(self):
        rows = [mk("m0", 24, 100, "sport", "win")]
        self.assertEqual(D.recent_form_summary(rows, None)["total"], 0)


class CompetitiveClassificationTest(unittest.TestCase):
    def test_queue_kind_from_backend_fields(self):
        self.assertEqual(
            D.competitive_queue_kind({"filter_key": "sport", "source_mode": "sport",
                                      "game_mode": "SportPreset"}),
            "preset",
        )
        self.assertEqual(
            D.competitive_queue_kind({"filter_key": "sport", "source_mode": "sport",
                                      "game_mode": "SportOpen"}),
            "open",
        )
        # 中文展示名也能识别
        self.assertEqual(
            D.competitive_queue_kind({"filter_key": "sport", "source_mode": "sport",
                                      "game_mode": "sport", "game_mode_zh": "竞技 · 开放职责"}),
            "open",
        )
        # 快速 / 角斗不属于标准竞技
        self.assertEqual(D.competitive_queue_kind({"source_mode": "leisure"}), "")
        self.assertEqual(D.competitive_queue_kind({"source_mode": "SportFight"}), "")

    def test_queue_kind_falls_back_to_role(self):
        self.assertEqual(
            D.competitive_queue_kind({"filter_key": "sport", "source_mode": "sport",
                                      "game_mode": "sport", "role_type": "open"}),
            "open",
        )
        self.assertEqual(
            D.competitive_queue_kind({"filter_key": "sport", "source_mode": "sport",
                                      "game_mode": "sport", "role_type": "tank"}),
            "preset",
        )

    def test_filter_matches_preset_open_and_role(self):
        rows = [
            mk("t", 24, 1, "sport", "win", queue_kind="preset", role_type="tank"),
            mk("d", 24, 2, "sport", "win", queue_kind="preset", role_type="dps"),
            mk("h", 24, 3, "sport", "win", queue_kind="preset", role_type="healer"),
            mk("o", 24, 4, "sport", "win", queue_kind="open", role_type="open"),
            mk("q", 24, 5, "leisure", "win", queue_kind=""),
        ]
        self.assertEqual(len(D.filter_matches(rows, filter_key="sport")), 4)
        presets = D.filter_matches(rows, filter_key="sport_preset")
        self.assertEqual([r["match_id"] for r in presets], ["t", "d", "h"])
        opens = D.filter_matches(rows, filter_key="sport_open")
        self.assertEqual([r["match_id"] for r in opens], ["o"])
        for role, expect in (("tank", ["t"]), ("dps", ["d"]), ("healer", ["h"])):
            got = D.filter_matches(rows, filter_key="sport_preset", preset_role=role)
            self.assertEqual([r["match_id"] for r in got], expect)
        self.assertEqual(len(D.filter_matches(rows, filter_key="leisure")), 1)


def player(name: str, role: str, friends=()):
    return {"name": name, "bnet_id": name, "role_type": role, "friend_bnet_ids": list(friends)}


class TeamOrderTest(unittest.TestCase):
    def test_solo_team_order(self):
        layout = D.build_team_layout([
            player("d1", "dps"), player("s1", "healer"), player("t", "tank"),
            player("d2", "dps"), player("s2", "healer"),
        ])
        self.assertEqual(
            [p["role_type"] for p in layout.players],
            ["tank", "dps", "dps", "healer", "healer"],
        )
        self.assertEqual([p["name"] for p in layout.players], ["t", "d1", "d2", "s1", "s2"])
        self.assertTrue(all(pid is None for pid in layout.party_ids))

    def test_dps_support_duo_stays_adjacent(self):
        layout = D.build_team_layout([
            player("t", "tank"), player("dA", "dps"), player("dB", "dps", ["sA"]),
            player("sA", "healer", ["dB"]), player("sB", "healer"),
        ])
        names = [p["name"] for p in layout.players]
        self.assertEqual(names[0], "t")
        self.assertEqual(abs(names.index("dB") - names.index("sA")), 1)
        self.assertEqual(
            layout.party_ids[names.index("dB")], layout.party_ids[names.index("sA")]
        )
        self.assertIsNone(layout.party_ids[names.index("dA")])

    def test_tank_support_duo_keeps_tank_first(self):
        layout = D.build_team_layout([
            player("dA", "dps"), player("t", "tank", ["sA"]), player("sA", "healer", ["t"]),
            player("dB", "dps"), player("sB", "healer"),
        ])
        names = [p["name"] for p in layout.players]
        self.assertEqual(names[0], "t")
        self.assertEqual(names[1], "sA")

    def test_tank_first_for_trio_and_quad(self):
        for friends in (("sA", "dB"), ("sA", "dB", "sB")):
            rows = [
                player("dA", "dps"),
                player("t", "tank", friends),
                player("dB", "dps", [x for x in ("t", *friends) if x != "dB"]),
                player("sA", "healer", [x for x in ("t", *friends) if x != "sA"]),
                player("sB", "healer", [x for x in ("t", *friends) if x != "sB"]),
            ]
            layout = D.build_team_layout(rows)
            names = [p["name"] for p in layout.players]
            self.assertEqual(names[0], "t", friends)
            members = {"t", *friends}
            idx = sorted(names.index(m) for m in members)
            self.assertEqual(idx, list(range(idx[0], idx[0] + len(members))), friends)
            self.assertEqual(idx[0], 0, friends)

    def test_tank_first_even_when_others_are_grouped(self):
        # 重装单排，其余四人组成两两双排：重装仍须第一。
        layout = D.build_team_layout([
            player("dA", "dps", ["sA"]), player("sA", "healer", ["dA"]),
            player("dB", "dps", ["sB"]), player("sB", "healer", ["dB"]),
            player("t", "tank"),
        ])
        names = [p["name"] for p in layout.players]
        self.assertEqual(names[0], "t", names)
        self.assertEqual(abs(names.index("dA") - names.index("sA")), 1, names)
        self.assertEqual(abs(names.index("dB") - names.index("sB")), 1, names)

    def test_unknown_role_goes_last(self):
        layout = D.build_team_layout([
            player("x", ""), player("t", "tank"), player("d", "dps"), player("s", "healer"),
        ])
        self.assertEqual([p["name"] for p in layout.players], ["t", "d", "s", "x"])


class SelfIdentityTest(unittest.TestCase):
    """本人识别只依据完整 BattleTag（含 #、两侧非空、忽略大小写）确定性匹配。

    customer_token / bnet_id / accountId / playerId 等稳定 ID 一律不参与判定：
    ID 相同不能在 BattleTag 缺失或不同时判为本人，ID 不同也不能否决 BattleTag 相同。
    """

    def test_same_customer_token_without_self_battle_tag_is_not_self(self):
        row = {"customer_token": "ct-1", "name": "Foo#1"}
        self.assertFalse(D.is_self_player(row, self_token="ct-1"))
        self.assertFalse(D.is_self_player(row, self_identity={"customer_token": "ct-1"}))

    def test_same_bnet_id_without_self_battle_tag_is_not_self(self):
        row = {"bnet_id": "42", "name": "Foo#1"}
        self.assertFalse(D.is_self_player(row, self_bnet_id="42"))
        self.assertFalse(D.is_self_player(row, self_identity={"bnet_id": "42"}))

    def test_token_mismatch_does_not_veto_same_battle_tag(self):
        row = {"customer_token": "ct-1", "name": "Foo#1"}
        self.assertTrue(D.is_self_player(row, self_token="ct-2", self_battle_tag="Foo#1"))
        self.assertTrue(
            D.is_self_player(row, self_identity={"customer_token": "ct-2", "battle_tag": "Foo#1"})
        )

    def test_bnet_id_mismatch_does_not_veto_same_battle_tag(self):
        row = {"bnet_id": "42", "name": "Foo#1"}
        self.assertTrue(D.is_self_player(row, self_bnet_id="43", self_battle_tag="Foo#1"))
        self.assertTrue(
            D.is_self_player(row, self_identity={"bnet_id": "43", "battle_tag": "Foo#1"})
        )

    def test_battle_tag_is_case_insensitive(self):
        self.assertTrue(D.is_self_player({"name": "foo#1"}, self_battle_tag="FOO#1"))
        self.assertTrue(D.is_self_player({"name": "player#1234"}, self_battle_tag="player#1234"))

    def test_surrounding_whitespace_is_stripped(self):
        # 当前实现对取到的 BattleTag 做 strip()：前后空白不影响匹配。
        self.assertTrue(D.is_self_player({"name": "  Foo#1 "}, self_battle_tag=" foo#1  "))
        self.assertTrue(
            D.is_self_player({"name": "player#1234"}, self_identity={"full_id": " player#1234 "})
        )

    def test_plain_nickname_without_hash_is_not_self(self):
        self.assertFalse(D.is_self_player({"name": "Foo"}, self_battle_tag="Foo"))
        self.assertFalse(D.is_self_player({"name": "Foo#1"}, self_battle_tag="Foo"))
        self.assertFalse(D.is_self_player({"name": "Foo"}, self_battle_tag="Foo#1"))

    def test_empty_side_of_hash_is_not_self(self):
        for tag in ("#1", "Foo#", "#"):
            self.assertFalse(D.is_self_player({"name": tag}, self_battle_tag=tag), tag)

    def test_different_battle_tag_is_not_self_even_if_ids_match(self):
        row = {"customer_token": "ct-1", "bnet_id": "42", "name": "Foo#1"}
        self.assertFalse(
            D.is_self_player(row, self_token="ct-1", self_bnet_id="42", self_battle_tag="Bar#2")
        )
        self.assertFalse(D.is_self_player(row, self_identity={
            "customer_token": "ct-1", "bnet_id": "42", "battle_tag": "Bar#2",
        }))

    def test_missing_player_battle_tag_is_not_self(self):
        self.assertFalse(D.is_self_player({}, self_battle_tag="Foo#1"))
        self.assertFalse(D.is_self_player({"customer_token": "ct-1"}, self_token="ct-1",
                                          self_battle_tag="Foo#1"))


class CacheManagerTest(unittest.TestCase):
    def setUp(self):
        from ui import cache_manager as CM

        self.CM = CM

    def test_assets_and_parents_are_never_deletable(self):
        from ui.assets import ASSETS_DIR

        CM = self.CM
        self.assertFalse(CM.is_safe_cache_path(ASSETS_DIR))
        for sub in ("heroes", "maps", "ranks", "roles", "map_modes"):
            self.assertFalse(CM.is_safe_cache_path(ASSETS_DIR / sub))
        self.assertFalse(CM.is_safe_cache_path(CM.PROJECT_CACHE_DIR.parent))
        self.assertFalse(CM.is_safe_cache_path(ROOT / "config.json"))
        self.assertFalse(CM.is_safe_cache_path(ROOT / "drive_core.py"))

    def test_cache_dirs_are_deletable(self):
        CM = self.CM
        self.assertTrue(CM.is_safe_cache_path(CM.detail_cache_dir()))
        self.assertTrue(CM.is_safe_cache_path(CM.avatar_cache_dir()))
        self.assertTrue(CM.is_safe_cache_path(CM.avatar_cache_dir() / "x.png"))

    def test_missing_dir_is_zero_and_never_raises(self):
        CM = self.CM
        missing = CM.PROJECT_CACHE_DIR / "definitely_missing_dir"
        self.assertEqual(CM.dir_size(missing), 0)
        self.assertEqual(CM.format_size(0), "0 B")
        usage = CM.collect_usage()
        self.assertGreaterEqual(usage.total, 0)
        self.assertEqual(usage.total, usage.detail + usage.avatar + usage.other)

    def test_detail_cache_dir_is_shared_with_detail_cache_module(self):
        from ui.detail_cache import MatchDetailCache, default_cache_root

        self.assertEqual(self.CM.detail_cache_dir(), default_cache_root())
        self.assertEqual(MatchDetailCache().root, default_cache_root())

    def test_avatar_cache_dir_inside_project_cache(self):
        CM = self.CM
        self.assertEqual(CM.avatar_cache_dir().parent, CM.PROJECT_CACHE_DIR)
        self.assertEqual(CM.PROJECT_CACHE_DIR.name, "cache")


if __name__ == "__main__":
    unittest.main()
