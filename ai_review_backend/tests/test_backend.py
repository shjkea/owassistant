from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_review import (
    AIReviewService,
    DeepSeekClient,
    MatchReviewContext,
    ReviewCache,
    ReviewRequest,
    ReviewScope,
    ReviewStyle,
    SelfPlayerNotFoundError,
    TargetSelectionError,
)


def sample_detail():
    friendly = [
        {
            "name": "SelfPlayer#12345", "bnet_id": "12345", "customer_token": "ct-self",
            "team_type": "teammate", "role_type": "tank", "hero_guid": "winston",
            "hero_name": "温斯顿", "kills": 18, "deaths": 4, "assists": 7,
            "hero_damage": 9000, "healing": 0, "resist_damage": 10500,
            "damage_taken": 12000, "healing_taken": 8200, "final_hits": 7,
            "rank_info": {"display_rank": "翡翠 2", "is_ranked": True},
            "friend_bnet_ids": [],
        },
        {
            "name": "DpsA#1002", "bnet_id": "1002", "team_type": "teammate", "role_type": "dps",
            "hero_guid": "ashe", "hero_name": "艾什", "kills": 24, "deaths": 5, "assists": 4,
            "hero_damage": 13200, "friend_bnet_ids": ["1003"],
        },
        {
            "name": "SupA#1003", "bnet_id": "1003", "team_type": "teammate", "role_type": "healer",
            "hero_guid": "ana", "hero_name": "安娜", "kills": 7, "deaths": 4, "assists": 18,
            "hero_damage": 3500, "healing": 12800, "friend_bnet_ids": ["1002"],
        },
    ]
    enemy = [
        {
            "name": "EnemyTank#2001", "bnet_id": "2001", "team_type": "enemy", "role_type": "tank",
            "hero_guid": "dva", "hero_name": "D.Va", "kills": 15, "deaths": 7, "assists": 6,
            "hero_damage": 8100, "resist_damage": 9000,
        }
    ]
    extras = [
        {
            "name": "SelfPlayer#12345", "bnet_id": "12345", "customer_token": "ct-self",
            "hero_list": [
                {"hero_guid": "winston", "hero_name": "温斯顿", "play_time": 500},
                {"hero_guid": "dva", "hero_name": "D.Va", "play_time": 120},
            ],
        }
    ]
    return {
        "match_id": "MATCH-1",
        "scoreboard_players": friendly + enemy,
        "players": extras,
        "detail_root": {
            "teamScore": 3,
            "opponentScore": 2,
            "gameMode": "SportPreset",
            "teammateBanHeroList": [{"heroGuid": "ana", "heroName": "安娜"}],
            "enemyBanHeroList": [{"heroGuid": "kiriko", "heroName": "雾子"}],
        },
    }


class FakeClient:
    def __init__(self):
        self.calls = 0

    def set_api_key(self, _key):
        pass

    def test_connection(self):
        return {"id": "deepseek-flash", "name": "DeepSeek-V4.1-Flash"}

    def chat(self, messages, *, reasoning_effort, max_tokens):
        self.calls += 1
        assert messages[0]["role"] == "system"
        return "测试点评", {"prompt_tokens": 100, "completion_tokens": 20}, "stop"

    def stream_chat(self, messages, *, reasoning_effort, max_tokens, on_delta=None, on_status=None, cancel_token=None):
        self.calls += 1
        if on_status:
            on_status("thinking")
            on_status("answering")
        for piece in ("测试", "流式", "点评"):
            if on_delta:
                on_delta(piece)
        if on_status:
            on_status("done")
        return "测试流式点评", {"total_tokens": 123}, "stop"


class AdapterTests(unittest.TestCase):
    def test_prepare_match_default_self_and_multihhero(self):
        service = AIReviewService(client=FakeClient(), enable_cache=False)
        context = service.prepare_match(
            sample_detail(),
            match_meta={
                "match_id": "MATCH-1", "result": "win", "map_name": "国王大道",
                "map_mode_zh": "混合", "game_mode_zh": "竞技 · 预设职责", "game_time_sec": 720,
            },
            self_identity={"battle_tag": "SelfPlayer#12345", "bnet_id": "12345"},
        )
        self.assertEqual(context.match.map_name, "国王大道")
        self.assertEqual(context.match.result, "win")
        self.assertEqual(context.self_player.battle_tag, "SelfPlayer#12345")
        self.assertEqual(len(context.self_player.heroes), 2)
        self.assertEqual(context.players[1].party_id, context.players[2].party_id)
        self.assertIsNotNone(context.players[1].party_id)
        ui = service.players_for_ui(context)
        self.assertTrue(ui[0]["is_self"])
        self.assertEqual(ui[0]["hero_name"], "温斯顿")

    def test_self_scope_requires_self(self):
        service = AIReviewService(client=FakeClient(), enable_cache=False)
        context = service.prepare_match(sample_detail())
        with self.assertRaises(SelfPlayerNotFoundError):
            service.validate_request(context, ReviewRequest(scope="self"))

    def test_selected_scope_validates_ids(self):
        service = AIReviewService(client=FakeClient(), enable_cache=False)
        context = service.prepare_match(sample_detail(), self_identity="SelfPlayer#12345")
        with self.assertRaises(TargetSelectionError):
            service.validate_request(context, ReviewRequest(scope="selected", target_player_ids=("nope",)))
        ids = service.validate_request(context, ReviewRequest(scope="selected", target_player_ids=("p01", "p04")))
        self.assertEqual(ids, ["p01", "p04"])

    def test_six_v_six_is_supported_without_incomplete_warning(self):
        detail = sample_detail()
        base = list(detail["scoreboard_players"])
        # 补到 6v6，共 12 人；player_id 应自然扩展到 p12。
        for i in range(4, 7):
            base.append({
                "name": f"Friendly{i}#10{i:02d}",
                "bnet_id": f"10{i:02d}",
                "team_type": "teammate",
                "role_type": "dps" if i % 2 == 0 else "healer",
                "hero_name": "测试英雄",
                "kills": i, "deaths": 2, "assists": 3,
            })
        for i in range(2, 7):
            base.append({
                "name": f"Enemy{i}#20{i:02d}",
                "bnet_id": f"20{i:02d}",
                "team_type": "enemy",
                "role_type": "dps" if i % 2 == 0 else "healer",
                "hero_name": "测试英雄",
                "kills": i, "deaths": 3, "assists": 2,
            })
        self.assertEqual(len(base), 12)
        detail["scoreboard_players"] = base
        detail["player_count"] = 12
        detail["all_players_complete"] = True

        service = AIReviewService(client=FakeClient(), enable_cache=False)
        context = service.prepare_match(detail, self_identity="SelfPlayer#12345")
        self.assertEqual(len(context.players), 12)
        self.assertEqual(context.players[-1].player_id, "p12")
        self.assertFalse(any("不完整" in w for w in context.data_warnings))

    def test_completeness_warning_uses_upstream_expected_count(self):
        detail = sample_detail()
        detail["player_count"] = 12
        detail["all_players_complete"] = False
        service = AIReviewService(client=FakeClient(), enable_cache=False)
        context = service.prepare_match(detail, self_identity="SelfPlayer#12345")
        self.assertTrue(any("4/12" in w for w in context.data_warnings))

    def test_prompt_does_not_send_internal_account_tokens(self):
        service = AIReviewService(client=FakeClient(), enable_cache=False)
        context = service.prepare_match(sample_detail(), self_identity="SelfPlayer#12345")
        messages = service.preview_messages(context, ReviewRequest(style="coach", scope="self"))
        joined = json.dumps(messages, ensure_ascii=False)
        self.assertNotIn("ct-self", joined)
        self.assertNotIn('"bnet_id"', joined)
        self.assertIn("SelfPlayer#12345", joined)
        self.assertIn("不得虚构录像级事实", joined)


def _full_match(per_team: int):
    """构造一局 per_team v per_team（共 2*per_team 人）的详情；第一名我方玩家为本人。"""
    rows = []
    for side, team_type in (("F", "teammate"), ("E", "enemy")):
        for i in range(per_team):
            rows.append({
                "name": "SelfPlayer#12345" if (side == "F" and i == 0) else f"{side}Player{i}#{3000 + i}",
                "bnet_id": f"{side}{i}", "customer_token": f"ct-{side}{i}",
                "team_type": team_type, "role_type": "dps", "hero_name": "测试英雄",
                "kills": 5, "deaths": 5, "assists": 5,
            })
    return {"match_id": f"M{per_team}", "scoreboard_players": rows, "players": [],
            "player_count": len(rows), "all_players_complete": True}


class SelfScopePromptCountTests(unittest.TestCase):
    """self 模式 Prompt 的「其他 N 人」必须按实际人数动态生成。"""

    def _self_prompt(self, per_team: int) -> str:
        service = AIReviewService(client=FakeClient(), enable_cache=False)
        context = service.prepare_match(_full_match(per_team), self_identity="SelfPlayer#12345")
        self.assertEqual(len(context.players), per_team * 2)
        messages = service.preview_messages(context, ReviewRequest(style="coach", scope="self"))
        return messages[1]["content"]

    def test_5v5_self_prompt_other_9(self):
        text = self._self_prompt(5)
        self.assertIn("其他 9 人", text)
        self.assertNotIn("其他 11 人", text)

    def test_6v6_self_prompt_other_11(self):
        text = self._self_prompt(6)
        self.assertIn("其他 11 人", text)
        self.assertNotIn("其他 9 人", text)

    def test_no_fixed_player_count_left_in_scope_text(self):
        from ai_review.prompts import _scope_instruction

        self.assertIn("其他 3 人", _scope_instruction(ReviewScope.SELF, ["p01"], 4))
        # 异常人数不产生负数或「其他 0 人」
        for count in (0, 1):
            text = _scope_instruction(ReviewScope.SELF, ["p01"], count)
            self.assertNotIn("-", text.split("；")[1].split("仅")[0])
            self.assertNotIn("其他 0 人", text)
            self.assertIn("其他玩家", text)
        # selected / global 不涉及人数
        self.assertNotIn("其他 9 人", _scope_instruction(ReviewScope.SELECTED, ["p01"], 10))
        self.assertNotIn("其他 9 人", _scope_instruction(ReviewScope.GLOBAL, [], 10))


class SelfIdentityBattleTagOnlyTests(unittest.TestCase):
    """本人识别只按完整 BattleTag（与主项目 is_self_player 一致）。"""

    SELF = {"battle_tag": "本人#1000", "customer_token": "token-self", "bnet_id": "1000"}

    def _ctx(self, rows, self_identity=None):
        service = AIReviewService(client=FakeClient(), enable_cache=False)
        detail = {"scoreboard_players": rows, "players": []}
        return service.prepare_match(detail, self_identity=self_identity or self.SELF)

    @staticmethod
    def _row(name, token, bnet_id, team="teammate"):
        return {"name": name, "customer_token": token, "bnet_id": bnet_id,
                "team_type": team, "role_type": "tank", "hero_name": "温斯顿"}

    def test_scenario_a_same_battle_tag_different_ids_is_self(self):
        ctx = self._ctx([self._row("本人#1000", "other-token", "9999")])
        self.assertTrue(ctx.players[0].is_self)

    def test_scenario_b_same_token_and_bnet_id_but_other_tag_is_not_self(self):
        ctx = self._ctx([
            self._row("本人#1000", "other-token", "9999"),
            self._row("其他人#2000", "token-self", "1000"),
        ])
        self.assertTrue(ctx.players[0].is_self)
        self.assertFalse(ctx.players[1].is_self)
        self.assertEqual([p.player_id for p in ctx.players if p.is_self], ["p01"])

    def test_only_token_matches_is_not_self(self):
        ctx = self._ctx([self._row("其他人#2000", "token-self", "5555")])
        self.assertFalse(ctx.players[0].is_self)

    def test_only_bnet_id_matches_is_not_self(self):
        ctx = self._ctx([self._row("其他人#2000", "x-token", "1000")])
        self.assertFalse(ctx.players[0].is_self)

    def test_scenario_c_case_insensitive(self):
        ctx = self._ctx([self._row("PLAYER#1234", "a", "b")], {"battle_tag": "player#1234"})
        self.assertTrue(ctx.players[0].is_self)

    def test_scenario_d_nickname_without_hash_is_not_self(self):
        ctx = self._ctx([self._row("player", "token-self", "1000")],
                        {"battle_tag": "player", "customer_token": "token-self", "bnet_id": "1000"})
        self.assertFalse(ctx.players[0].is_self)

    def test_missing_or_incomplete_battle_tag_is_not_self(self):
        for tag in ("", "本人#", "#1000", "本人"):
            ctx = self._ctx([self._row(tag or "", "token-self", "1000")])
            self.assertFalse(ctx.players[0].is_self, tag)
        # 本人侧缺完整 BattleTag：即使 token / bnet_id 相同也不是本人
        ctx = self._ctx([self._row("本人#1000", "token-self", "1000")],
                        {"battle_tag": "", "customer_token": "token-self", "bnet_id": "1000"})
        self.assertFalse(ctx.players[0].is_self)

    def test_no_prefix_or_suffix_match(self):
        for tag in ("本人#10000", "本人#100", "本人X#1000", "本#1000"):
            ctx = self._ctx([self._row(tag, "a", "b")])
            self.assertFalse(ctx.players[0].is_self, tag)

    def test_twelve_players_single_self_with_full_identity(self):
        """12 人对局 + 前端同时传入 battle_tag / bnet_id / customer_token：本人唯一。"""
        detail = _full_match(6)
        rows = detail["scoreboard_players"]
        # 另一名玩家故意持有本人的 token 与 bnet_id（对应前端冒烟数据中的「玩家D」）
        rows[3]["customer_token"] = "ct-F0"
        rows[3]["bnet_id"] = "F0"
        detail["players"] = [dict(rows[3], heroes=[{"hero_name": "测试英雄"}])]
        service = AIReviewService(client=FakeClient(), enable_cache=False)
        ctx = service.prepare_match(detail, self_identity={
            "battle_tag": "SelfPlayer#12345", "bnet_id": "F0", "customer_token": "ct-F0",
        })
        self.assertEqual(len(ctx.players), 12)
        ui = service.players_for_ui(ctx)
        self.assertEqual([p["player_id"] for p in ui if p["is_self"]], ["p01"])
        self.assertEqual(service.validate_request(ctx, ReviewRequest(scope="self")), ["p01"])
        self.assertIn("其他 11 人", service.preview_messages(ctx, ReviewRequest(scope="self"))[1]["content"])


class ClientParserTests(unittest.TestCase):
    def test_sse_parser(self):
        lines = [
            b"\n",
            b'data: {"choices":[{"delta":{"reasoning_content":"hidden"},"finish_reason":null}],"usage":null}\n',
            b'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}],"usage":null}\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"total_tokens":12}}\n',
            b"data: [DONE]\n",
        ]
        chunks = list(DeepSeekClient.iter_sse_json(lines))
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[1]["choices"][0]["delta"]["content"], "hello")
        self.assertEqual(chunks[2]["usage"]["total_tokens"], 12)


class ServiceTests(unittest.TestCase):
    def test_stream_and_cache(self):
        fake = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            service = AIReviewService(client=fake, cache=ReviewCache(Path(tmp)), enable_cache=True)
            context = service.prepare_match(sample_detail(), self_identity="SelfPlayer#12345")
            req = ReviewRequest(style=ReviewStyle.ROAST, scope=ReviewScope.SELF)
            deltas = []
            result1 = service.stream_review(context, req, on_delta=deltas.append)
            self.assertEqual(result1.text, "测试流式点评")
            self.assertFalse(result1.cached)
            self.assertEqual(fake.calls, 1)
            self.assertEqual("".join(deltas), "测试流式点评")

            deltas2 = []
            result2 = service.stream_review(context, req, on_delta=deltas2.append)
            self.assertTrue(result2.cached)
            self.assertEqual(fake.calls, 1)
            self.assertEqual("".join(deltas2), "测试流式点评")

    def test_global_uses_high_effort_path(self):
        service = AIReviewService(client=FakeClient(), enable_cache=False)
        context = service.prepare_match(sample_detail(), self_identity="SelfPlayer#12345")
        result = service.review(context, ReviewRequest(style="praise", scope="global"))
        self.assertEqual(result.scope, "global")
        self.assertEqual(result.target_player_ids, [])


if __name__ == "__main__":
    unittest.main()
