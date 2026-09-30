# -*- coding: utf-8 -*-
"""单局详情页：顶部地图 Banner + 左右双队 PlayerRow（含组排连续竖线）。

视觉基准是 ui_reference/match_detail_reference.png：
- 顶部真实地图图 + 深色渐变遮罩，右侧 VICTORY/DEFEAT 与比分；
- 下半部分「我方队伍 / 敌方队伍」两栏，每人一行紧凑数据；
- 同组排玩家在队内连续排列，职责图标列**左侧**一条连续细竖线。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import owstats_data as D
from .assets import AssetLibrary, AvatarCache
from .ow_ai_review import AIReviewEntryCard
from .ow_widgets import BanPanel, MapBanner, PlayerRow
from .theme import C
from .widgets import Banner, make_button


class TeamPanel(QFrame):
    """一支队伍的玩家列表。行控件按需创建并复用。"""

    def __init__(
        self,
        library: AssetLibrary,
        avatars: AvatarCache,
        title: str,
        color: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TeamPanel")
        self._library = library
        self._avatars = avatars
        self._rows: List[PlayerRow] = []
        # 本局竞技队列类型（preset / open / ""），只用于职责图标显示。
        self._queue_kind = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        head = QLabel(title)
        head.setObjectName("TeamTitle")
        head.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: 600;")
        lay.addWidget(head)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        # 行间距设为 0，让同组竖线在相邻行之间完全连续，不出现断口。
        self._rows_layout.setSpacing(0)
        lay.addLayout(self._rows_layout)

        self.empty = QLabel("暂无公开玩家数据")
        self.empty.setObjectName("EmptyHint")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(60)
        lay.addWidget(self.empty)
        lay.addStretch(1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_queue_kind(self, queue_kind: str) -> None:
        """设置本局队列类型。开放职责对局里每行职责栏统一显示 flex.png。"""
        self._queue_kind = str(queue_kind or "")

    def set_layout_data(self, layout: "D.TeamLayout") -> None:
        players = layout.players
        for row in self._rows:
            row.setVisible(False)
        # 队内最多的次英雄数：所有行统一按它预留英雄列宽度，玩家信息列左边缘才会对齐。
        # 全队都是单英雄时预留 0，英雄头像紧贴玩家信息，和游戏内记分板一致。
        extra_slots = max(
            (len([h for h in (p.get("heroes") or []) if isinstance(h, dict)]) - 1 for p in players),
            default=0,
        )
        for index, player in enumerate(players):
            while index >= len(self._rows):
                # 玩家行不再显示账号小头像，因此不传 AvatarCache。
                row = PlayerRow(self._library, parent=self)
                self._rows_layout.addWidget(row)
                self._rows.append(row)
            row = self._rows[index]
            row.hero.set_reserved_slots(extra_slots)
            row.set_player(player, layout.party_span(index), self._queue_kind)
            row.setVisible(True)
        self.empty.setVisible(not players)


class MatchDetailPage(QWidget):
    """详情页。通过 QStackedWidget 与列表页同尺寸切换，不做小弹窗。"""

    backRequested = Signal()
    # 点击底部「AI 锐评」入口：页面容器负责切到本局 AI 锐评页。
    aiReviewRequested = Signal()

    def __init__(
        self,
        library: AssetLibrary,
        avatars: AvatarCache,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        self._library = library
        self._avatars = avatars
        # 当前查询账号标识：优先 customer_token，其次 bnet_id，最后才是 BattleTag。
        self._self_identity: Dict[str, Any] = {}
        self._self_token = ""
        self._self_bnet_id = ""
        self._self_battle_tag = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(scroll, 1)

        page = QWidget()
        page.setObjectName("Root")
        scroll.setWidget(page)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        back = make_button("← 返回战绩", "ghost")
        back.clicked.connect(self.backRequested.emit)
        head.addWidget(back, 0)
        self.title = QLabel("单局详情")
        self.title.setObjectName("DetailTitle")
        head.addWidget(self.title, 1)
        self.match_id_label = QLabel("")
        self.match_id_label.setObjectName("DetailMatchId")
        self.match_id_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        head.addWidget(self.match_id_label, 0)
        lay.addLayout(head)

        self.banner_msg = Banner()
        lay.addWidget(self.banner_msg)

        self.map_banner = MapBanner(library, page)
        lay.addWidget(self.map_banner)

        self.summary = QLabel("")
        self.summary.setObjectName("DetailSummary")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)

        # 英雄禁用：比赛基础信息之后、玩家列表之前；左右两卡与下方两队同列宽对齐。
        # 上游完全没有 Ban 字段（如角斗 / 快速）时整行隐藏，不留空白。
        self.bans = QWidget(page)
        bans_lay = QHBoxLayout(self.bans)
        bans_lay.setContentsMargins(0, 0, 0, 0)
        bans_lay.setSpacing(12)
        self.friendly_bans = BanPanel(library, "我方禁用", C.WIN, self.bans)
        self.enemy_bans = BanPanel(library, "敌方禁用", C.LOSE, self.bans)
        bans_lay.addWidget(self.friendly_bans, 1)
        bans_lay.addWidget(self.enemy_bans, 1)
        self.bans.setVisible(False)
        lay.addWidget(self.bans)

        teams = QHBoxLayout()
        teams.setSpacing(12)
        self.friendly = TeamPanel(library, avatars, "我方队伍", C.WIN, page)
        self.enemy = TeamPanel(library, avatars, "敌方队伍", C.LOSE, page)
        teams.addWidget(self.friendly, 1)
        teams.addWidget(self.enemy, 1)
        lay.addLayout(teams)

        # AI 锐评入口：放在所有现有详情内容之后，全宽，与上方内容同宽。
        # 只有本局详情加载完成后才可用（AI 直接使用已获取的 detail，不重新请求）。
        self.ai_entry = AIReviewEntryCard(page)
        self.ai_entry.openRequested.connect(self.aiReviewRequested.emit)
        self.ai_entry.setVisible(False)
        lay.addWidget(self.ai_entry)
        lay.addStretch(1)

    # ---------------- 状态 ----------------
    def set_self_identity(
        self,
        *,
        customer_token: str = "",
        bnet_id: str = "",
        battle_tag: str = "",
        identity: Optional[Dict[str, Any]] = None,
    ) -> None:
        """设置「当前查询账号本人」的稳定标识，用于详情行高亮。"""
        self._self_identity = dict(identity or {})
        self._self_token = str(customer_token or "").strip()
        self._self_bnet_id = str(bnet_id or "").strip()
        self._self_battle_tag = str(battle_tag or "").strip()

    def show_loading(self, match_view: Dict[str, Any]) -> None:
        self.map_banner.set_match(match_view)
        self.title.setText(str(match_view.get("map_name") or "单局详情"))
        match_id = str(match_view.get("match_id") or "")
        self.match_id_label.setText(f"ID: {match_id}" if match_id else "")
        self.summary.setText("")
        queue_kind = str(match_view.get("queue_kind") or "")
        self.friendly.set_queue_kind(queue_kind)
        self.enemy.set_queue_kind(queue_kind)
        self.friendly.set_layout_data(D.TeamLayout())
        self.enemy.set_layout_data(D.TeamLayout())
        self.bans.setVisible(False)
        self.ai_entry.setVisible(False)
        self.banner_msg.show_message("正在加载单局全员详情…", "info")

    def show_error(self, message: str) -> None:
        self.banner_msg.show_message(message, "error")

    def set_detail(self, match_view: Dict[str, Any], detail: Dict[str, Any]) -> None:
        """detail 为 match_all_players_detail() 的返回。"""
        data = dict(detail or {})
        scoreboard = [dict(x) for x in data.get("scoreboard_players") or [] if isinstance(x, dict)]
        full_players = [dict(x) for x in data.get("players") or [] if isinstance(x, dict)]
        friendly, enemy = D.split_detail_teams(
            scoreboard,
            full_players,
            self_identity=self._self_identity,
            self_token=self._self_token,
            self_bnet_id=self._self_bnet_id,
            self_battle_tag=self._self_battle_tag,
        )

        merged = dict(match_view)
        normalized = data.get("normalized") if isinstance(data.get("normalized"), dict) else {}
        for key in ("map_name", "map_mode", "map_mode_zh"):
            if normalized.get(key):
                merged[key] = normalized.get(key)
        root = data.get("detail_root") if isinstance(data.get("detail_root"), dict) else {}
        if root:
            # 详情根节点的比分/时长比列表条目更准确时优先采用。
            for src, dst in (("teamScore", "team_score"), ("opponentScore", "opponent_score")):
                if root.get(src) is not None:
                    try:
                        merged[dst] = int(float(root.get(src)))
                    except (TypeError, ValueError):
                        pass
        self.map_banner.set_match(merged)
        self.title.setText(str(merged.get("map_name") or "单局详情"))

        # 队列类型只取数据层已解析好的 queue_kind（预设 / 开放），不在这里重算模式。
        queue_kind = str(merged.get("queue_kind") or "")
        self.friendly.set_queue_kind(queue_kind)
        self.enemy.set_queue_kind(queue_kind)
        self.friendly.set_layout_data(friendly)
        self.enemy.set_layout_data(enemy)

        # Ban 数据直接取详情根节点现成字段（teamBanHeroGuids / enemyBanHeroGuids），不额外请求。
        friendly_bans, enemy_bans = D.detail_ban_heroes(root)
        has_bans = friendly_bans is not None or enemy_bans is not None
        self.friendly_bans.set_heroes(friendly_bans or [])
        self.enemy_bans.set_heroes(enemy_bans or [])
        self.bans.setVisible(has_bans)

        parties = sum(1 for pid in friendly.party_ids if pid) + sum(1 for pid in enemy.party_ids if pid)
        bits: List[str] = [f"公开玩家 {len(friendly.players) + len(enemy.players)} 名"]
        if parties:
            bits.append(f"识别到组排玩家 {parties} 名")
        if data.get("all_players_supported"):
            available = int(data.get("available_player_count") or 0)
            total = int(data.get("player_count") or 0)
            bits.append(f"英雄明细 {available}/{total}")
        self.summary.setText(" · ".join(bits))
        self.ai_entry.setVisible(bool(scoreboard or full_players))

        warnings = [str(w) for w in data.get("warnings") or []]
        if not scoreboard:
            self.banner_msg.show_message("上游未返回本局公开玩家数据。", "warn")
        elif data.get("all_players_supported") is False:
            self.banner_msg.show_message(
                "角斗对局上游不提供全员英雄明细，已显示可用记分板数据。", "warn"
            )
        elif warnings:
            self.banner_msg.show_message(
                f"详情加载完成，{len(warnings)} 名玩家的英雄明细不可用（上游未公开）。", "warn"
            )
        else:
            self.banner_msg.clear()


__all__ = ["MatchDetailPage", "TeamPanel"]
