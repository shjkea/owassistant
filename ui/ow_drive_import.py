# -*- coding: utf-8 -*-
"""竞逐导入交互：赛季 + 起点比赛 + 终点比赛 → 区间内标准竞技比赛。

约束（与需求一致）：
- 竞逐只能属于一个赛季；起点和终点必须同赛季；
- 起止比赛都包含在内；
- 区间里的快速 / 角斗等非标准竞技比赛跳过，但不截断区间；
- 切换赛季会清除未完成的区间选择；
- 用 match_id 去重，绝不默认把整个赛季当成一次竞逐。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import owstats_data as D
from .theme import C
from .widgets import make_button


class DriveRangeBar(QWidget):
    """竞逐区间操作按钮组（不再是一条常驻状态 Bar）。

    以前这里是一整行「当前竞逐赛季：— / 起点：未选择 / 终点：未选择」，
    常年横在比赛列表顶部白占一行高度。现在只保留两个动作按钮：

        [导入竞逐计算器] [清除区间]

    赛季 / 起点 / 终点等竞逐区间信息改为按钮 tooltip，以及 ``status_text()``，
    真正的竞逐信息展示交给竞逐预测 / 竞逐历史页面。

    「导入竞逐计算器」按钮名称保留：它表达的是把当前比赛导入竞逐计算功能，
    与应用改名无关。
    """

    importRequested = Signal()
    clearRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("DriveActions")
        lay = QHBoxLayout(self)
        # 无额外 margin / 无 spacer：删掉状态条后不留空布局与空高度。
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._status_text = "右键任意竞技比赛可设为竞逐起点 / 终点"

        self.import_btn = make_button("导入竞逐计算器", "primary")
        self.import_btn.setEnabled(False)
        self.import_btn.setToolTip(self._status_text)
        self.import_btn.clicked.connect(self.importRequested.emit)
        lay.addWidget(self.import_btn, 0)

        self.clear_btn = make_button("清除区间", "ghost")
        self.clear_btn.setEnabled(False)
        self.clear_btn.clicked.connect(self.clearRequested.emit)
        lay.addWidget(self.clear_btn, 0)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def status_text(self) -> str:
        """当前竞逐区间摘要（赛季 / 起点 / 终点 / 场次）。只用于 tooltip 与诊断。"""
        return self._status_text

    def update_state(
        self,
        season: Optional[int],
        start: Optional[Dict[str, Any]],
        end: Optional[Dict[str, Any]],
        summary: Optional[Dict[str, Any]],
    ) -> None:
        def describe(view: Optional[Dict[str, Any]]) -> str:
            if not view:
                return "未选择"
            return f"{D.format_ts(view.get('begin_ts'))} {view.get('map_name') or ''}".strip()

        parts = [
            f"竞逐赛季：{D.season_text(season)}" if season else "竞逐赛季：—",
            f"起点：{describe(start)}",
            f"终点：{describe(end)}",
        ]
        if summary and summary.get("ok"):
            count = len(summary.get("matches") or [])
            parts.append(f"区间竞技 {count} 场（{summary.get('wins', 0)} 胜 / {summary.get('losses', 0)} 负）")
            skipped = int(summary.get("skipped_count") or 0)
            if skipped:
                parts.append(f"跳过非竞技 {skipped} 场")
        else:
            parts.append("右键任意竞技比赛可设为竞逐起点 / 终点")
        self._status_text = "\n".join(parts)
        self.import_btn.setToolTip(self._status_text)
        self.clear_btn.setToolTip(self._status_text)
        self.import_btn.setEnabled(bool(summary and summary.get("ok") and summary.get("matches")))
        self.clear_btn.setEnabled(bool(start or end))


class DriveImportDialog(QDialog):
    """导入前确认：赛季、起止比赛、区间统计、重复检测。"""

    def __init__(
        self,
        summary: Dict[str, Any],
        duplicated: int,
        added: int,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("确认导入竞逐计算器")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)

        season = QLabel(f"竞逐赛季：{D.season_text(summary.get('season'))}")
        season.setStyleSheet(f"color: {C.ACCENT}; font-size: 14px; font-weight: 600;")
        lay.addWidget(season)

        def block(title: str, view: Optional[Dict[str, Any]]) -> QWidget:
            box = QVBoxLayout()
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(2)
            head = QLabel(title)
            head.setProperty("role", "fieldLabel")
            box.addWidget(head)
            info = dict(view or {})
            text = f"{D.format_ts(info.get('begin_ts'))}\n{info.get('map_name') or '—'}"
            body = QLabel(text)
            body.setStyleSheet(f"color: {C.TEXT}; font-size: 13px;")
            box.addWidget(body)
            wrap = QWidget()
            wrap.setLayout(box)
            return wrap

        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(block("起点", summary.get("start")), 1)
        row.addWidget(block("终点", summary.get("end")), 1)
        lay.addLayout(row)

        matches = list(summary.get("matches") or [])
        stat = QLabel(
            f"共 {len(matches)} 场竞技\n"
            f"{summary.get('wins', 0)} 胜 / {summary.get('losses', 0)} 负"
            + (f"（{summary.get('draws', 0)} 平局将跳过）" if summary.get("draws") else "")
        )
        stat.setStyleSheet(f"color: {C.TEXT}; font-size: 13px;")
        lay.addWidget(stat)

        skipped = int(summary.get("skipped_count") or 0)
        if skipped:
            note = QLabel(f"区间内 {skipped} 场快速 / 角斗等非竞技比赛不会导入，但不会截断区间。")
            note.setProperty("role", "hint")
            note.setWordWrap(True)
            lay.addWidget(note)

        dedupe = QLabel(
            f"重复检测：已存在 {duplicated} 场，新增 {added} 场。"
            if duplicated else f"重复检测：全部为新记录，新增 {added} 场。"
        )
        dedupe.setProperty("role", "hint")
        dedupe.setWordWrap(True)
        lay.addWidget(dedupe)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self)
        buttons.button(QDialogButtonBox.Ok).setText("确认导入")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


class DriveSelection:
    """竞逐起止选择状态。赛季变化时强制清空，避免跨赛季残留。"""

    def __init__(self) -> None:
        self.season: Optional[int] = None
        self.start_id: str = ""
        self.end_id: str = ""

    def clear(self) -> None:
        self.start_id = ""
        self.end_id = ""

    def set_season(self, season: Optional[int]) -> None:
        if season != self.season:
            self.season = season
            self.clear()

    def set_start(self, season: Optional[int], match_id: str) -> None:
        self.set_season(season)
        self.start_id = str(match_id or "")

    def set_end(self, season: Optional[int], match_id: str) -> None:
        self.set_season(season)
        self.end_id = str(match_id or "")

    def summary(self, views: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if self.season is None or not self.start_id or not self.end_id:
            return None
        return D.drive_range_summary(views, int(self.season), self.start_id, self.end_id)

    def mark_for(self, views: Sequence[Dict[str, Any]], match_id: str) -> str:
        if not match_id:
            return ""
        if match_id == self.start_id:
            return "start"
        if match_id == self.end_id:
            return "end"
        summary = self.summary(views)
        if not summary or not summary.get("ok"):
            return ""
        window_ids = {str(v.get("match_id") or "") for v in summary.get("matches") or []}
        return "range" if match_id in window_ids else ""


__all__ = ["DriveImportDialog", "DriveRangeBar", "DriveSelection"]
