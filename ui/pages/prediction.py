# -*- coding: utf-8 -*-
"""竞逐预测页。

所有数值都来自 drive_core.predict()，本文件只负责展示。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from drive_core import DriveRules, band_index, checkpoint_floor, predict

from ..state import AppState
from ..theme import C
from ..widgets import (
    Card,
    FlowFrame,
    SegmentedControl,
    StatTile,
    StatusLine,
    field_label,
    hline,
    make_button,
)

QUANTILES = ("0.5000", "0.8000", "0.9000", "0.9500")
QUANTILE_LABELS = {"0.5000": "50%", "0.8000": "80%", "0.9000": "90%", "0.9500": "95%"}


# ----------------------------- 计算线程 -----------------------------

class _PredictWorker(QObject):
    finished = Signal(int, dict)
    failed = Signal(int, str)

    def __init__(self, token: int, params: Dict[str, Any], rules: DriveRules) -> None:
        super().__init__()
        self._token = token
        self._params = params
        self._rules = rules

    def run(self) -> None:
        try:
            result = predict(rules=self._rules, **self._params)
        except Exception as exc:  # 计算异常直接回报文本，不弹窗
            self.failed.emit(self._token, str(exc))
        else:
            self.finished.emit(self._token, result)


def _fmt_games(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        if math.isnan(value):
            return "≥上限"
        if math.isinf(value):
            return "∞"
        return f"{value:.1f}"
    return str(value)


def _fmt_quantile(value: Optional[int]) -> str:
    return "-" if value is None else str(int(value))


# ----------------------------- 页面 -----------------------------

class PredictionPage(QWidget):
    def __init__(self, state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._state = state
        self._token = 0
        self._thread: Optional[QThread] = None
        self._worker: Optional[_PredictWorker] = None
        self._last_result: Optional[Dict[str, Any]] = None
        # 胜率默认值状态：
        # - winrate_user_modified 为 True 表示用户手动改过胜率，
        #   此后任何后台刷新（重新查询战绩、最近 20 场胜率变化）都不得覆盖输入；
        # - _winrate_source_account 记录默认值来自哪个查询账号，
        #   切换到另一个玩家时重新初始化，不把上一个玩家的手改状态永久套给新玩家。
        self.winrate_user_modified = False
        self._winrate_source_account = ""
        self._applying_default_winrate = False
        self._recent_winrate: Optional[float] = None

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)
        self._debounce.timeout.connect(self._start_compute)

        self._build_ui()
        state.rulesChanged.connect(self._on_rules_changed)
        state.historyChanged.connect(self._on_history_changed)
        self._on_rules_changed()
        self._schedule()

    # ---------------- 构建 ----------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        root.addWidget(scroll, 1)

        page = QWidget()
        page.setObjectName("Root")
        scroll.setWidget(page)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 18, 20, 20)
        lay.setSpacing(14)

        title = QLabel("竞逐预测")
        title.setProperty("role", "title")
        lay.addWidget(title)
        sub = QLabel("按当前分数与预估胜率推算节点进度，概率由核心 DP 计算。")
        sub.setProperty("role", "subtitle")
        lay.addWidget(sub)

        body = QHBoxLayout()
        body.setSpacing(14)
        lay.addLayout(body, 1)

        body.addWidget(self._build_input_card(), 0)

        right = QVBoxLayout()
        right.setSpacing(14)
        right.addWidget(self._build_stage_card())
        right.addWidget(self._build_expect_card())
        right.addWidget(self._build_probability_card())
        right.addStretch(1)
        body.addLayout(right, 1)

    def _build_input_card(self) -> QWidget:
        card = Card("参数输入", "修改任意参数会自动重新计算")
        card.setMinimumWidth(268)
        card.setMaximumWidth(340)
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        self.score_spin = QSpinBox()
        self.score_spin.setRange(0, 4000)
        self.score_spin.setSingleStep(10)
        self.score_spin.setValue(0)
        self.score_spin.setAccelerated(True)
        self.score_spin.valueChanged.connect(self._schedule)
        card.body.addWidget(field_label("当前分数"))
        card.body.addWidget(self.score_spin)

        self.use_history_btn = make_button(
            "同步竞逐历史分数", "ghost", "把竞逐历史页算出的当前分数填进来"
        )
        self.use_history_btn.clicked.connect(self._pull_history_score)
        card.body.addWidget(self.use_history_btn)

        card.body.addWidget(hline())

        wr_row = QHBoxLayout()
        wr_row.setContentsMargins(0, 0, 0, 0)
        wr_row.addWidget(field_label("预估胜率"), 1)
        self.win_spin = QSpinBox()
        self.win_spin.setRange(0, 100)
        self.win_spin.setValue(55)
        self.win_spin.setSuffix(" %")
        self.win_spin.setFixedWidth(84)
        wr_row.addWidget(self.win_spin, 0)
        card.body.addLayout(wr_row)

        self.win_slider = QSlider(Qt.Horizontal)
        self.win_slider.setRange(0, 100)
        self.win_slider.setValue(55)
        card.body.addWidget(self.win_slider)
        self.win_slider.valueChanged.connect(self.win_spin.setValue)
        self.win_spin.valueChanged.connect(self.win_slider.setValue)
        self.win_spin.valueChanged.connect(self._schedule)
        # 任何非程序化的数值变化都视为「用户手动修改」。
        self.win_spin.valueChanged.connect(self._on_winrate_changed)

        self.winrate_hint = QLabel("")
        self.winrate_hint.setProperty("role", "hint")
        self.winrate_hint.setWordWrap(True)
        card.body.addWidget(self.winrate_hint)

        self.history_wr_btn = make_button("套用历史胜率", "ghost", "使用竞逐历史页的实际胜率")
        self.history_wr_btn.clicked.connect(self._pull_history_winrate)
        card.body.addWidget(self.history_wr_btn)

        card.body.addWidget(hline())

        card.body.addWidget(field_label("排队方式"))
        self.queue_seg = SegmentedControl(["单排", "组排"], 0)
        self.queue_seg.changed.connect(self._schedule)
        card.body.addWidget(self.queue_seg)

        card.body.addWidget(field_label("比赛模式"))
        self.mode_seg = SegmentedControl(["普通", "决斗"], 0)
        self.mode_seg.changed.connect(self._schedule)
        card.body.addWidget(self.mode_seg)

        card.body.addWidget(hline())

        n_row = QHBoxLayout()
        n_row.setContentsMargins(0, 0, 0, 0)
        n_row.addWidget(field_label("指定局数 N"), 1)
        self.n_spin = QSpinBox()
        self.n_spin.setRange(0, 2000)
        self.n_spin.setValue(20)
        self.n_spin.setSuffix(" 局")
        self.n_spin.setFixedWidth(96)
        self.n_spin.valueChanged.connect(self._schedule)
        n_row.addWidget(self.n_spin, 0)
        card.body.addLayout(n_row)

        self.rule_hint = QLabel("")
        self.rule_hint.setProperty("role", "hint")
        self.rule_hint.setWordWrap(True)
        card.body.addWidget(self.rule_hint)

        self.status = StatusLine()
        card.body.addWidget(self.status)
        card.body.addStretch(1)
        return card

    def _build_stage_card(self) -> QWidget:
        card = Card("阶段进度")
        flow = FlowFrame(margins=(0, 0, 0, 0), h_spacing=10, v_spacing=10, object_name="Plain")
        card.body.addWidget(flow)

        self.tile_stage = StatTile("当前阶段", "-")
        self.tile_next = StatTile("下一节点", "-", "分")
        self.tile_gap = StatTile("距下一节点", "-", "分", accent=True)
        for tile in (self.tile_stage, self.tile_next, self.tile_gap):
            tile.setMinimumWidth(184)
            flow.addWidget(tile)

        self.win_loss_hint = QLabel("")
        self.win_loss_hint.setProperty("role", "hint")
        self.win_loss_hint.setWordWrap(True)
        card.body.addWidget(self.win_loss_hint)
        return card

    def _build_expect_card(self) -> QWidget:
        card = Card("期望局数")
        flow = FlowFrame(margins=(0, 0, 0, 0), h_spacing=10, v_spacing=10, object_name="Plain")
        card.body.addWidget(flow)

        self.tile_exp_next = StatTile("到下一节点期望局数", "-", "局", accent=True)
        self.tile_exp_final = StatTile("到 4000 分期望局数", "-", "局", accent=True)
        for tile in (self.tile_exp_next, self.tile_exp_final):
            tile.setMinimumWidth(216)
            flow.addWidget(tile)
        return card

    def _build_probability_card(self) -> QWidget:
        card = Card("完成概率", "分位数为“首次达到目标所需局数”")

        self.quantile_target_hint = QLabel("目标：最终节点")
        self.quantile_target_hint.setProperty("role", "hint")
        card.body.addWidget(self.quantile_target_hint)

        flow = FlowFrame(margins=(0, 0, 0, 0), h_spacing=10, v_spacing=10, object_name="Plain")
        card.body.addWidget(flow)
        self.quantile_tiles: Dict[str, StatTile] = {}
        for key in QUANTILES:
            tile = StatTile(f"{QUANTILE_LABELS[key]} 概率完成", "-", "局")
            tile.setMinimumWidth(152)
            flow.addWidget(tile)
            self.quantile_tiles[key] = tile

        card.body.addWidget(hline())

        self.n_result_caption = QLabel("指定 N 局后的完成概率")
        self.n_result_caption.setProperty("role", "fieldLabel")
        card.body.addWidget(self.n_result_caption)

        n_flow = FlowFrame(margins=(0, 0, 0, 0), h_spacing=10, v_spacing=10, object_name="Plain")
        card.body.addWidget(n_flow)
        self.tile_n_next = StatTile("N 局内达到下一节点", "-", "%", accent=True)
        self.tile_n_final = StatTile("N 局内达到 4000", "-", "%", accent=True)
        for tile in (self.tile_n_next, self.tile_n_final):
            tile.setMinimumWidth(216)
            n_flow.addWidget(tile)

        self.n_bar = QProgressBar()
        self.n_bar.setRange(0, 1000)
        self.n_bar.setValue(0)
        self.n_bar.setFormat("到 4000 分：%p‰")
        self.n_bar.setTextVisible(False)
        card.body.addWidget(self.n_bar)
        return card

    # ---------------- 数据流 ----------------
    def _on_rules_changed(self) -> None:
        rules = self._state.rules
        final = rules.checkpoints[-1]
        self.score_spin.setRange(0, final)
        self.tile_exp_final.set_caption(f"到 {final} 分期望局数")
        self.tile_n_final.set_caption(f"N 局内达到 {final}")
        names = [rules.modes[k].name for k in ("normal", "duel") if k in rules.modes]
        if names:
            self.rule_hint.setText("规则：" + " / ".join(names))
        self._schedule()

    def _on_history_changed(self) -> None:
        summary = self._state.history.get("summary", {})
        games = int(summary.get("games", 0))
        if games:
            self.use_history_btn.setText(
                f"同步竞逐历史分数（{self._state.current_score}）"
            )
            self.history_wr_btn.setText(
                f"套用历史胜率（{summary.get('win_rate_percent', 0.0):.1f}%）"
            )
        else:
            self.use_history_btn.setText("同步竞逐历史分数")
            self.history_wr_btn.setText("套用历史胜率")
        self.use_history_btn.setEnabled(games > 0)
        self.history_wr_btn.setEnabled(games > 0)

    def _pull_history_score(self) -> None:
        self.score_spin.setValue(self._state.current_score)

    def _pull_history_winrate(self) -> None:
        pct = self._state.history.get("summary", {}).get("win_rate_percent", 0.0)
        # 手动点击套用历史胜率同样算「用户已经决定过胜率」。
        self.win_spin.setValue(int(round(pct)))

    # ---------------- 胜率默认值 ----------------
    def _on_winrate_changed(self, _value: int) -> None:
        if self._applying_default_winrate:
            return
        self.winrate_user_modified = True

    def apply_recent_win_rate(self, percent: float, account: str = "") -> bool:
        """把「当前赛季最近最多 20 场（竞技 + 快速合并）」胜率写为默认值。

        规则：
        - 第一次进入计算器 / 用户尚未手改过胜率 → 写入默认值；
        - 用户已手改过 → 不覆盖当前输入（后台刷新战绩也不会顶掉）；
        - 切换查询账号 → 重新初始化手改状态，新玩家可用自己的最近胜率初始化；
        - 没有最近比赛数据时调用方不会调用本方法，因此绝不会自动填 0%。
        返回是否实际写入了默认值。
        """
        try:
            value = float(percent)
        except (TypeError, ValueError):
            return False
        token = str(account or "")
        self._recent_winrate = value
        if token != self._winrate_source_account:
            # 换了查询账号：上一个玩家的手改状态不继承给新玩家。
            self._winrate_source_account = token
            self.winrate_user_modified = False
        if self.winrate_user_modified:
            self.winrate_hint.setText(
                f"最近比赛胜率 {value:.1f}%（已保留你手动修改的 {self.win_spin.value()}%）"
            )
            return False
        self._applying_default_winrate = True
        try:
            self.win_spin.setValue(int(round(value)))
        finally:
            self._applying_default_winrate = False
        self.winrate_hint.setText(f"默认胜率来自当前赛季最近比赛（竞技 + 快速）{value:.1f}%，可手动修改")
        return True

    def _schedule(self, *_args: Any) -> None:
        self.status.show_message("计算中…", "info")
        self._debounce.start()

    def _params(self) -> Dict[str, Any]:
        n = int(self.n_spin.value())
        probability_games = sorted({n, 5, 10, 15, 20, 30, 40, 60})
        return {
            "current_score": int(self.score_spin.value()),
            "win_rate": self.win_spin.value() / 100.0,
            "grouped": self.queue_seg.current_index() == 1,
            "mode": "duel" if self.mode_seg.current_index() == 1 else "normal",
            "probability_games": probability_games,
        }

    def _start_compute(self) -> None:
        self._token += 1
        token = self._token

        thread = QThread(self)
        worker = _PredictWorker(token, self._params(), self._state.rules)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_result)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_failed(self, token: int, message: str) -> None:
        if token != self._token:
            return
        self.status.show_message(f"计算失败：{message}", "error")

    def _on_result(self, token: int, result: Dict[str, Any]) -> None:
        if token != self._token:
            return
        self._last_result = result
        self._render(result)
        self.status.show_message("已更新", "ok")

    # ---------------- 渲染 ----------------
    def _render(self, result: Dict[str, Any]) -> None:
        rules = self._state.rules
        cps = rules.checkpoints
        score = int(result["current_score"])
        nxt = result.get("next_checkpoint")
        floor = checkpoint_floor(score, cps)
        idx = band_index(score, cps)

        if nxt is None:
            self.tile_stage.set_value("已满级")
            self.tile_stage.set_footer(f"{cps[-1]} 分已达成")
            self.tile_next.set_value("-")
            self.tile_next.set_footer("")
            self.tile_gap.set_value("0")
            self.tile_gap.set_footer("")
            for tile in (self.tile_exp_next, self.tile_exp_final):
                tile.set_value("0")
                tile.set_footer("")
            for key in QUANTILES:
                self.quantile_tiles[key].set_value("0")
            self.tile_n_next.set_value("100.0")
            self.tile_n_final.set_value("100.0")
            self.n_bar.setValue(1000)
            self.win_loss_hint.setText("当前分数已到最终节点，不再计分。")
            return

        self.tile_stage.set_value(f"第 {idx} 阶段")
        self.tile_stage.set_footer(f"{floor} → {nxt}")
        self.tile_next.set_value(str(nxt))
        span = nxt - floor
        self.tile_next.set_footer(f"本阶段跨度 {span} 分" if span > 0 else "")
        self.tile_gap.set_value(str(nxt - score))
        self.tile_gap.set_footer(f"当前 {score} 分")

        rule = result.get("current_rule", {})
        win_delta = rule.get("win_delta_actual", 0)
        loss_delta = rule.get("loss_delta_actual", 0)
        self.win_loss_hint.setText(
            f"本局若胜 <b style='color:{C.WIN}'>{win_delta:+d}</b> → {rule.get('win_score', '-')}　"
            f"若负 <b style='color:{C.LOSE}'>{loss_delta:+d}</b> → {rule.get('loss_score', '-')}　"
            f"（{rule.get('mode_name', '')}·{'组排' if rule.get('grouped') else '单排'}）"
        )
        self.win_loss_hint.setTextFormat(Qt.RichText)

        targets = result.get("targets", {})
        t_next = targets.get("next") or {}
        t_final = targets.get("final") or {}

        self.tile_exp_next.set_value(_fmt_games(t_next.get("expected_games")))
        self.tile_exp_next.set_footer(f"目标 {t_next.get('target', '-')} 分")
        self.tile_exp_final.set_value(_fmt_games(t_final.get("expected_games")))
        self.tile_exp_final.set_footer(f"目标 {t_final.get('target', '-')} 分")

        quantiles = t_final.get("quantile_games", {})
        for key in QUANTILES:
            self.quantile_tiles[key].set_value(_fmt_quantile(quantiles.get(key)))
        self.quantile_target_hint.setText(
            f"目标：最终节点 {t_final.get('target', cps[-1])} 分"
        )

        n = str(int(self.n_spin.value()))
        p_next = float(t_next.get("probability_within", {}).get(n, 0.0))
        p_final = float(t_final.get("probability_within", {}).get(n, 0.0))
        self.tile_n_next.set_value(f"{p_next * 100:.1f}")
        self.tile_n_next.set_footer(f"目标 {t_next.get('target', '-')} 分")
        self.tile_n_final.set_value(f"{p_final * 100:.1f}")
        self.tile_n_final.set_footer(f"目标 {t_final.get('target', '-')} 分")
        self.n_result_caption.setText(f"打 {n} 局后的完成概率")
        self.n_bar.setValue(int(round(p_final * 1000)))
