# -*- coding: utf-8 -*-
"""国服战绩 UI 冒烟测试（离屏 + 假后端，不联网）。

覆盖：
- 当前赛季完全来自后端；把假后端改成 S25 后界面自动显示 S25/S24/S23
- 三赛季完整战绩不被前端重新限制成 30 场，长列表分批渲染
- 地图中文名 / 地图类型图标 / 职责图标 / 段位图标素材可解析
- 一局多英雄：近期战绩卡与单局详情都显示主英雄 + 次英雄头像
- 单局玩家行左侧列顺序：组排粗竖线 → 全高职责栏 → 英雄头像 → 玩家信息（不含账号小头像）
- 国服战绩页：固定顶部 + 只有比赛列表滚动；无整页 QScrollArea；无两条常驻状态 Bar
- 四张段位卡（重装 / 输出 / 支援 / 开放职责）同一行紧凑排列
- 比赛卡右侧模式图标列固定成列，且左边缘与「导入竞逐计算器」按钮左边缘对齐
- 未定级不会被 rankScore 反推成青铜
- 组排：同组玩家队内连续排列 + 组排竖线在职责图标左侧、连续、单排无线、两组各自独立
- 详情排序：重装永远第一行；组排连续时允许输出 / 支援交错
- 详情本人高亮：按稳定账号 ID 识别
- 近期状态：当前赛季最近最多 20 场（预设竞技 + 开放竞技 + 快速）胜率与走势，无 Tooltip / Hover
- 竞逐默认胜率：首次自动初始化、用户手改后刷新不覆盖、切换账号重新初始化
- 竞技筛选：全部竞技 / 预设职责（含重装 / 输出 / 支援）/ 开放职责
- 设置页缓存管理：占用统计 + 三种清理，且 assets / 授权 / 设置 / 竞逐记录不被删除
- 竞逐：同赛季起止比赛区间、跳过非竞技、跨赛季拒绝、match_id 去重

运行：
    python owstats_ui_smoke_test.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from typing import Any, Dict, List  # noqa: E402

from PySide6.QtCore import QEventLoop, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

from main import build_app  # noqa: E402
from ui import owstats_data as D  # noqa: E402
from ui.assets import library  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def pump(ms: int = 150) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


# (地图英文名, 地图中文名, 地图类型机器值, 地图类型中文名)
# 真实后端由 map_guid 经 game_catalog 解析出 map_mode / map_mode_zh；
# instance_type 是 IT_RANKED 这类对局属性，不是地图类型。
MAP_POOL = [
    ("Colosseo", "斗兽场", "push", "机动推进"),
    ("King's Row", "国王大道", "hybrid", "攻击/护送"),
    ("Dorado", "多拉多", "escort", "运载目标"),
    ("New Junk City", "新渣客城", "flashpoint", "闪点作战"),
    ("Busan", "釜山", "control", "占领要点"),
    ("Hanaoka", "花冈", "clash", "攻防阵线"),
]
HERO_POOL = [("Kiriko", "雾子", "healer"), ("Ashe", "艾什", "dps"), ("Winston", "温斯顿", "tank")]


class FakeService:
    """只实现战绩页真正调用的后端方法；赛季号由 current_season 决定。"""

    def __init__(self, current_season: int = 24, matches_per_season: int = 140) -> None:
        self.current_season = int(current_season)
        self.matches_per_season = int(matches_per_season)
        self.detail_calls: List[str] = []

    # --- 授权 ---
    def auth_status(self) -> Dict[str, Any]:
        return {"configured": True, "role_id_masked": "****1234"}

    def season_status(self) -> Dict[str, Any]:
        return {"current_season": self.current_season, "source": "upstream", "automatic": True}

    @staticmethod
    def load_image(url: str) -> bytes:
        raise RuntimeError("测试中不联网")

    # --- 玩家 ---
    def resolve_player(self, battletag: str):
        class _Resolved:
            def __init__(self, tag: str) -> None:
                self.customer_token = "ct-demo"
                self.full_id = tag
                self.bnet_id = "42"

            def to_dict(self) -> Dict[str, Any]:
                return {
                    "customer_token": self.customer_token,
                    "full_id": self.full_id,
                    "bnet_id": self.bnet_id,
                }

        return _Resolved(battletag)

    def player_card(self, battletag=None, *, customer_token=None) -> Dict[str, Any]:
        return {
            "battle_tag": "Demo#1234",
            "bnet_id": "42",
            "icon_url": "https://nosdn.127.net/demo.png",
            "title": "纸糊的支援",
            "level": 88,
            "game_time": "833.28",
        }

    def _roles_for(self, season: int) -> List[Dict[str, Any]]:
        # 支援：正常段位；输出：未定级但上游给了 rankScore（必须显示未定级）；重装：正常。
        return [
            {
                "role_type": "healer", "role_name_zh": "支援",
                "match_sum": 31, "wins": 17, "losses": 14, "win_rate": 55.0,
                "current": {
                    "rank_name": "翡翠", "rank_name_zh": "翡翠", "rank_sub_tier": 4,
                    "is_ranked": True, "rank_status": "ranked", "display_rank": "翡翠 4",
                },
                "peak": {
                    "rank_name": "翡翠", "rank_name_zh": "翡翠", "rank_sub_tier": 2,
                    "is_ranked": True, "rank_status": "ranked", "display_rank": "翡翠 2",
                },
            },
            {
                "role_type": "dps", "role_name_zh": "输出",
                "match_sum": 3, "wins": 1, "losses": 2, "win_rate": 33.3,
                "current": {
                    "rank_name": "", "rank_name_zh": "未定级", "rank_sub_tier": None,
                    "rank_score": 1450, "is_ranked": False,
                    "rank_status": "unranked_hidden_mmr", "display_rank": "未定级",
                    "hidden_mmr": 1450, "rank_score_is_display_rank": False,
                },
                "peak": None,
            },
            {
                "role_type": "tank", "role_name_zh": "重装",
                "match_sum": 64, "wins": 37, "losses": 27, "win_rate": 58.0,
                "current": {
                    "rank_name": "翡翠", "rank_name_zh": "翡翠", "rank_sub_tier": 2,
                    "is_ranked": True, "rank_status": "ranked", "display_rank": "翡翠 2",
                },
                "peak": None,
            },
            {
                "role_type": "open", "role_name_zh": "开放职责",
                "match_sum": 24, "wins": 9, "losses": 15, "win_rate": 38.0,
                "current": {
                    "rank_name": "翡翠", "rank_name_zh": "翡翠", "rank_sub_tier": 4,
                    "is_ranked": True, "rank_status": "ranked", "display_rank": "翡翠 4",
                },
                "peak": {
                    "rank_name": "翡翠", "rank_name_zh": "翡翠", "rank_sub_tier": 2,
                    "is_ranked": True, "rank_status": "ranked", "display_rank": "翡翠 2",
                },
            },
        ]

    def player_seasons(self, battletag=None, *, customer_token=None, season_count=3) -> Dict[str, Any]:
        seasons = [self.current_season - i for i in range(season_count)]
        return {
            "current_season": self.current_season,
            "seasons": [
                {"season": s, "has_competitive": True,
                 "competitive": {"roles": self._roles_for(s), "hero_stats": {}, "raw": {}},
                 "quick": {"hero_stats": {}, "raw": {}}}
                for s in seasons
            ],
            "warnings": [],
        }

    def match_history(self, battletag=None, *, customer_token=None, season_count=3) -> Dict[str, Any]:
        matches: List[Dict[str, Any]] = []
        base_ts = 1_790_000_000
        for offset in range(season_count):
            season = self.current_season - offset
            for i in range(self.matches_per_season):
                map_en, map_zh, mode_key, mode_zh = MAP_POOL[i % len(MAP_POOL)]
                hero_en, hero_zh, role = HERO_POOL[i % len(HERO_POOL)]
                # 每 2 场造一场“一局多英雄”，验证多头像显示（数据来自后端 heroList）。
                multi = (i % 2 == 0)
                alt_en, alt_zh, _ = HERO_POOL[(i + 1) % len(HERO_POOL)]
                hero_list = [{"heroGuid": hero_en.lower(), "heroName": hero_en, "playTime": 420}]
                if multi:
                    hero_list.append({"heroGuid": alt_en.lower(), "heroName": alt_en, "playTime": 180})
                # 每 4 场里掺一场快速，用于验证区间跳过非竞技比赛。
                quick = (i % 4 == 3)
                # 每 8 场里掺一场「竞技 · 开放职责」，用于验证预设 / 开放分类。
                open_queue = (not quick) and (i % 8 == 5)
                mode = "leisure" if quick else "sport"
                if open_queue:
                    role = "open"
                game_mode = "leisure" if quick else ("SportOpen" if open_queue else "SportPreset")
                game_mode_zh = (
                    "快速" if quick else ("竞技 · 开放职责" if open_queue else "竞技 · 预设职责")
                )
                result = "win" if i % 3 else "loss"
                ts = base_ts - offset * 5_000_000 - i * 3600
                matches.append({
                    "match_id": f"S{season}-{mode}-{i:04d}",
                    "result": result,
                    "result_code": 1 if result == "win" else 2,
                    "competitive": not quick,
                    "game_mode": game_mode,
                    "game_mode_zh": game_mode_zh,
                    # 对局属性（竞技/快速标记），不是地图类型
                    "instance_type": "IT_RANKED" if not quick else "IT_QUICK",
                    "instance_type_zh": mode_zh,
                    "map_guid": f"guid-{map_en}",
                    "map_name": map_zh,
                    "map_name_raw": map_en,
                    # 地图类型来自后端 game_catalog 解析结果
                    "map_mode": mode_key,
                    "map_mode_zh": mode_zh,
                    "hero_guid": hero_en.lower(),
                    "hero_name": hero_zh,
                    "hero_name_raw": hero_en,
                    "role_type": role,
                    "role_name_zh": {
                        "tank": "重装", "dps": "输出", "healer": "支援", "open": "开放职责",
                    }[role],
                    "team_score": 3 if result == "win" else 1,
                    "opponent_score": 1 if result == "win" else 3,
                    "kills": 10 + i % 17, "assists": 5 + i % 11, "deaths": 4 + i % 7,
                    "hero_damage": 8000 + i * 13, "healing": 6000 + i * 7,
                    "resist_damage": 1200 + i * 3,
                    "damage_taken": 7000, "healing_taken": 4200, "final_hits": 6 + i % 5,
                    "game_time_sec": 635.0,
                    "begin_ts": ts,
                    "season": season,
                    "source_mode": mode,
                    "raw": {
                        "matchId": f"S{season}-{mode}-{i:04d}",
                        "gameMode": game_mode,
                        "heroList": hero_list,
                    },
                })
        return {
            "current_season": self.current_season,
            "count": len(matches),
            "complete": getattr(self, "history_complete", True),
            "season_range": {"start": self.current_season - season_count + 1,
                             "end": self.current_season, "count": season_count},
            "warnings": [],
            "matches": matches,
        }

    def match_all_players_detail(self, match_id, *, customer_token=None, source_match=None) -> Dict[str, Any]:
        self.detail_calls.append(str(match_id))
        # 我方：A/Demo/D 三人组 + C 单排 + E 双排（与 F 同组，F 在我方）
        # 本人识别只看完整 BattleTag：第二名队友的 BattleTag 就是当前查询账号 Demo#1234，
        # 但它的 customer_token / bnet_id 故意与本人（ct-demo / 42）不一致；
        # 反过来玩家D 故意持有本人的 customer_token ct-demo，但 BattleTag 不同，不能被判为本人。
        friendly = [
            self._player_row("玩家A#1001", "1001", "teammate", "tank", ["1002", "1004"]),
            self._player_row("Demo#1234", "1002", "teammate", "dps", ["1001", "1004"],
                             token="ct-not-self"),
            self._player_row("玩家C#1003", "1003", "teammate", "healer", []),
            self._player_row("玩家D#1004", "1004", "teammate", "dps", ["1001", "1002"],
                             token="ct-demo"),
            self._player_row("玩家E#1005", "1005", "teammate", "healer", ["1006"]),
            self._player_row("玩家F#1006", "1006", "teammate", "tank", ["1005"]),
        ]
        enemy = [
            self._player_row("敌人G#2001", "2001", "enemy", "tank", []),
            self._player_row("敌人H#2002", "2002", "enemy", "dps", ["2003"]),
            self._player_row("敌人I#2003", "2003", "enemy", "healer", ["2002"]),
        ]
        return {
            "match_id": str(match_id),
            "match_kind": "normal",
            "detail_root": {"teamScore": 3, "opponentScore": 1},
            "scoreboard_players": friendly + enemy,
            "all_players_supported": True,
            "all_players_complete": True,
            "player_count": len(friendly) + len(enemy),
            "available_player_count": len(friendly) + len(enemy),
            # 玩家A 一局用了 3 个英雄（带使用时间），用于验证多头像 + 主英雄排序。
            "players": [
                {"name": row["name"], "bnet_id": row["bnet_id"], "customer_token": row["customer_token"],
                 "icon_url": "https://nosdn.127.net/p.png", "title": "上单之王",
                 "hero_list": (
                     [
                         {"heroGuid": "winston", "heroName": "Winston", "playTime": 120},
                         {"heroGuid": "dva", "heroName": "D.Va", "playTime": 480},
                         {"heroGuid": "orisa", "heroName": "Orisa", "playTime": 60},
                     ]
                     if row["name"].startswith("玩家A")
                     else [{"heroGuid": row["hero_guid"]}]
                 ),
                 "success": True}
                for row in friendly + enemy
            ],
            "warnings": [],
        }

    @staticmethod
    def _player_row(name, bnet_id, team, role, friends, *, token=None) -> Dict[str, Any]:
        hero_en, hero_zh, _ = {"tank": HERO_POOL[2], "dps": HERO_POOL[1], "healer": HERO_POOL[0]}[role]
        unranked = name.startswith("玩家C")
        rank_info = {
            "rank_name": "" if unranked else "翡翠",
            "rank_name_zh": "未定级" if unranked else "翡翠",
            "rank_sub_tier": None if unranked else 4,
            "rank_score": 1500 if unranked else 2600,
            "is_ranked": not unranked,
            "rank_status": "unranked_hidden_mmr" if unranked else "ranked",
            "display_rank": "未定级" if unranked else "翡翠 4",
        }
        return {
            "team_type": team, "name": name, "bnet_id": bnet_id,
            "customer_token": token or f"ct-{bnet_id}",
            "hero_guid": hero_en.lower(), "hero_name": hero_zh, "hero_name_raw": hero_en,
            "role_type": role, "role_name_zh": {"tank": "重装", "dps": "输出", "healer": "支援"}[role],
            "kills": 19, "assists": 2, "deaths": 4,
            "hero_damage": 9800, "healing": 120, "resist_damage": 3000,
            "damage_taken": 8100, "healing_taken": 5200, "final_hits": 11,
            "rank_info": rank_info, "friend_bnet_ids": list(friends),
        }


def _isolated_user_settings():
    """所有测试设置写入临时 INI；从不读写用户真实 QSettings。"""
    import atexit
    import tempfile
    from PySide6.QtCore import QSettings

    temporary = tempfile.TemporaryDirectory(prefix="ow-ui-settings-")
    atexit.register(temporary.cleanup)
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, temporary.name)
    QSettings.setPath(QSettings.IniFormat, QSettings.SystemScope, temporary.name)
    settings = QSettings("ow_drive_split", "drive_calculator")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}

    def restore() -> None:
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()

    return snapshot, restore


def main() -> int:
    settings_snapshot, restore_settings = _isolated_user_settings()
    app = build_app([sys.argv[0]])
    win = MainWindow()
    page = win.owstats_page
    fake = FakeService(current_season=24)
    fake.history_complete = False  # 未确认分页完整时也不得显示常驻提示条。
    page._service = fake  # 只替换 UI 侧引用，不触碰真实后端实现
    win.resize(1570, 990)
    win.show()
    win._select(0)  # 国服战绩已是第一项导航，保证列表视口有真实高度
    pump(200)
    assert win.stack.currentIndex() == 0 and win.stack.currentWidget() is page

    lib = library()
    assert lib.ready(), "assets/manifest.json 未就绪，请先运行 scripts/download_ow_assets.py"

    # 1) 素材解析：英雄头像 / 地图背景 / 段位 / 职责 / 地图类型
    assert lib.hero_path("雾子"), "中文英雄名无法解析头像"
    assert lib.hero_path("Kiriko"), "英文英雄名无法解析头像"
    assert lib.map_path("斗兽场"), "中文地图名无法解析背景图"
    assert lib.map_path("国王大道"), "中文地图名无法解析背景图"
    for rank in ("翡翠 4", "青铜", "英杰", "未定级"):
        assert not lib.rank_icon(rank, 16).isNull(), f"段位图标缺失: {rank}"
    for role in ("tank", "dps", "healer", "open", "重装", "输出", "支援", "开放职责"):
        assert not lib.role_icon(role, 16).isNull(), f"职责图标缺失: {role}"
    for inst in ("机动推进", "占领要点", "运载目标", "攻击/护送", "闪点作战", "攻防阵线", "Push", "Control"):
        assert not lib.map_mode_icon(inst, 14).isNull(), f"地图类型图标缺失: {inst}"
    print("[ok] 英雄 / 地图 / 段位 / 职责 / 地图类型素材全部可解析（本地 assets）")

    # 2) 查询（同步执行任务，避免依赖线程时序）
    page.query_edit.setText("Demo#1234")
    page.search()
    for _ in range(60):
        pump(100)
        if page._matches:
            break
    assert page._matches, "查询未返回比赛"
    assert page._current_season == 24, page._current_season
    from PySide6.QtWidgets import QLabel, QFrame
    sidebar = win.findChild(QFrame, "Sidebar")
    assert sidebar.width() == 120
    for label in sidebar.findChildren(QLabel):
        if label.text() == "OW ASSISTANT":
            assert label.fontMetrics().horizontalAdvance(label.text()) <= label.contentsRect().width()
    for button in win._nav_buttons:
        assert button.sizeHint().width() <= sidebar.width(), (button.text(), button.sizeHint())
    assert page.banner._host_layout.indexOf(page.banner) == -1
    assert page.banner.isHidden()
    assert not hasattr(page.recent_form, "older") and not hasattr(page.recent_form, "newer")
    # 资料卡只有昵称时仍使用解析查询结果的完整 BattleTag。
    identity = D.query_player_identity(
        {"full_id": " player#1234 ", "customer_token": "current"},
        {"battle_tag": "PublicDemo"},
    )
    page.detail_page.set_self_identity(identity=identity)
    page.detail_page.set_detail({}, {"scoreboard_players": [
        {"name": "player#1234", "role_type": "tank"},
        {"name": "PublicDemo#99999", "role_type": "dps"},
    ]})
    assert page.detail_page.friendly._rows[0].is_self()
    assert not page.detail_page.friendly._rows[1].is_self()
    # 本人识别只看完整 BattleTag，任何稳定 ID 都不参与判定。
    for field in ("customerToken", "playerToken", "accountId", "playerId", "bnetId"):
        # 只有稳定 ID 一致、没有 BattleTag：不是本人
        assert not D.is_self_player({field: "42"}, self_identity={field: "42"}), field
        # 稳定 ID 一致但 BattleTag 不同：不是本人
        assert not D.is_self_player({field: "42", "name": "别人#0001"},
                                    self_identity={field: "42", "full_id": "player#1234"}), field
        # 稳定 ID 不一致但完整 BattleTag 相同：是本人
        assert D.is_self_player({field: "43", "name": "player#1234"},
                                self_identity={field: "42", "full_id": "player#1234"}), field
    assert D.is_self_player({"name": " Foo#123 "}, self_battle_tag="foo#123")
    assert not D.is_self_player({"name": "Foo"}, self_battle_tag="foo")
    page.detail_page.set_self_identity(customer_token=page._customer_token,
                                     battle_tag=page._player.get("battle_tag", ""))
    print("[ok] 120px 导航完整、提示条不占布局、完整 BattleTag 命中、稳定 ID 不参与本人判定")

    assert page._seasons == [24, 23, 22], page._seasons
    assert len(page._matches) == 420, len(page._matches)
    print(f"[ok] 后端赛季 S24 -> 界面 {['S%d' % s for s in page._seasons]}，共 {len(page._matches)} 场（无 30 场限制）")

    # 3) 长列表懒加载
    rendered = page.match_list.rendered_count()
    viewport = page.match_list.scroll.viewport().height()
    assert viewport > 200, f"测试环境视口异常: {viewport}"
    assert rendered <= 80, f"首屏渲染过多卡片: {rendered}"
    assert rendered < page.match_list.total_count()
    assert len(page.match_list._cards) == rendered, "创建的卡片数应等于已渲染数"
    bar = page.match_list.scroll.verticalScrollBar()
    bar.setValue(bar.maximum())
    pump(150)
    grown = page.match_list.rendered_count()
    assert grown > rendered, "滚动到底部未继续加载"
    assert grown < page.match_list.total_count(), "滚动一次不应直接渲染全部"
    print(
        f"[ok] 长列表懒加载：首屏 {rendered} 张卡片 / 共 {page.match_list.total_count()} 场，"
        f"滚动后增量到 {grown} 张（未一次实例化上千 QWidget）"
    )

    # 4) 职责卡：未定级不被 rankScore 反推
    dps_card = next(c for c in page.role_cards if c._data.get("role_type") == "dps")
    assert dps_card._data["current"]["display"] == "未定级", dps_card._data["current"]
    assert dps_card._data["current"]["is_ranked"] is False
    healer_card = next(c for c in page.role_cards if c._data.get("role_type") == "healer")
    assert healer_card._data["current"]["display"] == "翡翠 4"
    assert healer_card._data["role_name"] == "支援"
    tank_card = next(c for c in page.role_cards if c._data.get("role_type") == "tank")
    assert tank_card._data["role_name"] == "重装"
    assert page.open_card.isVisible() and page.open_card._data["role_name"] == "开放职责"
    print("[ok] 三职责中文名 + 开放职责卡；未定级（隐藏 MMR 1450）显示为未定级而非青铜")

    # 4b) 四张段位卡：紧凑 + 同一行等宽 + 开放职责不再独占整行
    from ui.ow_widgets import RoleRankCard

    four_cards = list(page.role_cards) + [page.open_card]
    for c in four_cards:
        assert c.height() == RoleRankCard.CARD_HEIGHT, (c.height(), RoleRankCard.CARD_HEIGHT)
    assert RoleRankCard.CARD_HEIGHT <= 60, RoleRankCard.CARD_HEIGHT
    tops = {c.y() for c in four_cards}
    assert len(tops) == 1, f"四张段位卡未排在同一行：{sorted(tops)}"
    widths = [c.width() for c in four_cards]
    assert max(widths) - min(widths) <= 2, f"四张段位卡宽度不等：{widths}"
    xs = [c.x() for c in four_cards]
    assert xs == sorted(xs) and len(set(xs)) == 4, xs
    # 标题合并：只剩一个「竞技段位 · Sxx」，不再有独立「开放职责」标题
    assert not hasattr(page, "open_header"), "开放职责独立标题应已删除"
    assert page.season_header._text.startswith("竞技段位 · S"), page.season_header._text
    assert "预设职责" not in page.season_header._text
    assert "开放职责" not in page.season_header._text
    print(
        f"[ok] 四张段位卡同一行等宽（高 {RoleRankCard.CARD_HEIGHT}px，宽 {widths}），"
        f"标题合并为「{page.season_header._text}」"
    )

    # 4c) 国服战绩页：固定顶部 + 只有比赛列表滚动
    from PySide6.QtWidgets import QScrollArea as _QScrollArea

    browser = page.stack.widget(0)
    scroll_areas = browser.findChildren(_QScrollArea)
    assert len(scroll_areas) == 1, [type(s).__name__ for s in scroll_areas]
    assert scroll_areas[0] is page.match_list.scroll, "唯一滚动区必须是比赛列表"
    # 顶部区域（查询 / 资料 / 段位 / 近期状态 / 筛选）不在任何滚动容器内
    for widget in (page.query_edit, page.player_header, page.season_header,
                   page.recent_form, page.mode_chips, page.open_card):
        parents = []
        node = widget.parentWidget()
        while node is not None and node is not browser:
            parents.append(node)
            node = node.parentWidget()
        assert not any(isinstance(p, _QScrollArea) for p in parents), (
            f"{type(widget).__name__} 仍在滚动容器内"
        )
    # 顶部在 1570x990 下必须完整显示（底部不超出页面可视高度）
    top_bottom = page.recent_form.mapTo(browser, page.recent_form.rect().bottomLeft()).y()
    assert top_bottom < browser.height(), (top_bottom, browser.height())
    assert page.match_list.scroll.viewport().height() > 150, page.match_list.scroll.viewport().height()
    print(
        "[ok] 国服战绩页只有比赛列表一个滚动容器；顶部区域不滚动且完整显示"
        f"（近期状态底边 {top_bottom}px < 页面 {browser.height()}px）"
    )

    # 4d) 两条常驻状态 Bar 已删除
    import inspect

    from PySide6.QtWidgets import QLabel as _QLabel

    assert not hasattr(page.drive_bar, "season_label"), "竞逐赛季状态条应已删除"
    assert not hasattr(page.drive_bar, "range_label"), "起点 / 终点状态条应已删除"
    assert not page.drive_bar.findChildren(_QLabel), "竞逐区间不应再有任何常驻文字标签"
    assert page.drive_bar.layout().count() == 2, page.drive_bar.layout().count()
    margins = page.drive_bar.layout().contentsMargins()
    assert (margins.top(), margins.bottom(), margins.left()) == (0, 0, 0), margins
    assert page.drive_bar.import_btn.text() == "导入竞逐计算器"
    # 「已加载 xxx 场……」不再常驻：正常加载完成后提示条直接隐藏。
    assert not page.banner.isVisible(), "正常加载完成后不应常驻状态条"
    assert "已加载" not in page.banner._text.text(), page.banner._text.text()
    print("[ok] 「已加载 xxx 场」与「当前竞逐赛季 / 起点 / 终点」两条 Bar 均已删除")

    # 5) MatchCard 字段
    card = page.match_list._cards[0]
    view = card._view
    assert view["map_name"] in {m[1] for m in MAP_POOL}, view["map_name"]
    # 地图类型取后端 map_mode_zh；instance_type(IT_RANKED) 不是地图类型
    assert view["map_mode_zh"] in {m[3] for m in MAP_POOL}, view["map_mode_zh"]
    assert view["map_mode"] in {m[2] for m in MAP_POOL}, view["map_mode"]
    assert view["instance_type"].startswith("IT_"), view["instance_type"]
    assert view["role_name"] in {"重装", "输出", "支援"}
    assert view["rank_display"], "MatchCard 缺少段位信息"
    assert not card._portrait.isNull(), "MatchCard 缺少英雄头像"
    assert not card._mode_icon.isNull(), "MatchCard 缺少地图类型图标"
    assert not card._role_icon.isNull(), "MatchCard 缺少职责图标"
    # 地图类型只能来自后端 map_mode_zh，IT_RANKED 之类的 instance_type 不得出现在界面上
    assert "IT_" not in (card.toolTip() or ""), card.toolTip()
    assert all("IT_" not in (c.toolTip() or "") for c in page.match_list._cards if c.isVisible())
    card._ensure_background()
    assert not card._background.isNull(), "MatchCard 缺少地图背景图"
    print("[ok] MatchCard：地图中文名 + 地图类型图标 + 职责图标 + 段位 + 英雄头像 + 地图背景")

    # 5a) 地图模式图标列：素材来源 / 尺寸 / 不变色 / 成列 / 与按钮左缘对齐
    from PySide6.QtGui import QColor as _QColor

    from ui.assets import ASSETS_DIR, MAP_MODE_KEYS
    from ui.ow_widgets import MatchCard as _MatchCard

    # 素材必须来自 assets/map_modes 下已有的透明图标，不新增第二套解析
    for mode_key, mode_zh in (("control", "占领要点"), ("hybrid", "攻击/护送"),
                              ("escort", "运载目标"), ("push", "机动推进"),
                              ("flashpoint", "闪点作战"), ("clash", "攻防阵线"),
                              ("assault", "攻防作战")):
        assert (ASSETS_DIR / "map_modes" / f"{mode_key}.png").exists(), mode_key
        assert MAP_MODE_KEYS[mode_zh] == mode_key, (mode_zh, MAP_MODE_KEYS[mode_zh])
        assert lib.map_mode_key(mode_key) == mode_key
    assert _MatchCard.MODE_ICON_SIZE == 26, _MatchCard.MODE_ICON_SIZE

    visible_cards = [c for c in page.match_list._cards if c.isVisible()]
    assert visible_cards, "没有可见比赛卡"
    for c in visible_cards:
        assert not c._mode_icon_column.isNull(), f"{c.match_id()} 缺少地图模式图标"
        # 图标由已解析好的 map_mode 字段决定，而不是地图名
        assert lib.map_mode_key(c._view["map_mode"]) == c._view["map_mode"], c._view["map_mode"]
    # 模式图标列左边缘完全一致（成一条固定纵向列，不随左侧文字漂移）
    lefts = {c.mode_icon_left() for c in visible_cards}
    assert len(lefts) == 1, f"模式图标列左边缘不一致：{sorted(lefts)}"
    icon_left_in_card = lefts.pop()
    # 模式图标位于比分左侧
    assert icon_left_in_card + _MatchCard.MODE_ICON_SIZE < visible_cards[0].width() - 14

    # 与「导入竞逐计算器」按钮左边缘大致对齐（允许几像素误差）
    page._sync_mode_icon_column()
    pump(80)
    ref = visible_cards[0]
    btn = page.drive_bar.import_btn
    btn_left_global = btn.mapToGlobal(btn.rect().topLeft()).x()
    icon_left_global = ref.mapToGlobal(ref.rect().topLeft()).x() + ref.mode_icon_left()
    assert abs(icon_left_global - btn_left_global) <= 6, (icon_left_global, btn_left_global)

    # 图标不随胜负变色：胜 / 负两张卡的同模式图标像素完全一致
    def _mode_pix(result_kind: str):
        target = next(
            (c for c in visible_cards
             if c._view["result"] == result_kind and c._view["map_mode"] == "push"),
            None,
        )
        return target._mode_icon_column.toImage() if target is not None else None

    win_img, lose_img = _mode_pix("win"), _mode_pix("loss")
    if win_img is not None and lose_img is not None:
        assert win_img == lose_img, "模式图标随胜负变了色"
    # 素材保持透明背景
    probe = lib.map_mode_icon("push", _MatchCard.MODE_ICON_SIZE).toImage()
    assert probe.hasAlphaChannel(), "模式图标素材应为透明 PNG"
    assert _QColor(probe.pixel(0, 0)).alpha() == 0 or probe.pixelColor(0, 0).alpha() == 0

    # 原有模式文字仍保留（精确阅读），图标只是辅助扫视
    center_src = inspect.getsource(_MatchCard._paint_center)
    assert "map_mode_zh" in center_src and "game_mode_zh" in center_src, "模式文字被误删"
    print(
        f"[ok] 地图模式图标：assets/map_modes 透明素材 {_MatchCard.MODE_ICON_SIZE}px，"
        f"按 map_mode 字段选择，固定成列（卡内 x={icon_left_in_card}）"
        f"且与「导入竞逐计算器」按钮左缘对齐（差 {abs(icon_left_global - btn_left_global)}px），"
        "不随胜负变色，模式文字保留"
    )

    # 5b) 一局多英雄：近期战绩卡显示主英雄 + 次英雄头像
    multi_card = next(
        (c for c in page.match_list._cards if c.isVisible() and len(c._view.get("heroes") or []) > 1),
        None,
    )
    assert multi_card is not None, "近期战绩未出现多英雄对局"
    assert multi_card._secondary_portraits, "多英雄对局缺少次英雄头像"
    assert all(not p.isNull() for p in multi_card._secondary_portraits), "次英雄头像为空"
    assert multi_card._portrait_width() > multi_card.PORTRAIT_SIZE, "多英雄未扩展头像区宽度"
    single_card = next(
        (c for c in page.match_list._cards if c.isVisible() and len(c._view.get("heroes") or []) == 1),
        None,
    )
    assert single_card is not None and not single_card._secondary_portraits, "单英雄对局不应出现次头像"
    print(
        f"[ok] 近期战绩多英雄：主头像 + {len(multi_card._secondary_portraits)} 个次头像，"
        "单英雄对局仍只有一个头像"
    )

    # 6) 筛选：模式 + 赛季
    page._on_mode_filter("sport")
    assert all(v["filter_key"] == "sport" for v in page._filtered_matches())
    page._on_season_filter("23")
    filtered = page._filtered_matches()
    assert filtered and all(v["season"] == 23 for v in filtered)
    page._on_mode_filter("all")
    page._on_season_filter("all")
    assert len(page._filtered_matches()) == 420
    print("[ok] 模式 / 赛季筛选联动")

    # 6b) 竞技分类：全部竞技 / 预设职责（含重装/输出/支援）/ 开放职责
    page._on_mode_filter("sport")
    all_sport = page._filtered_matches()
    page._on_mode_filter("sport_preset")
    assert page.preset_role_chips.isVisible(), "选中预设职责后应出现职责二级筛选"
    presets = page._filtered_matches()
    assert presets and all(v["queue_kind"] == "preset" for v in presets)
    assert all(v["role_type"] in {"tank", "dps", "healer"} for v in presets)
    page._on_mode_filter("sport_open")
    assert not page.preset_role_chips.isVisible(), "开放职责不应显示预设职责二级筛选"
    opens = page._filtered_matches()
    assert opens and all(v["queue_kind"] == "open" for v in opens)
    assert all(v["role_type"] == "open" for v in opens)
    assert len(presets) + len(opens) == len(all_sport), (len(presets), len(opens), len(all_sport))
    # 预设职责的二级筛选（重装 / 输出 / 支援）
    page._on_mode_filter("sport_preset")
    for role_key in ("tank", "dps", "healer"):
        page._on_preset_role_filter(role_key)
        rows = page._filtered_matches()
        assert rows and all(v["role_type"] == role_key for v in rows), role_key
    page._on_preset_role_filter("all")
    page._on_mode_filter("all")
    assert page._preset_role_filter == "all" and not page.preset_role_chips.isVisible()
    print(
        f"[ok] 竞技分类：全部竞技 {len(all_sport)} = 预设 {len(presets)} + 开放 {len(opens)}，"
        "预设可再按重装 / 输出 / 支援筛选"
    )

    # 6c) 职责图标严格映射：flex.png 只代表开放职责，绝不作为未知职责 fallback
    from ui.assets import ROLE_FILES

    assert lib.role_key("tank") == "tank" and lib.role_key("重装") == "tank"
    assert lib.role_key("dps") == "damage" and lib.role_key("输出") == "damage"
    assert lib.role_key("healer") == "support" and lib.role_key("支援") == "support"
    assert lib.role_key("open") == "flex" and lib.role_key("开放职责") == "flex"
    for unknown in ("", "unknown", "任意", "任意职责", "flex", "???"):
        assert lib.role_key(unknown) != "flex", f"{unknown!r} 不得映射到 flex（开放职责专用）"
        assert lib.role_icon(unknown, 16).isNull(), f"{unknown!r} 不应拿到任何职责图标"
    assert ROLE_FILES["tank"][0] == "roles/tank.png"
    assert ROLE_FILES["damage"][0] == "roles/damage.png"
    assert ROLE_FILES["support"][0] == "roles/support.png"
    assert ROLE_FILES["flex"][0] == "roles/flex.png"
    print("[ok] tank/damage/support 对应预设三职责，flex 仅代表开放职责且不作为未知 fallback")

    # 6d) 近期状态：当前赛季最近最多 20 场（预设竞技 + 开放竞技 + 快速合并）
    form = page.recent_form_summary()
    current_season_rows = [v for v in page._matches if v["season"] == 24]
    eligible = [v for v in current_season_rows if v["filter_key"] in ("sport", "leisure")]
    assert form["available"] == len(eligible), (form["available"], len(eligible))
    assert form["total"] == 20, form["total"]
    assert form["season"] == 24
    kinds = {v["filter_key"] for v in form["matches"]}
    queue_kinds = {v.get("queue_kind") for v in form["matches"] if v["filter_key"] == "sport"}
    assert "sport" in kinds and "leisure" in kinds, kinds
    assert {"preset", "open"} <= queue_kinds, queue_kinds
    # 截取的必须是时间最新的 20 场
    newest20 = sorted(eligible, key=lambda v: v["begin_ts"])[-20:]
    assert [v["match_id"] for v in form["matches"]] == [v["match_id"] for v in newest20]
    # 数据层顺序不变，仅 UI 反转为最左最新
    ts = [v["begin_ts"] for v in form["matches"]]
    assert ts == sorted(ts), "近期走势必须按时间升序（最右最新）"
    assert page.recent_form.strip.results() == list(reversed(form["results"]))
    # 胜率 = 胜场 / 总场数（平局计入分母）
    assert abs(form["win_rate"] - form["wins"] * 100.0 / form["total"]) < 1e-9
    assert form["wins"] + form["losses"] + form["draws"] == form["total"]
    assert page.recent_form.rate.text() == f"{form['win_rate']:.1f}%"
    assert page.recent_form.record.text() == f"{form['wins']}胜 · {form['losses']}负"
    assert "0平" not in page.recent_form.record.text()
    assert page.recent_form.strip.isVisible() and not page.recent_form.empty.isVisible()
    print(
        f"[ok] 近期状态：{form['total']} 场（候选 {form['available']}）"
        f" {page.recent_form.record.text()} · {page.recent_form.rate.text()}，"
        "含预设竞技 / 开放竞技 / 快速，最左为最新"
    )

    # 6e) 近期走势不做任何交互，也不发额外网络请求
    strip = page.recent_form.strip
    assert not strip.toolTip(), "近期走势不应有 Tooltip"
    assert strip.testAttribute(Qt.WA_TransparentForMouseEvents), "近期走势不应接收鼠标事件"
    assert strip.focusPolicy() == Qt.NoFocus
    assert type(strip).enterEvent is QWidget.enterEvent, "近期走势不应实现 Hover"
    assert type(strip).mousePressEvent is QWidget.mousePressEvent, "近期走势不应可点击"

    strip_src = inspect.getsource(type(strip))
    for banned in ("setToolTip", "load_image", "AvatarCache", "submit("):
        assert banned not in strip_src, f"近期走势不应出现 {banned}"
    print("[ok] 近期走势无 Tooltip / Hover / 点击，且不触发任何网络请求")

    # 6f) 纯数据层口径校验（自定义 / 训练 / 街机不进入；不足 20 场用实际场数；不跨赛季补齐）
    def _mk(mid, season, ts, filter_key, result, *, game_mode="", queue="preset"):
        return {
            "match_id": mid, "season": season, "begin_ts": ts, "filter_key": filter_key,
            "result": result, "game_mode": game_mode or filter_key, "queue_kind": queue,
            "instance_type": "", "game_mode_zh": "", "instance_type_zh": "", "source_mode": "",
        }

    rows30 = [
        _mk(f"m{i}", 24, 1000 + i, "sport" if i % 2 else "leisure", "win" if i % 3 else "loss")
        for i in range(30)
    ]
    s30 = D.recent_form_summary(rows30, 24)
    assert s30["available"] == 30 and s30["total"] == 20
    assert [m["match_id"] for m in s30["matches"]] == [f"m{i}" for i in range(10, 30)]

    rows13 = rows30[:13]
    s13 = D.recent_form_summary(rows13, 24)
    assert s13["total"] == 13 and s13["available"] == 13

    mixed = rows30[:10] + [
        _mk("custom", 24, 5000, "other", "win", game_mode="CustomGame"),
        _mk("arcade", 24, 5001, "other", "win", game_mode="Arcade"),
        _mk("train", 24, 5002, "other", "win", game_mode="Training"),
        _mk("fight", 24, 5003, "SportFight", "win", game_mode="SportFight"),
    ] + [_mk(f"old{i}", 23, 900 + i, "sport", "win") for i in range(40)]
    s_mixed = D.recent_form_summary(mixed, 24)
    assert s_mixed["total"] == 10, s_mixed["total"]
    ids = {m["match_id"] for m in s_mixed["matches"]}
    assert not ({"custom", "arcade", "train", "fight"} & ids), ids
    assert not any(m["match_id"].startswith("old") for m in s_mixed["matches"]), "不得跨赛季补齐"

    draw_rows = (
        [_mk(f"w{i}", 24, 100 + i, "sport", "win") for i in range(12)]
        + [_mk(f"l{i}", 24, 200 + i, "leisure", "loss") for i in range(7)]
        + [_mk("d0", 24, 300, "sport", "draw")]
    )
    s_draw = D.recent_form_summary(draw_rows, 24)
    assert s_draw["total"] == 20 and s_draw["draws"] == 1
    assert abs(s_draw["win_rate"] - 60.0) < 1e-9, s_draw["win_rate"]
    assert D.recent_form_record_text(s_draw) == "12胜 · 7负 · 1平"
    s_empty = D.recent_form_summary([], 24)
    assert s_empty["total"] == 0 and s_empty["win_rate"] is None
    assert D.recent_form_rate_text(s_empty) == "" and D.recent_form_record_text(s_empty) == ""
    assert D.recent_form_summary(rows30, None)["total"] == 0, "赛季未知时不得统计"
    print("[ok] 近期比赛口径：30→20 / 13→13 / 排除自定义·训练·街机·角斗 / 不跨赛季 / 平局计入分母")

    # 6g) 没有本赛季比赛数据时显示占位文案，绝不显示 0%
    page.recent_form.set_summary(s_empty)
    assert page.recent_form.empty.isVisible()
    assert page.recent_form.empty.text() == "暂无本赛季比赛数据"
    assert not page.recent_form.rate.isVisible() and page.recent_form.rate.text() == ""
    assert not page.recent_form.strip.isVisible()
    page.recent_form.set_summary(form)
    print("[ok] 无本赛季比赛数据时显示“暂无本赛季比赛数据”，不显示 0%")

    # 7) 赛季切换同步职责数据与标题
    page._on_season_changed(22)
    assert page._selected_season == 22
    assert "S22" in page.season_header._text, page.season_header._text
    assert page._filtered_matches() and all(v["season"] == 22 for v in page._filtered_matches())
    page._on_season_changed(24)
    print("[ok] 赛季切换同步段位区、筛选与标题")

    # 8) 单局详情 + 组排
    # 详情缓存用独立临时目录：避免复用上一次运行落盘的旧详情，导致断言看到过期数据。
    import tempfile

    from ui.detail_cache import MatchDetailCache

    cache_dir = tempfile.mkdtemp(prefix="ow_ui_smoke_detail_")
    page._detail_cache = MatchDetailCache(cache_dir)

    target = next(v["match_id"] for v in page._matches if v["competitive"])
    page.open_detail(target)
    for _ in range(40):
        pump(100)
        if page.detail_page.friendly._rows and page.detail_page.friendly._rows[0].isVisible():
            break
    assert win.owstats_page.stack.currentIndex() == 1
    friendly_rows = [r for r in page.detail_page.friendly._rows if r.isVisible()]
    names = [r._data["name"] for r in friendly_rows]
    assert len(names) == 6, names
    # A/B/D 同组必须连续，E/F 同组必须连续，C 单排在后
    def pos(tag: str) -> int:
        return next(i for i, n in enumerate(names) if n.startswith(tag))
    group1 = sorted(pos(t) for t in ("玩家A", "Demo#1234", "玩家D"))
    group2 = sorted(pos(t) for t in ("玩家E", "玩家F"))
    assert group1 == list(range(group1[0], group1[0] + 3)), names
    assert group2 == list(range(group2[0], group2[0] + 2)), names
    assert pos("玩家C") > max(group1 + group2), names
    spans = [r.party._active for r in friendly_rows]
    assert spans[group1[0]] and spans[group1[1]] and spans[group1[2]], "组排竖线未激活"
    assert friendly_rows[group1[0]].party._first and friendly_rows[group1[2]].party._last
    assert not friendly_rows[group1[1]].party._first and not friendly_rows[group1[1]].party._last, "中间行应连续无端点"
    assert friendly_rows[group2[0]].party._first and friendly_rows[group2[1]].party._last, "第二组应有独立竖线"
    assert not friendly_rows[pos("玩家C")].party._active, "单排不应显示组排竖线"
    # 重装（玩家A）必须永远第一行，即使 A 参与三排
    assert names[0].startswith("玩家A"), names
    assert friendly_rows[0]._data["role_type"] == "tank", friendly_rows[0]._data["role_type"]
    # 左侧列顺序必须是：组排粗线 → 全高职责栏 → 英雄头像 → 玩家信息（记分板基准）
    row0 = friendly_rows[0]
    pump(30)
    assert row0.role.role_name() in {"重装", "输出", "支援"}, row0.role.role_name()
    assert not row0.role._pixmap.isNull(), "PlayerRow 缺少职责图标"
    assert not row0.hero._pixmap.isNull(), "PlayerRow 缺少英雄头像"
    order = [row0.party.x(), row0.role.x(), row0.hero.x(), row0.name.x()]
    assert order == sorted(order), f"左侧列顺序不符合记分板：{order}"
    assert row0.party.x() == row0.role.x() == 0
    assert row0.layout().indexOf(row0.party) == -1, "组排 overlay 不得加入布局"
    positions = {(r.role.x(), r.hero.x(), r.name.x()) for r in friendly_rows}
    assert len(positions) == 1, positions
    # 职责栏不得出现在段位 / KDA 附近
    assert row0.role.x() < row0.rank_badge.x() and row0.role.x() < row0.kda.x()
    print("[ok] PlayerRow 组排 overlay 不占位，混合队伍内容对齐")

    # 8b) 玩家账号小圆头像已从详情玩家行删除，且没有残留占位/空白
    from ui.ow_widgets import PartyIndicator, PlayerAvatar, PlayerRow, RoleIcon

    assert not hasattr(row0, "avatar"), "详情玩家行不应再有账号小头像控件"
    for r in friendly_rows + [r for r in page.detail_page.enemy._rows if r.isVisible()]:
        assert not r.findChildren(PlayerAvatar), "详情玩家行残留 PlayerAvatar（空圆框）"
    # 英雄头像与玩家信息之间只有显式 8px 间距，没有被删控件留下的空洞。
    gap = row0.name.x() - (row0.hero.x() + row0.hero.width())
    assert 0 <= gap <= 10, f"英雄头像与玩家信息之间存在异常空白：{gap}px"
    # 顶部查询玩家的主头像没有被误删（此刻正显示详情页，所以只校验控件仍在且未隐藏）
    assert isinstance(page.player_header.avatar, PlayerAvatar)
    assert not page.player_header.avatar.isHidden()
    assert page.player_header.avatar._url, "主头像 URL 应来自玩家资料"
    print("[ok] 详情玩家行的账号小头像已删除（无空圆框 / 无异常空白），顶部主头像保留")

    # 8c) 职责栏：窄固定宽度 + 上下顶满玩家行 + 独立背景层级（不是悬浮 badge）
    assert 28 <= RoleIcon.COLUMN_WIDTH <= 32, RoleIcon.COLUMN_WIDTH
    assert row0.role.width() == RoleIcon.COLUMN_WIDTH, row0.role.width()
    assert row0.role.height() == row0.height() == PlayerRow.ROW_HEIGHT, (
        row0.role.height(), row0.height()
    )
    assert row0.role.y() == 0, f"职责栏上方仍有留白：{row0.role.y()}"
    row_margins = row0.layout().contentsMargins()
    assert row_margins.top() == 0 and row_margins.bottom() == 0, row_margins
    role_src = inspect.getsource(RoleIcon.paintEvent)
    assert "fillRect(self.rect()" in role_src, "职责栏背景未铺满整行"
    assert "addRoundedRect" not in role_src, "职责栏不应再是圆角悬浮小方块"
    # 职责素材仍严格来自 assets/roles
    from ui.assets import ROLE_FILES as _ROLE_FILES

    assert _ROLE_FILES["tank"][0] == "roles/tank.png"
    assert _ROLE_FILES["flex"][0] == "roles/flex.png"
    print(
        f"[ok] 职责栏 {RoleIcon.COLUMN_WIDTH}px 宽、{row0.role.height()}px 高上下顶满玩家行，"
        "有独立背景层级，不是悬浮 badge"
    )

    # 8c-2) 组排标记：粗竖线，无顶/底横线与括号拐角；无组排不留大块空白
    assert 4 <= PartyIndicator.LINE_WIDTH <= 6, PartyIndicator.LINE_WIDTH
    party_src = inspect.getsource(PartyIndicator.paintEvent)
    assert "drawLine" not in party_src, "组排标记不应再画横线 / 括号拐角"
    assert "drawRect" in party_src, "组排标记应为一条粗竖线矩形"
    assert row0.party.height() == PlayerRow.ROW_HEIGHT, row0.party.height()
    # 没有组排的玩家（玩家C）左侧不得保留大块空白：职责栏起点与组排行完全一致
    solo = friendly_rows[pos("玩家C")]
    assert not solo.party._active
    assert solo.role.x() == row0.role.x(), (solo.role.x(), row0.role.x())
    assert solo.role.x() <= PartyIndicator.WIDTH, f"职责栏左侧空白过大：{solo.role.x()}px"
    print(
        f"[ok] 组排标记为 {PartyIndicator.LINE_WIDTH}px 粗竖线（无括号拐角）；"
        f"无组排玩家左侧仅 {solo.role.x()}px，不浪费空间"
    )

    # 8c-3) 本人行高亮：背景层级 + 强调边，克制且无大标签 / 动画
    row_paint_src = inspect.getsource(PlayerRow.paintEvent)
    assert "ACCENT" in row_paint_src and "fillRect" in row_paint_src
    assert "drawText" not in row_paint_src, "本人高亮不应画任何文字标签"
    for banned in ("QPropertyAnimation", "QTimer", "startTimer", "setGraphicsEffect"):
        assert banned not in row_paint_src, f"本人高亮不应出现 {banned}"

    # 8d) 当前查询账号本人高亮：只按完整 BattleTag（Demo#1234）确定性识别
    assert page._customer_token == "ct-demo"
    self_rows = [r for r in friendly_rows if r.is_self()]
    assert len(self_rows) == 1, [r._data["name"] for r in self_rows]
    assert self_rows[0]._data["name"] == "Demo#1234", self_rows[0]._data["name"]
    # 本人行的稳定 ID 与当前账号不一致，仍被识别为本人 → BattleTag 是唯一依据
    assert self_rows[0]._data["customer_token"] == "ct-not-self"
    assert self_rows[0]._data["bnet_id"] != "42"
    # 持有本人 customer_token（ct-demo）但 BattleTag 不同的玩家D 不是本人
    holder = next(r for r in friendly_rows if r._data["name"].startswith("玩家D"))
    assert holder._data["customer_token"] == "ct-demo" and not holder.is_self()
    assert not any(r.is_self() for r in page.detail_page.enemy._rows if r.isVisible())
    # 纯函数层：稳定 ID 相同不能代替 BattleTag，ID 不同不能否决 BattleTag
    assert not D.is_self_player({"customer_token": "ct-demo", "name": "别的名字"}, self_token="ct-demo")
    assert not D.is_self_player({"customer_token": "ct-demo", "name": "玩家D#1004"},
                                self_token="ct-demo", self_battle_tag="Demo#1234")
    assert D.is_self_player({"customer_token": "ct-other", "name": "demo#1234"},
                            self_token="ct-demo", self_battle_tag="Demo#1234")
    assert D.is_self_player({"name": "demo#1234"}, self_battle_tag="Demo#1234")
    print("[ok] 详情本人高亮：只按完整 BattleTag 识别，稳定账号 ID 不参与判定")

    # 8e) 详情排序规则（纯数据层）
    def _p(name, role, friends=()):
        return {"name": name, "bnet_id": name, "role_type": role, "friend_bnet_ids": list(friends)}

    # 无组排：重装 → 输出 → 输出 → 支援 → 支援
    solo = D.build_team_layout([
        _p("d1", "dps"), _p("s1", "healer"), _p("t", "tank"), _p("d2", "dps"), _p("s2", "healer"),
    ])
    assert [p["role_type"] for p in solo.players] == ["tank", "dps", "dps", "healer", "healer"]
    assert [p["name"] for p in solo.players] == ["t", "d1", "d2", "s1", "s2"]
    assert all(pid is None for pid in solo.party_ids)

    # 输出 + 支援双排：两人连续，允许出现 输出 → 支援 → 输出
    duo = D.build_team_layout([
        _p("t", "tank"), _p("dA", "dps"), _p("dB", "dps", ["sA"]), _p("sA", "healer", ["dB"]),
        _p("sB", "healer"),
    ])
    duo_names = [p["name"] for p in duo.players]
    assert duo_names[0] == "t", duo_names
    assert abs(duo_names.index("dB") - duo_names.index("sA")) == 1, duo_names
    assert duo.party_ids[duo_names.index("dB")] == duo.party_ids[duo_names.index("sA")]
    assert duo.party_ids[duo_names.index("dA")] is None

    # 重装 + 支援双排：重装第一，支援紧随其后
    tank_duo = D.build_team_layout([
        _p("dA", "dps"), _p("t", "tank", ["sA"]), _p("sA", "healer", ["t"]),
        _p("dB", "dps"), _p("sB", "healer"),
    ])
    td_names = [p["name"] for p in tank_duo.players]
    assert td_names[0] == "t" and td_names[1] == "sA", td_names

    # 三排 / 四排：重装第一，组排成员连续
    for friends in (("sA", "dB"), ("sA", "dB", "sB")):
        members = {"t"} | set(friends)
        rows = [
            _p("dA", "dps"),
            _p("t", "tank", friends),
            _p("dB", "dps", [f for f in ("t", *friends) if f != "dB"]),
            _p("sA", "healer", [f for f in ("t", *friends) if f != "sA"]),
            _p("sB", "healer", [f for f in ("t", *friends) if f != "sB"]),
        ]
        layout = D.build_team_layout(rows)
        order_names = [p["name"] for p in layout.players]
        assert order_names[0] == "t", (friends, order_names)
        idx = sorted(order_names.index(m) for m in members)
        assert idx == list(range(idx[0], idx[0] + len(members))), (friends, order_names)
        assert idx[0] == 0, (friends, order_names)
    print("[ok] 详情排序：重装恒为第一行；组排连续时允许输出 / 支援交错；三排 / 四排同样成立")

    # 单局详情同样支持一局多英雄
    multi_row = next((r for r in friendly_rows if r._data["name"].startswith("玩家A")), None)
    assert multi_row is not None
    assert len(multi_row._data.get("heroes") or []) == 3, multi_row._data.get("heroes")
    # 带使用时间时用得最多的作为主英雄（D.Va 480s > Winston 120s > Orisa 60s）
    assert multi_row._data["heroes"][0]["hero_name_raw"] == "D.Va", multi_row._data["heroes"][0]
    assert multi_row.hero.hero_count() == 3, multi_row.hero.hero_count()
    assert len(multi_row.hero._secondaries) == 2
    solo_row = friendly_rows[pos("玩家C")]
    assert solo_row.hero.hero_count() == 1, solo_row.hero.hero_count()
    print("[ok] 单局详情多英雄：主英雄按使用时间排序 + 2 个次英雄小头像，单英雄行仍只有 1 张")
    unranked_row = friendly_rows[pos("玩家C")]
    assert unranked_row.rank_badge._text == "未定级", unranked_row.rank_badge._text
    # 未定级不能拿到青铜图标
    assert lib.rank_key("未定级") == "", lib.rank_key("未定级")
    assert row0.rank_badge._text.startswith("翡翠"), row0.rank_badge._text
    assert lib.rank_key("翡翠 4") == "emerald", "翡翠段位图标未解析"
    assert not row0.rank_badge._pixmap.isNull(), "PlayerRow 缺少段位图标"
    enemy_rows = [r for r in page.detail_page.enemy._rows if r.isVisible()]
    assert len(enemy_rows) == 3
    enemy_names = [r._data["name"] for r in enemy_rows]
    # 敌方：重装 G 单排必须第一，H + I 双排在后且连续。
    assert enemy_names[0].startswith("敌人G"), enemy_names
    assert not enemy_rows[0].party._active, "单排重装不应显示组排竖线"
    assert enemy_rows[1].party._active and enemy_rows[2].party._active, enemy_names
    assert enemy_rows[1].party._first and enemy_rows[2].party._last, enemy_names
    assert not page.detail_page.map_banner._view == {}
    page.detail_page.map_banner.resize(900, 200)
    page.detail_page.map_banner.repaint()
    assert not page.detail_page.map_banner._background.isNull(), "详情页缺少地图背景"
    print("[ok] 单局详情：双队 PlayerRow、组排连续排列、连续竖线、两组独立、单排无线、未定级正确")

    # 9) 返回保留筛选与滚动
    page._close_detail()
    pump(80)
    assert page.stack.currentIndex() == 0
    assert page._selected_season == 24 and page._mode_filter == "all"
    print("[ok] 详情返回后保留赛季 / 筛选 / 滚动位置")

    # 9b) 详情缓存：再次打开同一局不再请求后端
    calls_before = len(fake.detail_calls)
    page.open_detail(target)
    pump(60)
    assert page.stack.currentIndex() == 1
    assert len(fake.detail_calls) == calls_before, "再次打开详情不应重新抓取"
    rows_again = [r for r in page.detail_page.friendly._rows if r.isVisible()]
    assert len(rows_again) == 6, len(rows_again)
    assert next(r for r in rows_again if r._data["name"].startswith("玩家A")).hero.hero_count() == 3
    page._close_detail()
    pump(60)
    print("[ok] 详情缓存命中：再次打开同一局直接复用，未新增后端请求")

    # 10) 竞逐区间：同赛季起止、跳过非竞技、包含端点
    season24 = sorted(
        [v for v in page._matches if v["season"] == 24],
        key=lambda v: v["begin_ts"],
    )
    comp24 = [v for v in season24 if D.is_preset_competitive(v)]
    start_view, end_view = comp24[2], comp24[12]
    page._set_drive_start(start_view["match_id"])
    page._set_drive_end(end_view["match_id"])
    summary = page._drive.summary(page._matches)
    assert summary and summary["ok"], summary
    ids = [m["match_id"] for m in summary["matches"]]
    assert ids[0] == start_view["match_id"] and ids[-1] == end_view["match_id"], "起止比赛必须包含"
    assert all(D.is_preset_competitive(m) for m in summary["matches"]), "区间内混入非竞技比赛"
    assert summary["skipped_count"] > 0, "区间内应存在被跳过的快速比赛"
    assert summary["window_count"] > len(summary["matches"])
    assert len(summary["matches"]) == 11, len(summary["matches"])
    assert page.drive_bar.import_btn.isEnabled()
    # 竞逐区间信息改为按钮 tooltip，不再常驻一整行。
    assert "S24" in page.drive_bar.status_text()
    assert page.drive_bar.status_text() == page.drive_bar.import_btn.toolTip()
    print(f"[ok] 竞逐区间：S24 起止包含端点，跳过 {summary['skipped_count']} 场非竞技，导入 {len(summary['matches'])} 场")

    # 11) 跨赛季终点必须被拒绝
    other_season_match = next(v for v in page._matches if v["season"] == 23 and D.is_preset_competitive(v))
    page._set_drive_end(other_season_match["match_id"])
    assert page._drive.end_id == end_view["match_id"], "跨赛季终点不应被接受"
    assert page.banner.property("level") == "error"
    print("[ok] 跨赛季终点被拒绝，竞逐仍属于单一赛季")

    # 12) 切换赛季清除另一个赛季的竞逐边界
    page._on_season_changed(23)
    assert page._drive.start_id == "" and page._drive.end_id == "", "切换赛季未清除区间"
    page._on_season_changed(24)
    print("[ok] 切换赛季后清除未完成的竞逐区间")

    # 13) 导入 + match_id 去重
    page._set_drive_start(start_view["match_id"])
    page._set_drive_end(end_view["match_id"])
    summary = page._drive.summary(page._matches)
    records = D.drive_records_from_matches(summary["matches"])
    merged, dup, added = D.dedupe_drive_records([], records)
    assert (dup, added) == (0, len(records)), (dup, added)
    win.state.set_records(merged)
    assert len(win.state.records) == len(records)
    merged2, dup2, added2 = D.dedupe_drive_records(win.state.records, records)
    assert dup2 == len(records) and added2 == 0, (dup2, added2)
    # 竞逐只导入区间，不是整个赛季
    assert len(records) < len(comp24), "不应把整个赛季当成一次竞逐"
    print(f"[ok] 竞逐导入：{len(records)} 场写入计算器；重复导入被 match_id 去重（{dup2} 场已存在）")

    # 14) 计分器仍然正常工作
    win.state.set_records([{"result": "W"}, {"result": "W"}, {"result": "L"}])
    assert win.state.current_score > 0
    print(f"[ok] 竞逐计算器仍可用，当前分数 {win.state.current_score}")

    # 14b) 竞逐计算器默认胜率 = 最近比赛（竞技 + 快速合并）胜率
    pred = win.prediction_page
    recent = page.recent_form_summary()
    expect_pct = int(round(recent["win_rate"]))
    # 首次进入：自动写入默认值
    pred.winrate_user_modified = False
    pred._winrate_source_account = ""
    assert pred.apply_recent_win_rate(recent["win_rate"], page._customer_token) is True
    assert pred.win_spin.value() == expect_pct, (pred.win_spin.value(), expect_pct)
    assert pred.winrate_user_modified is False
    assert pred.win_spin.isEnabled() and pred.win_slider.isEnabled(), "胜率输入不得被锁死"
    # 用户手动修改
    for manual in (55, 60, 70):
        pred.win_spin.setValue(manual)
        assert pred.win_spin.value() == manual
    assert pred.winrate_user_modified is True, "用户手改后应记录 winrate_user_modified"
    # 后台刷新战绩：不得覆盖用户输入
    page.search()
    for _ in range(60):
        pump(80)
        if not page._query_busy:
            break
    assert not page._query_busy
    assert pred.win_spin.value() == 70, pred.win_spin.value()
    assert pred.apply_recent_win_rate(33.0, page._customer_token) is False
    assert pred.win_spin.value() == 70, "后台刷新不得覆盖用户手改的胜率"
    # 切换查询账号：手改状态不继承，新玩家用自己的最近胜率初始化
    assert pred.apply_recent_win_rate(42.0, "ct-other-player") is True
    assert pred.win_spin.value() == 42, pred.win_spin.value()
    assert pred.winrate_user_modified is False
    # 没有最近比赛数据时不会自动填 0%
    before = pred.win_spin.value()
    empty_form = D.recent_form_summary([], page._current_season)
    assert empty_form["win_rate"] is None
    page.recent_form.set_summary(empty_form)
    pump(30)
    assert pred.win_spin.value() == before != 0, pred.win_spin.value()
    print(
        f"[ok] 竞逐默认胜率：首次写入 {expect_pct}%，手改 70% 后刷新不被覆盖，"
        "切换账号重新初始化为 42%，无数据时不填 0%"
    )

    # 20) 设置页缓存管理
    from pathlib import Path as _Path

    from ui import cache_manager as CM

    st = win.settings_page
    detail_dir = CM.detail_cache_dir()
    avatar_dir = CM.avatar_cache_dir()
    detail_dir.mkdir(parents=True, exist_ok=True)
    avatar_dir.mkdir(parents=True, exist_ok=True)
    other_dir = CM.PROJECT_CACHE_DIR / "smoke_other_cache"
    other_dir.mkdir(parents=True, exist_ok=True)
    (detail_dir / "smoke_detail.json").write_bytes(b"x" * 2048)
    (avatar_dir / "smoke_avatar.png").write_bytes(b"y" * 1024)
    (other_dir / "misc.bin").write_bytes(b"z" * 512)

    usage = CM.collect_usage()
    assert usage.detail >= 2048 and usage.avatar >= 1024 and usage.other >= 512
    assert usage.total == usage.detail + usage.avatar + usage.other
    st._apply_cache_usage(usage)
    assert st.cache_total.text() == CM.format_size(usage.total)
    assert st._cache_size_labels["detail"].text() == CM.format_size(usage.detail)
    assert st._cache_size_labels["avatar"].text() == CM.format_size(usage.avatar)
    assert st._cache_size_labels["other"].text() == CM.format_size(usage.other)
    print(
        f"[ok] 设置页缓存占用：详情 {CM.format_size(usage.detail)} / "
        f"头像 {CM.format_size(usage.avatar)} / 其他 {CM.format_size(usage.other)} / "
        f"总计 {CM.format_size(usage.total)}"
    )

    # 清理详情缓存：文件被删除，之后仍可重新请求并重新生成
    page._detail_cache = MatchDetailCache(str(detail_dir))
    page._detail_cache.put("ct-demo", target, {"scoreboard_players": []})
    assert list(detail_dir.glob("*.json")), "详情缓存未写盘"
    freed = CM.clear_detail_cache()
    assert freed > 0 and not list(detail_dir.glob("*.json")), "详情缓存未被清理"
    page.drop_runtime_caches("detail")
    assert page._detail_cache.get("ct-demo", target) is None, "清理后不应命中缓存"
    calls_before = len(fake.detail_calls)
    page.open_detail(target)
    for _ in range(40):
        pump(100)
        if len(fake.detail_calls) > calls_before:
            break
    assert len(fake.detail_calls) > calls_before, "清理详情缓存后应重新请求"
    for _ in range(40):
        pump(100)
        if [r for r in page.detail_page.friendly._rows if r.isVisible()]:
            break
    assert [r for r in page.detail_page.friendly._rows if r.isVisible()], "详情未能重新渲染"
    assert list(detail_dir.glob("*.json")), "重新打开后应重新生成详情缓存"
    page._close_detail()
    pump(60)
    print("[ok] 清理详情缓存后详情可重新请求并重新生成缓存，页面不报错")

    # 清理头像缓存：只删 cache/avatars，不动 assets
    (avatar_dir / "smoke_avatar.png").write_bytes(b"y" * 1024)
    freed = CM.clear_avatar_cache()
    assert freed > 0 and not list(avatar_dir.glob("*.png")), "头像缓存未被清理"
    page.drop_runtime_caches("avatar")
    assert page._avatars.peek("https://nosdn.127.net/demo.png", 24) is None
    assert page.player_header.avatar._url, "主头像 URL 应保留，下次自动重新下载"
    print("[ok] 清理头像缓存后运行时头像文件被删除，下次显示会重新下载")

    # 清理全部运行缓存：assets / 授权 / 默认账号 / 用户设置 / 竞逐记录都不受影响
    (detail_dir / "again.json").write_bytes(b"x" * 100)
    (avatar_dir / "again.png").write_bytes(b"y" * 100)
    (other_dir / "again.bin").write_bytes(b"z" * 100)
    win.state.set_records([{"result": "W"}, {"result": "L"}])
    records_before = list(win.state.records)
    # 绝不能为了冒烟测试写入真实用户的默认关联账号。之前这里把 QSettings
    # 覆盖成 Demo#1234 / ****1234，会导致实际默认玩家在测试后失效。
    account_before = st._settings.value("dashen/account_name", None)
    role_mask_before = st._settings.value("dashen/role_id_masked", None)
    from ui.assets import ASSETS_DIR

    assets_before = sorted(p.name for p in ASSETS_DIR.iterdir())
    CM.clear_all_runtime_cache()
    assert not list(detail_dir.glob("*.json"))
    assert not list(avatar_dir.glob("*.png"))
    assert not other_dir.exists() or not list(other_dir.iterdir())
    assert ASSETS_DIR.exists(), "assets 目录被误删"
    for sub in ("heroes", "maps", "ranks", "roles", "map_modes"):
        assert (ASSETS_DIR / sub).exists() and list((ASSETS_DIR / sub).iterdir()), f"assets/{sub} 被误删"
    assert sorted(p.name for p in ASSETS_DIR.iterdir()) == assets_before
    assert _Path("config.json").exists(), "config.json 被误删"
    assert _Path("drive_core.py").exists(), "项目源码被误删"
    assert st._settings.value("dashen/account_name", None) == account_before, "默认关联账号被误删或修改"
    assert st._settings.value("dashen/role_id_masked", None) == role_mask_before, "授权信息被误删或修改"
    assert win.state.records == records_before, "竞逐记录被误删"
    print("[ok] 清理全部运行缓存：assets / 授权 / 默认账号 / 用户设置 / 竞逐记录 / 源码全部保留")

    # 路径安全：assets 与 cache 父目录绝不允许被当作缓存删除
    assert not CM.is_safe_cache_path(ASSETS_DIR)
    for sub in ("heroes", "maps", "ranks", "roles", "map_modes"):
        assert not CM.is_safe_cache_path(ASSETS_DIR / sub)
    assert not CM.is_safe_cache_path(CM.PROJECT_CACHE_DIR.parent)
    assert not CM.is_safe_cache_path(_Path("config.json").resolve())
    assert CM.is_safe_cache_path(avatar_dir) and CM.is_safe_cache_path(detail_dir)
    print("[ok] 缓存清理路径白名单：assets / cache 父目录 / 配置文件均被拒绝")

    # 设置页按钮路径：清理详情 / 头像缓存（“全部”会弹确认框，这里只走无确认的两项）
    detail_dir.mkdir(parents=True, exist_ok=True)
    avatar_dir.mkdir(parents=True, exist_ok=True)
    (detail_dir / "btn_detail.json").write_bytes(b"x" * 300)
    (avatar_dir / "btn_avatar.png").write_bytes(b"y" * 300)
    cleared: List[str] = []
    st.cacheCleared.connect(cleared.append)
    st._clear_cache("detail")
    for _ in range(40):
        pump(80)
        if "detail" in cleared:
            break
    assert "detail" in cleared, "清理详情缓存未发出 cacheCleared 信号"
    assert not list(detail_dir.glob("*.json"))
    st._clear_cache("avatar")
    for _ in range(40):
        pump(80)
        if "avatar" in cleared:
            break
    assert "avatar" in cleared
    assert not list(avatar_dir.glob("*.png"))
    assert st.banner.property("level") == "ok", st.banner.property("level")
    # 重新统计走后台线程，不应阻塞或报错
    st.refresh_cache_usage()
    for _ in range(40):
        pump(80)
        if st.cache_total.text() != "统计中…":
            break
    assert st.cache_total.text() != "统计中…", st.cache_total.text()
    assert st.clear_all_btn.isEnabled() and st.clear_detail_btn.isEnabled()
    print("[ok] 设置页清理按钮可用：详情 / 头像缓存被清理并通知其它页面同步内存缓存")

    # 缓存目录不存在时统计与清理都不报错
    import shutil as _shutil

    missing_dir = CM.PROJECT_CACHE_DIR / "smoke_missing"
    _shutil.rmtree(missing_dir, ignore_errors=True)
    assert CM.dir_size(missing_dir) == 0
    assert CM.format_size(0) == "0 B"
    _shutil.rmtree(avatar_dir, ignore_errors=True)
    _shutil.rmtree(detail_dir, ignore_errors=True)
    assert CM.collect_usage().total >= 0
    assert CM.clear_detail_cache() == 0
    assert CM.clear_avatar_cache() == 0
    assert CM.clear_all_runtime_cache() >= 0
    print("[ok] 缓存目录不存在时占用按 0 B 处理，清理操作不报错")

    # 15) 模拟赛季推进到 S25，界面自动变成 S25/S24/S23（不改任何源码）
    fake.current_season = 25
    page._current_season = None
    page._selected_season = None
    page.query_edit.setText("Demo#1234")
    page.search()
    for _ in range(60):
        pump(100)
        if page._current_season == 25:
            break
    assert page._current_season == 25, page._current_season
    assert page._seasons == [25, 24, 23], page._seasons
    labels = [b.text() for b in page.season_selector._buttons]
    assert labels == ["S25", "S24", "S23"], labels
    assert "S25" in page.season_header._text
    print(f"[ok] 后端切到 S25 后界面自动显示 {labels}，无需修改前端源码")

    # 16) 再推进到 S26
    fake.current_season = 26
    page._current_season = None
    page._selected_season = None
    page.search()
    for _ in range(60):
        pump(100)
        if page._current_season == 26:
            break
    assert [b.text() for b in page.season_selector._buttons] == ["S26", "S25", "S24"]
    print("[ok] 继续推进到 S26 自动显示 S26/S25/S24")

    # 17) 全局 UI 不含硬编码赛季号
    import re
    from pathlib import Path

    pattern = re.compile(r"""(?<![\w"'])S2[0-9](?![\w])""")
    for path in sorted(Path("ui").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            line = text[: match.start()].count("\n") + 1
            raise AssertionError(f"{path}:{line} 存在硬编码赛季 {match.group(0)}")
        assert "CURRENT_SEASON" not in text, f"{path} 存在 CURRENT_SEASON 常量"
    print("[ok] ui/ 下不存在硬编码 S2x 赛季号或 CURRENT_SEASON 常量")

    # 18) 缩放不崩
    for w, h in ((1100, 700), (1680, 1000), (1280, 820)):
        win.resize(w, h)
        pump(80)
    print("[ok] 窗口缩放正常")

    # 19) 缺失素材报告：缺图只走统一 placeholder，并被记录下来
    from ui.assets import missing_assets

    missing = missing_assets()
    if missing:
        for kind, keys in missing.items():
            print(f"[warn] 缺少本地素材 {kind}: {', '.join(keys[:12])}" + (" …" if len(keys) > 12 else ""))
    else:
        print("[ok] 本次运行未出现缺失素材")

    # 20) 真实 QSettings 未被测试污染：默认账号 / 授权信息逐键还原
    from PySide6.QtCore import QSettings as _QSettings

    # 等待已有预测任务完成，避免测试退出时销毁仍在运行的 QThread。
    import time
    win.prediction_page._debounce.stop()
    deadline = time.monotonic() + 45
    from PySide6.QtCore import QThread
    while any(t.isRunning() for t in win.prediction_page.findChildren(QThread)):
        assert time.monotonic() < deadline, "预测测试线程未及时结束"
        pump(50)
    win.close()  # 写入的 geometry 也仅存在于临时 INI
    restore_settings()
    live = _QSettings("ow_drive_split", "drive_calculator")
    assert sorted(live.allKeys()) == sorted(settings_snapshot.keys()), (
        sorted(live.allKeys()), sorted(settings_snapshot.keys())
    )
    for key, value in settings_snapshot.items():
        assert live.value(key) == value, f"临时设置 {key} 未恢复"
    print(
        f"[ok] QSettings 隔离于临时 INI（{len(settings_snapshot)} 个键全部还原，"
        "默认关联账号 / 授权信息 / 窗口几何保持原样）"
    )

    print("\n国服战绩 UI 冒烟测试全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
