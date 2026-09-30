# -*- coding: utf-8 -*-
"""AI 锐评前端离屏测试。

使用真实的 ai_review.AIReviewService（真实 adapter / prompt / cache），
只把 DeepSeek 网络客户端替换为假客户端，不访问任何网络。

运行：
    python -m pytest tests/test_ai_review_ui.py -q
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ai_review_backend"))

import pytest  # noqa: E402
from PySide6.QtCore import QEventLoop, QPoint, Qt, QThreadPool, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

import ai_review as AR  # noqa: E402
from ui import ai_review_bridge as AIB  # noqa: E402
from ui.assets import library  # noqa: E402
from ui.ow_ai_review import AIReviewPage, player_stat_line  # noqa: E402

SECRET = "test-api-key-placeholder"
SELF_TAG = "PublicDemo#12345"


def _app():
    return QApplication.instance() or QApplication([sys.argv[0]])


def pump(ms: int = 30) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(cond, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump(15)
        if cond():
            return
    raise AssertionError("等待超时")


# --------------------------------------------------------------------------- 数据
ROLES_5 = ("tank", "dps", "dps", "healer", "healer")
ROLES_6 = ("tank", "tank", "dps", "dps", "healer", "healer")
HEROES = {"tank": ("winston", "温斯顿"), "dps": ("ashe", "艾什"), "healer": ("ana", "安娜")}


def make_detail(per_team: int, *, complete: bool = True, expected: int | None = None, multi_hero: bool = True):
    roles = ROLES_6 if per_team == 6 else ROLES_5
    scoreboard = []
    for side, team_type in (("F", "teammate"), ("E", "enemy")):
        for i in range(per_team):
            role = roles[i % len(roles)]
            guid, name = HEROES[role]
            tag = SELF_TAG if (side == "F" and i == 0) else f"{side}Player{i + 1}#{1000 + i}"
            scoreboard.append({
                "name": tag, "bnet_id": f"{side}{i}", "customer_token": f"ct-{side}{i}",
                "team_type": team_type, "role_type": role, "hero_guid": guid, "hero_name": name,
                "kills": 10 + i, "deaths": 3 + i, "assists": 5 + i,
                "hero_damage": 9000 + i * 100,
                "healing": 12800 if role == "healer" else 0,
                "resist_damage": 10500 if role == "tank" else 0,
            })
    players = []
    if multi_hero:
        players.append({
            "name": SELF_TAG, "bnet_id": "F0", "customer_token": "ct-F0",
            "heroes": [
                {"hero_guid": "winston", "hero_name": "温斯顿", "play_time": 500},
                {"hero_guid": "dva", "hero_name": "D.Va", "play_time": 120},
                {"hero_guid": "sigma", "hero_name": "西格玛", "play_time": 30},
            ],
        })
    return {
        "match_id": f"M-{per_team}v{per_team}",
        "scoreboard_players": scoreboard,
        "players": players,
        "detail_root": {"teamScore": 2, "opponentScore": 2},
        "all_players_supported": True,
        "all_players_complete": complete,
        "player_count": expected if expected is not None else per_team * 2,
    }


META = {
    "match_id": "", "map_name": "霓虹枢纽", "result": "draw", "game_mode_zh": "竞技 · 预设职责",
    "map_mode_zh": "攻防作战", "game_time_sec": 620, "queue_kind": "preset",
}
IDENTITY = {"battle_tag": SELF_TAG, "bnet_id": "", "customer_token": "ct-F0"}


class FakeClient:
    """模拟 DeepSeek 客户端：可控制流式分片、失败与阻塞。"""

    def __init__(self):
        self.calls = 0
        self.messages = []
        self.has_api_key = True
        self.fail: Exception | None = None
        self.gate: threading.Event | None = None
        self.pieces = ("你这局", "温斯顿的数据", "整体比较扎实。")

    def set_api_key(self, key):
        self.has_api_key = bool(key)

    def test_connection(self):
        if not self.has_api_key:
            raise AR.MissingAPIKeyError("未配置 DeepSeek API Key")
        return {"id": AR.DEEPSEEK_MODEL}

    def stream_chat(self, messages, *, reasoning_effort, max_tokens, on_delta=None, on_status=None, cancel_token=None):
        self.calls += 1
        self.messages.append(messages)
        if not self.has_api_key:
            raise AR.MissingAPIKeyError("未配置 DeepSeek API Key")
        if self.fail is not None:
            raise self.fail
        on_status and on_status("requesting")
        on_status and on_status("thinking")
        parts = []
        for index, piece in enumerate(self.pieces):
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            on_status and on_status("answering")
            parts.append(piece)
            on_delta and on_delta(piece)
            if index == 0 and self.gate is not None:
                self.gate.wait(5)
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        on_status and on_status("done")
        return "".join(parts), {}, "stop"


@pytest.fixture()
def env(tmp_path):
    _app()
    fake = FakeClient()
    service = AR.AIReviewService(client=fake, cache=AR.ReviewCache(tmp_path / "cache"))
    AIB.set_key_store(AIB.APIKeyStore(tmp_path / "key.json"))
    AIB.set_ai_service(service)
    page = AIReviewPage(library(), service_provider=lambda: service)
    page.resize(1180, 900)
    page.show()
    pump()
    yield page, service, fake
    page.cancel_running()
    if fake.gate is not None:
        fake.gate.set()
    QThreadPool.globalInstance().waitForDone(3000)
    page.close()
    page.deleteLater()
    AIB.set_ai_service(None)
    AIB.set_key_store(None)


def open_page(page, per_team, **kw):
    detail = make_detail(per_team, **kw)
    meta = dict(META, match_id=detail["match_id"])
    page.open_match(detail, match_meta=meta, self_identity=IDENTITY)
    pump(40)
    return detail


def card_rects(page, team):
    grid = page.team_grid(team)
    return grid, [c.geometry() for c in grid.cards()]


def click_card(page, player_id):
    card = page.cards()[player_id]
    QTest.mouseClick(card, Qt.LeftButton, Qt.NoModifier, QPoint(card.width() // 2, card.height() // 2))
    pump()


def run_to_done(page, **kw):
    assert page.start_review(**kw)
    wait_until(lambda: not page.is_running() and not page.is_draining())


# --------------------------------------------------------------------------- 布局
@pytest.mark.parametrize("per_team", [5, 6])
def test_dynamic_team_layout_one_row(env, per_team):
    page, service, _ = env
    open_page(page, per_team)
    players = page.players()
    assert len(players) == per_team * 2
    # player_id 原样来自后端，不由前端生成。
    ctx_ids = [p["player_id"] for p in service.players_for_ui(page._ctx)]
    assert list(page.cards()) == ctx_ids
    assert ctx_ids[-1] == f"p{per_team * 2:02d}"
    for team in ("friendly", "enemy"):
        grid, rects = card_rects(page, team)
        assert len(rects) == per_team
        # 正常窗口宽度下一行显示完整，所有卡片在网格可见区域内、互不重叠。
        assert len({r.y() for r in rects}) == 1
        for r in rects:
            assert r.right() <= grid.width() and r.bottom() <= grid.height() and r.width() > 0
        for a, b in zip(rects, rects[1:]):
            assert a.right() < b.left()


def test_six_v_six_wraps_when_narrow_without_losing_players(env):
    page, _, _ = env
    open_page(page, 6)
    page.resize(560, 900)
    pump(60)
    grid, rects = card_rects(page, "friendly")
    assert len(rects) == 6
    assert len({r.y() for r in rects}) > 1  # 已换行
    for r in rects:
        assert r.right() <= grid.width() and r.bottom() <= grid.height()
    assert len({(r.x(), r.y()) for r in rects}) == 6  # 无覆盖
    page.resize(1180, 900)
    pump(60)
    _, rects = card_rects(page, "friendly")
    assert len({r.y() for r in rects}) == 1


def test_sixth_player_selectable_in_6v6(env):
    page, _, _ = env
    open_page(page, 6)
    sixth_friendly = page.team_grid("friendly").cards()[5].player_id
    sixth_enemy = page.team_grid("enemy").cards()[5].player_id
    assert (sixth_friendly, sixth_enemy) == ("p06", "p12")
    click_card(page, sixth_friendly)
    click_card(page, sixth_enemy)
    assert page.selected_ids() == ["p01", "p06", "p12"]
    req = page.build_request()
    assert req.scope == "selected" and req.target_player_ids == ("p01", "p06", "p12")
    # 12 名玩家全部可选
    page.select_team("friendly")
    for card in page.team_grid("enemy").cards():
        click_card(page, card.player_id)
    assert len(page.selected_ids()) == 12


# --------------------------------------------------------------------------- 选择 → 请求
def test_default_self(env):
    page, _, _ = env
    open_page(page, 5)
    assert page.selected_ids() == ["p01"]
    assert page.cards()["p01"].is_selected()
    req = page.build_request()
    assert (req.scope, req.style, req.target_player_ids) == ("self", "coach", ())


def test_single_teammate_and_single_enemy(env):
    page, _, _ = env
    open_page(page, 5)
    click_card(page, "p01")  # 取消本人
    assert page.build_request() is None and not page.start_btn.isEnabled()
    click_card(page, "p03")
    req = page.build_request()
    assert req.scope == "selected" and req.target_player_ids == ("p03",)
    click_card(page, "p03")
    click_card(page, "p08")
    req = page.build_request()
    assert req.scope == "selected" and req.target_player_ids == ("p08",)


def test_mixed_and_team_quick_select(env):
    page, _, _ = env
    open_page(page, 6)
    click_card(page, "p04")
    click_card(page, "p09")
    assert page.build_request().target_player_ids == ("p01", "p04", "p09")
    page.quick_friendly.click()
    pump()
    assert page.build_request().target_player_ids == tuple(f"p{i:02d}" for i in range(1, 7))
    assert page.quick_friendly.isChecked()
    page.quick_enemy.click()
    pump()
    assert page.build_request().target_player_ids == tuple(f"p{i:02d}" for i in range(7, 13))
    page.quick_self.click()
    pump()
    assert page.build_request().scope == "self"


def test_global_scope_is_not_select_all(env):
    page, _, _ = env
    open_page(page, 6)
    page.quick_global.click()
    pump()
    req = page.build_request()
    assert req.scope == "global" and req.target_player_ids == ()
    assert not any(c.is_selected() for c in page.cards().values())
    # 全选 12 人仍是 selected，而不是 global
    page.select_team("friendly")
    for card in page.team_grid("enemy").cards():
        click_card(page, card.player_id)
    assert page.build_request().scope == "selected"


def test_styles_map_to_backend_values(env):
    page, _, _ = env
    open_page(page, 5)
    for value in ("praise", "coach", "roast", "data"):
        page.set_style(value)
        assert page.build_request().style == value
        AR.ReviewRequest(style=page.build_request().style).normalized_style()


# --------------------------------------------------------------------------- 卡片内容
def test_card_content_and_multi_hero(env):
    page, _, _ = env
    open_page(page, 5)
    self_card = page.cards()["p01"]
    assert self_card.hero.hero_count() == 3  # 主英雄 + 最多 2 个次英雄
    assert self_card.name.full_text() == SELF_TAG
    tank = {"role_type": "tank", "hero_damage": 9000, "resist_damage": 10500, "healing": 0}
    assert player_stat_line(tank) == "9.0k伤害 · 10.5k抵挡"
    sup = {"role_type": "healer", "hero_damage": 3500, "healing": 12800}
    assert player_stat_line(sup) == "3.5k伤害 · 12.8k治疗"
    assert player_stat_line({"role_type": "dps", "hero_damage": 13200}) == "13.2k伤害"
    assert player_stat_line({"role_type": "dps"}) == ""


# --------------------------------------------------------------------------- 流式 / 缓存
def test_stream_cache_hit_and_force_refresh(env):
    page, _, fake = env
    open_page(page, 6)
    seen = []
    page.status.show_message = lambda text, level="info", _orig=page.status.show_message: (seen.append(text), _orig(text, level))
    run_to_done(page)
    assert page.output_text() == "你这局温斯顿的数据整体比较扎实。"
    assert "扎实" in page.output.toPlainText()
    assert fake.calls == 1
    assert "正在生成锐评…" in seen and seen[-1] == "完成"
    assert page.regen_btn.isEnabled()

    run_to_done(page)  # 相同 style/scope/targets → 缓存
    assert fake.calls == 1
    assert "已读取缓存" in seen and "缓存" in seen[-1]

    page.regen_btn.click()  # force_refresh=True
    wait_until(lambda: not page.is_running() and not page.is_draining())
    assert fake.calls == 2


def test_stop_generation_keeps_partial_text(env):
    page, _, fake = env
    open_page(page, 5)
    fake.gate = threading.Event()
    assert page.start_review()
    wait_until(lambda: "你这局" in page.output_text())
    assert page.start_btn.text() == "停止生成"
    page.start_btn.click()  # 停止
    pump()
    assert not page.is_running()
    assert page.output_text() == "你这局"
    fake.gate.set()
    wait_until(lambda: not page.is_draining())
    pump(80)
    # 取消后迟到的分片不会追加，缓存也不会写入。
    assert page.output_text() == "你这局"
    assert page.start_btn.isEnabled() and page.start_btn.text().endswith("开始 AI 锐评")
    fake.gate = None
    run_to_done(page)
    assert fake.calls == 2


def test_missing_api_key(env):
    page, _, fake = env
    open_page(page, 5)
    fake.has_api_key = False
    run_to_done(page)
    assert page.message.isVisible()
    assert "API Key" in page.message._text.text()


def test_api_failure_is_scrubbed(env):
    page, _, fake = env
    open_page(page, 5)
    AIB.key_store().save(SECRET)
    fake.fail = AR.DeepSeekAPIError(f"upstream exploded with {SECRET}", status_code=500)
    run_to_done(page)
    text = page.message._text.text()
    assert "HTTP 500" in text and SECRET not in text and "Traceback" not in text
    fake.fail = AR.DeepSeekAuthenticationError("bad key", status_code=401)
    run_to_done(page)
    assert "无效" in page.message._text.text()


def test_incomplete_warning_from_backend(env):
    page, _, _ = env
    open_page(page, 6, complete=False, expected=12)
    assert page.warning.isVisible()
    assert page.warning._text.text() == "\n".join(page._ctx.data_warnings)
    # 完整的 6v6（12 人）不应出现任何人数相关提示
    page._match_key = ""
    open_page(page, 6)
    assert not any("不完整" in w for w in page._ctx.data_warnings)


def test_prompt_contains_no_customer_token(env):
    page, _, fake = env
    open_page(page, 5)
    run_to_done(page)
    joined = str(fake.messages[-1])
    assert "ct-F0" not in joined


# --------------------------------------------------------------------------- 入口集成
def test_entry_card_at_bottom_and_reuses_detail(env, tmp_path):
    """详情页底部入口 → AI 锐评页：直接复用已获取的 detail，不重新请求。"""
    from PySide6.QtCore import QSettings

    import owstats_ui_smoke_test as SMOKE
    from ui.detail_cache import MatchDetailCache
    from ui.pages.owstats import OWStatsPage
    from ui.state import AppState

    # 所有 QSettings 写到临时 INI，不碰用户真实设置。
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "ini"))

    fake_stats = SMOKE.FakeService(current_season=24, matches_per_season=4)
    owp = OWStatsPage(AppState(), fake_stats)
    owp._detail_cache = MatchDetailCache(str(tmp_path / "detail"))
    owp.resize(1280, 900)
    owp.show()
    owp.query_edit.setText("Demo#1234")
    owp.search()
    wait_until(lambda: bool(owp._matches))
    target = next(v["match_id"] for v in owp._matches if v["competitive"])
    owp.open_detail(target)
    wait_until(lambda: owp.detail_page.ai_entry.isVisible())
    pump(60)
    calls = len(fake_stats.detail_calls)

    dp = owp.detail_page
    lay = dp.ai_entry.parentWidget().layout()
    # 入口在所有现有内容之后（后面只剩末尾 stretch），与上方内容同宽
    assert lay.indexOf(dp.ai_entry) == lay.count() - 2
    assert dp.ai_entry.y() > dp.friendly.geometry().bottom()
    assert dp.ai_entry.x() == dp.friendly.x()
    assert dp.ai_entry.geometry().right() == dp.enemy.geometry().right()

    dp.ai_entry.button.click()
    pump(60)
    assert owp.stack.currentWidget() is owp.ai_review_page
    assert len(fake_stats.detail_calls) == calls, "AI 锐评不应重新请求详情"
    ai = owp.ai_review_page
    # 本人由后端 is_self 判定（完整 BattleTag Demo#1234）
    self_players = [p for p in ai.players() if p["is_self"]]
    assert [p["battle_tag"] for p in self_players] == ["Demo#1234"]
    assert ai.selected_ids() == [self_players[0]["player_id"]]
    assert ai.build_request().scope == "self"
    # 该假数据为 6 + 3 人：按 team 动态分组，不要求 10 人、也不报错
    assert len(ai.team_grid("friendly").cards()) == 6
    assert len(ai.team_grid("enemy").cards()) == 3
    assert ai.title.text() == f"AI 锐评 · {owp._current_view['map_name']}"
    ai.backRequested.emit()
    assert owp.stack.currentWidget() is owp.detail_page
    owp.close()
    owp.deleteLater()


# --------------------------------------------------------------------------- 设置页
def test_settings_api_key_store(env, tmp_path):
    from ui.pages.settings import SettingsPage
    from ui.state import AppState

    class FakeStats:
        def auth_status(self):
            return {"configured": False}

    _page, service, fake = env
    settings = SettingsPage(AppState(), FakeStats())
    assert settings.ai_key_edit.echoMode() == QLineEdit.Password
    settings.ai_key_edit.setText(SECRET)
    assert settings._save_ai_key()
    assert settings.ai_key_edit.text() == ""
    assert AIB.key_store().load() == SECRET
    assert fake.has_api_key
    assert SECRET not in settings.ai_status.text()
    settings._test_ai_connection()
    wait_until(lambda: not settings._ai_busy)
    assert "连接成功" in settings.ai_status.text()
    settings._clear_ai_key()
    assert not AIB.key_store().has_key() and not fake.has_api_key
    settings.deleteLater()
