# -*- coding: utf-8 -*-
"""竞逐历史页。

记录的是竞逐预测 / 竞逐计算相关的逐局历史，不是 OW 对局历史。

核心交互：
- 粘贴 "+-++-+" 序列 + 组排/决斗局号区间，一键导入
- 表格内多选批量改单排/组排、普通/决斗、切换胜负、删除
- 顶部常驻汇总指标

所有分数与 delta 均由 drive_core.replay_history 计算，本文件不做任何计分。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from drive_core import build_history, compress_ranges, records_to_dicts

from ..state import AppState
from ..theme import C, MONO_FAMILY
from ..widgets import (
    Banner,
    Card,
    FlowFrame,
    SegmentedControl,
    StatTile,
    field_label,
    make_button,
)

COLUMNS = ("局号", "胜负", "队列", "模式", "加减分", "赛后总分")


class HistoryTableModel(QAbstractTableModel):
    """只读展示模型，数据来自 replay_history 的 rows。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: List[Dict[str, Any]] = []

    def set_rows(self, rows: List[Dict[str, Any]]) -> None:
        """行数不变时只刷新内容，避免 reset 清掉用户的多选。"""
        new_rows = list(rows)
        if len(new_rows) == len(self._rows) and self._rows:
            self._rows = new_rows
            top = self.index(0, 0)
            bottom = self.index(len(new_rows) - 1, len(COLUMNS) - 1)
            self.dataChanged.emit(top, bottom)
            return
        self.beginResetModel()
        self._rows = new_rows
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # noqa: N802
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()
        won = row["result"] == "W"

        if role == Qt.DisplayRole:
            if col == 0:
                return str(row["game"])
            if col == 1:
                return row["result_text"]
            if col == 2:
                return row["queue_text"]
            if col == 3:
                return "决斗" if row["mode"] == "duel" else "普通"
            if col == 4:
                return f"{row['delta']:+d}"
            if col == 5:
                return str(row["score_after"])

        if role == Qt.TextAlignmentRole:
            if col in (0, 4, 5):
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignCenter)

        if role == Qt.ForegroundRole:
            if col == 1:
                return QColor(C.WIN if won else C.LOSE)
            if col == 2:
                return QColor(C.GROUP if row["grouped"] else C.TEXT_SUB)
            if col == 3:
                return QColor(C.DUEL if row["mode"] == "duel" else C.TEXT_SUB)
            if col == 4:
                delta = row["delta"]
                if delta > 0:
                    return QColor(C.WIN)
                if delta < 0:
                    return QColor(C.LOSE)
                return QColor(C.TEXT_DIM)
            if col == 5:
                return QColor(C.TEXT)
            if col == 0:
                return QColor(C.TEXT_DIM)

        if role == Qt.FontRole:
            font = QFont()
            if col in (0, 4, 5):
                font.setFamilies(["Cascadia Mono", "Consolas", "Microsoft YaHei UI"])
            if col in (1, 5):
                font.setBold(True)
            return font

        if role == Qt.ToolTipRole:
            return (
                f"第 {row['game']} 局　{row['result_text']}　"
                f"{row['queue_text']}　{row['mode_name']}\n"
                f"{row['score_before']} → {row['score_after']}（{row['delta']:+d}）"
            )
        return None


class HistoryPage(QWidget):
    scoreSynced = Signal(int)

    def __init__(self, state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._state = state
        self._model = HistoryTableModel(self)
        self._build_ui()
        self._install_shortcuts()
        state.historyChanged.connect(self._render)
        state.rulesChanged.connect(self._render)
        self._render()

    # ---------------- 构建 ----------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("竞逐历史")
        title.setProperty("role", "title")
        title_box.addWidget(title)
        sub = QLabel("粘贴战绩串一键导入，或直接点按钮逐局追加；分数由核心逐局回放得出。")
        sub.setProperty("role", "subtitle")
        title_box.addWidget(sub)
        head.addLayout(title_box, 1)
        root.addLayout(head)

        root.addWidget(self._build_summary_bar())

        self.banner = Banner()
        root.addWidget(self.banner)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)

        # 左栏用滚动区包住：窗口变矮时内容整体滚动，而不是被挤压重叠。
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setWidget(self._build_import_panel())
        left_scroll.setMinimumWidth(316)
        left_scroll.setMaximumWidth(420)

        splitter.addWidget(left_scroll)
        splitter.addWidget(self._build_table_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 780])
        root.addWidget(splitter, 1)

    def _build_summary_bar(self) -> QWidget:
        # 用 FlowFrame：窄窗口时指标块折行，而不是互相压叠。
        frame = FlowFrame(
            margins=(14, 12, 14, 12), h_spacing=10, v_spacing=10, object_name="Card"
        )

        self.tile_score = StatTile("当前分数", "0", accent=True)
        self.tile_games = StatTile("总场次", "0", "场")
        self.tile_wins = StatTile("胜", "0", "场")
        self.tile_losses = StatTile("负", "0", "场")
        self.tile_rate = StatTile("胜率", "0.0", "%")
        self.tile_solo = StatTile("单排", "0", "场")
        self.tile_group = StatTile("组排", "0", "场")

        tiles = (
            self.tile_score,
            self.tile_games,
            self.tile_wins,
            self.tile_losses,
            self.tile_rate,
            self.tile_solo,
            self.tile_group,
        )
        for tile in tiles:
            tile.setMinimumWidth(132)
            frame.addWidget(tile)
        return frame

    def _build_import_panel(self) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("Root")
        wrap.setMinimumWidth(300)
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 2, 4)
        lay.setSpacing(12)

        card = Card("一键导入", "+ 为胜，- 为负；空格、逗号等分隔符会被忽略")
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        lay.addWidget(card)

        card.body.addWidget(field_label("战绩串"))
        self.seq_edit = QPlainTextEdit()
        self.seq_edit.setObjectName("SequenceEdit")
        self.seq_edit.setPlaceholderText("+-++-++++-++++-++")
        self.seq_edit.setMinimumHeight(74)
        self.seq_edit.setMaximumHeight(104)
        self.seq_edit.textChanged.connect(self._update_seq_hint)
        card.body.addWidget(self.seq_edit)

        self.seq_hint = QLabel("尚未输入")
        self.seq_hint.setProperty("role", "hint")
        card.body.addWidget(self.seq_hint)

        card.body.addWidget(field_label("组排局号（示例 3-9,12,15-17）"))
        self.group_edit = QLineEdit()
        self.group_edit.setObjectName("RangeEdit")
        self.group_edit.setPlaceholderText("3-9,12,15-17")
        card.body.addWidget(self.group_edit)

        card.body.addWidget(field_label("决斗局号（留空表示全部普通）"))
        self.duel_edit = QLineEdit()
        self.duel_edit.setObjectName("RangeEdit")
        self.duel_edit.setPlaceholderText("例如 10-14")
        card.body.addWidget(self.duel_edit)

        start_row = QHBoxLayout()
        start_row.setContentsMargins(0, 0, 0, 0)
        start_row.addWidget(field_label("起始分数"), 1)
        self.start_spin = QSpinBox()
        self.start_spin.setRange(0, 4000)
        self.start_spin.setSingleStep(10)
        self.start_spin.setFixedWidth(96)
        self.start_spin.valueChanged.connect(self._state.set_start_score)
        start_row.addWidget(self.start_spin, 0)
        card.body.addLayout(start_row)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)
        self.import_btn = make_button("一键导入", "primary", "覆盖当前列表（Ctrl+Enter）")
        self.import_btn.clicked.connect(self._do_import)
        btn_row.addWidget(self.import_btn, 2)
        self.append_btn = make_button("追加导入", "ghost", "把解析结果接到现有列表末尾")
        self.append_btn.clicked.connect(lambda: self._do_import(append=True))
        btn_row.addWidget(self.append_btn, 1)
        card.body.addLayout(btn_row)

        fill_row = QHBoxLayout()
        fill_row.setContentsMargins(0, 0, 0, 0)
        fill_row.setSpacing(8)
        self.pull_btn = make_button("回填当前列表", "ghost", "把表格内容写回上方输入框")
        self.pull_btn.clicked.connect(self._pull_from_records)
        fill_row.addWidget(self.pull_btn)
        self.clear_btn = make_button("清空全部", "ghost")
        self.clear_btn.clicked.connect(self._clear_all)
        fill_row.addWidget(self.clear_btn)
        card.body.addLayout(fill_row)

        lay.addStretch(1)
        wrap.setMinimumHeight(wrap.sizeHint().height())
        return wrap

    def _build_quick_bar(self) -> QWidget:
        """表格上方的“快速追加”工具条：高频操作一键完成，窄窗口自动折行。"""
        bar = FlowFrame(margins=(12, 9, 12, 9), h_spacing=8, v_spacing=8)

        cap = QLabel("快速追加")
        cap.setProperty("role", "section")
        cap.setMinimumHeight(34)
        bar.addWidget(cap)

        self.quick_queue = SegmentedControl(["单排", "组排"], 0)
        self.quick_queue.setFixedWidth(136)
        self.quick_queue.setToolTip("新增单局使用的队列")
        bar.addWidget(self.quick_queue)

        self.quick_mode = SegmentedControl(["普通", "决斗"], 0)
        self.quick_mode.setFixedWidth(136)
        self.quick_mode.setToolTip("新增单局使用的模式")
        bar.addWidget(self.quick_mode)

        self.add_win_btn = make_button("+ 添加胜", "win", "快捷键 W")
        self.add_win_btn.clicked.connect(lambda: self._append("W"))
        self.add_win_btn.setFixedWidth(104)
        bar.addWidget(self.add_win_btn)

        self.add_lose_btn = make_button("- 添加负", "lose", "快捷键 L")
        self.add_lose_btn.clicked.connect(lambda: self._append("L"))
        self.add_lose_btn.setFixedWidth(104)
        bar.addWidget(self.add_lose_btn)

        self.undo_btn = make_button("删除最后一局", "ghost", "快捷键 Backspace")
        self.undo_btn.clicked.connect(self._remove_last)
        self.undo_btn.setFixedWidth(126)
        bar.addWidget(self.undo_btn)
        return bar

    def _build_table_panel(self) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("Root")
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        lay.addWidget(self._build_quick_bar())

        bar = FlowFrame(margins=(12, 9, 12, 9), h_spacing=8, v_spacing=8)

        self.sel_label = QLabel("未选中")
        self.sel_label.setProperty("role", "fieldLabel")
        self.sel_label.setFixedWidth(104)
        self.sel_label.setMinimumHeight(34)
        bar.addWidget(self.sel_label)

        self.btn_set_solo = make_button("改为单排", tooltip="选中局设为单排")
        self.btn_set_group = make_button("改为组排", tooltip="选中局设为组排")
        self.btn_set_normal = make_button("改为普通", tooltip="选中局设为普通模式")
        self.btn_set_duel = make_button("改为决斗", tooltip="选中局设为决斗模式")
        self.btn_toggle = make_button("切换胜负", tooltip="快捷键 空格")
        self.btn_del_sel = make_button("删除选中", "lose", "快捷键 Delete")

        self.btn_set_solo.clicked.connect(lambda: self._update_selected(grouped=False))
        self.btn_set_group.clicked.connect(lambda: self._update_selected(grouped=True))
        self.btn_set_normal.clicked.connect(lambda: self._update_selected(mode="normal"))
        self.btn_set_duel.clicked.connect(lambda: self._update_selected(mode="duel"))
        self.btn_toggle.clicked.connect(self._toggle_selected)
        self.btn_del_sel.clicked.connect(self._delete_selected)

        for btn in (
            self.btn_set_solo,
            self.btn_set_group,
            self.btn_set_normal,
            self.btn_set_duel,
            self.btn_toggle,
            self.btn_del_sel,
        ):
            btn.setFixedWidth(96)
            bar.addWidget(btn)

        self.select_all_btn = make_button("全选", "ghost")
        self.select_all_btn.clicked.connect(lambda: self.table.selectAll())
        self.select_all_btn.setFixedWidth(72)
        bar.addWidget(self.select_all_btn)
        self.select_none_btn = make_button("取消选择", "ghost")
        self.select_none_btn.clicked.connect(lambda: self.table.clearSelection())
        self.select_none_btn.setFixedWidth(96)
        bar.addWidget(self.select_none_btn)

        lay.addWidget(bar)

        self.seq_preview = QLabel("")
        self.seq_preview.setStyleSheet(
            f"font-family: {MONO_FAMILY}; color: {C.TEXT_SUB}; font-size: 12px;"
        )
        self.seq_preview.setWordWrap(True)
        self.seq_preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.seq_preview)

        self.table = QTableView()
        self.table.setModel(self._model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setCornerButtonEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_menu)
        self.table.doubleClicked.connect(self._on_double_click)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.resizeSection(0, 64)
        header.setHighlightSections(False)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.selectionModel().selectionChanged.connect(self._update_selection_state)
        lay.addWidget(self.table, 1)

        self.empty_hint = QLabel("列表为空。左侧粘贴战绩串后点“一键导入”，或用“添加胜 / 添加负”逐局记录。")
        self.empty_hint.setProperty("role", "hint")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty_hint)
        return wrap

    def _install_shortcuts(self) -> None:
        def bind(seq: str, slot) -> None:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)

        bind("Ctrl+Return", self._do_import)
        bind("Ctrl+Enter", self._do_import)
        bind("Delete", self._delete_selected)
        bind("Space", self._toggle_selected)
        bind("Backspace", self._remove_last_if_table)

    # ---------------- 操作 ----------------
    def _selected_rows(self) -> List[int]:
        return sorted({idx.row() for idx in self.table.selectionModel().selectedRows()})

    def _quick_kwargs(self) -> Dict[str, Any]:
        return {
            "grouped": self.quick_queue.current_index() == 1,
            "mode": "duel" if self.quick_mode.current_index() == 1 else "normal",
        }

    def _append(self, result: str) -> None:
        self._state.append_record(result, **self._quick_kwargs())
        self.table.scrollToBottom()
        label = "胜" if result == "W" else "负"
        self.banner.show_message(
            f"已添加第 {len(self._state.records)} 局（{label}）", "ok"
        )

    def _remove_last(self) -> None:
        if self._state.remove_last():
            self.banner.show_message("已删除最后一局", "info")
        else:
            self.banner.show_message("列表已空，没有可删除的局", "warn")

    def _remove_last_if_table(self) -> None:
        focus = self.focusWidget()
        if isinstance(focus, (QPlainTextEdit, QLineEdit, QSpinBox)):
            return
        self._remove_last()

    def _delete_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            self.banner.show_message("请先在表格中选中要删除的局", "warn")
            return
        removed = self._state.remove_indices(rows)
        self.banner.show_message(f"已删除 {removed} 局", "info")

    def _update_selected(self, **changes: Any) -> None:
        rows = self._selected_rows()
        if not rows:
            self.banner.show_message("请先选中要修改的局（可按住 Ctrl / Shift 多选）", "warn")
            return
        touched = self._state.update_indices(rows, **changes)
        desc = []
        if "grouped" in changes:
            desc.append("组排" if changes["grouped"] else "单排")
        if "mode" in changes:
            desc.append("决斗" if changes["mode"] == "duel" else "普通")
        self.banner.show_message(f"已将 {touched} 局改为 {' · '.join(desc)}", "ok")

    def _toggle_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            self.banner.show_message("请先选中要切换胜负的局", "warn")
            return
        touched = self._state.toggle_results(rows)
        self.banner.show_message(f"已切换 {touched} 局胜负", "ok")

    def _on_double_click(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        row = index.row()
        col = index.column()
        if col == 1:
            self._state.toggle_results([row])
        elif col == 2:
            cur = bool(self._state.records[row].get("grouped"))
            self._state.update_indices([row], grouped=not cur)
        elif col == 3:
            cur = self._state.records[row].get("mode", "normal")
            self._state.update_indices([row], mode="normal" if cur == "duel" else "duel")
        else:
            self._state.toggle_results([row])

    def _show_menu(self, pos: QPoint) -> None:
        if not self._selected_rows():
            index = self.table.indexAt(pos)
            if index.isValid():
                self.table.selectRow(index.row())
        if not self._selected_rows():
            return
        menu = QMenu(self)
        menu.addAction("切换胜负", self._toggle_selected)
        menu.addSeparator()
        menu.addAction("改为单排", lambda: self._update_selected(grouped=False))
        menu.addAction("改为组排", lambda: self._update_selected(grouped=True))
        menu.addSeparator()
        menu.addAction("改为普通", lambda: self._update_selected(mode="normal"))
        menu.addAction("改为决斗", lambda: self._update_selected(mode="duel"))
        menu.addSeparator()
        menu.addAction("删除选中", self._delete_selected)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _do_import(self, append: bool = False) -> None:
        text = self.seq_edit.toPlainText().strip()
        if not text:
            self.banner.show_message("请先粘贴战绩串，例如 +-++-++++-++++-++", "error")
            return
        try:
            records = build_history(
                text,
                group_ranges=self.group_edit.text(),
                duel_ranges=self.duel_edit.text(),
            )
        except ValueError as exc:
            self.banner.show_message(str(exc), "error")
            return

        new_rows = records_to_dicts(records)
        if append:
            self._state.set_records(self._state.records + new_rows)
            self.banner.show_message(f"已追加 {len(new_rows)} 局", "ok")
        else:
            self._state.set_records(new_rows)
            self.banner.show_message(
                f"已导入 {len(new_rows)} 局，当前分数 {self._state.current_score}", "ok"
            )
        self.table.scrollToBottom()

    def _pull_from_records(self) -> None:
        summary = self._state.history.get("summary", {})
        self.seq_edit.setPlainText(summary.get("sequence", ""))
        self.group_edit.setText(summary.get("group_ranges", ""))
        self.duel_edit.setText(summary.get("duel_ranges", ""))
        self.banner.show_message("已把当前列表回填到输入框", "info")

    def _clear_all(self) -> None:
        if not self._state.records:
            self.banner.show_message("列表已经是空的", "warn")
            return
        count = len(self._state.records)
        self._state.clear_records()
        self.banner.show_message(f"已清空 {count} 局记录", "info")

    # ---------------- 渲染 ----------------
    def _update_seq_hint(self) -> None:
        text = self.seq_edit.toPlainText()
        wins = sum(1 for ch in text if ch in "+wW胜赢1")
        losses = sum(1 for ch in text if ch in "-lL负输0")
        total = wins + losses
        if total == 0:
            self.seq_hint.setText("尚未输入")
        else:
            rate = wins / total * 100
            self.seq_hint.setText(f"识别 {total} 局：{wins} 胜 {losses} 负，胜率 {rate:.1f}%")

    def _update_selection_state(self) -> None:
        rows = self._selected_rows()
        has = bool(rows)
        for btn in (
            self.btn_set_solo,
            self.btn_set_group,
            self.btn_set_normal,
            self.btn_set_duel,
            self.btn_toggle,
            self.btn_del_sel,
        ):
            btn.setEnabled(has)
        if not has:
            self.sel_label.setText("未选中")
            self.sel_label.setToolTip("")
        else:
            ranges = compress_ranges(r + 1 for r in rows)
            self.sel_label.setText(f"选中 {len(rows)} 局")
            self.sel_label.setToolTip(f"第 {ranges} 局")

    def _render(self) -> None:
        history = self._state.history
        summary = history.get("summary", {})
        rows = history.get("rows", [])
        self._model.set_rows(rows)

        score = int(history.get("current_score", 0))
        final = int(history.get("final_checkpoint", 4000))
        self.tile_score.set_value(str(score))
        self.tile_score.set_footer(
            "已达最终节点" if history.get("completed") else f"距 {final} 还差 {final - score}"
        )
        self.tile_games.set_value(str(summary.get("games", 0)))
        self.tile_wins.set_value(str(summary.get("wins", 0)), C.WIN)
        self.tile_losses.set_value(str(summary.get("losses", 0)), C.LOSE)
        self.tile_rate.set_value(f"{summary.get('win_rate_percent', 0.0):.1f}")
        self.tile_solo.set_value(str(summary.get("solo_games", 0)))
        self.tile_group.set_value(str(summary.get("group_games", 0)), C.GROUP)

        group_ranges = summary.get("group_ranges", "")
        duel_ranges = summary.get("duel_ranges", "")
        self.tile_group.set_footer(f"局号 {group_ranges}" if group_ranges else "")
        parts = [f"序列 {summary.get('sequence', '') or '（空）'}"]
        if group_ranges:
            parts.append(f"组排 {group_ranges}")
        if duel_ranges:
            parts.append(f"决斗 {duel_ranges}")
        self.seq_preview.setText("　".join(parts))

        if self.start_spin.value() != self._state.start_score:
            self.start_spin.blockSignals(True)
            self.start_spin.setValue(self._state.start_score)
            self.start_spin.blockSignals(False)
        self.start_spin.setMaximum(final)

        empty = not rows
        self.empty_hint.setVisible(empty)
        self.undo_btn.setEnabled(not empty)
        self.clear_btn.setEnabled(not empty)
        self.pull_btn.setEnabled(not empty)
        self._update_selection_state()
