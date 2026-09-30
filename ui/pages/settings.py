# -*- coding: utf-8 -*-
"""规则设置页。

校验与保存全部交给 drive_core.rules_from_dict / save_rules，
本文件只负责把 UI 上的数值组装成 config dict。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from owstats import OWStatsService

from drive_core import (
    DriveRules,
    default_rules,
    rules_from_dict,
    rules_to_dict,
    save_rules,
)

from .. import ai_review_bridge as AIB
from .. import cache_manager as CM
from ..async_worker import submit
from ..dashen_auth import DashenAuthDialog
from ..state import AppState
from ..theme import C, MONO_FAMILY
from ..widgets import Banner, Card, FlowFrame, StatusLine, field_label, hline, make_button

MODE_ORDER = ("normal", "duel")
MODE_TITLES = {"normal": "普通模式", "duel": "决斗模式"}


class _ModeEditor(QWidget):
    """单个模式的规则编辑块：胜场加分 + 组排奖励 + 各阶段扣分。"""

    def __init__(self, key: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.key = key
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        top = QGridLayout()
        top.setHorizontalSpacing(10)
        top.setVerticalSpacing(6)
        lay.addLayout(top)

        top.addWidget(field_label("模式名称"), 0, 0)
        self.name_edit = QLineEdit()
        top.addWidget(self.name_edit, 1, 0)

        top.addWidget(field_label("胜场加分"), 0, 1)
        self.win_spin = QSpinBox()
        self.win_spin.setRange(1, 100000)
        self.win_spin.setSingleStep(5)
        top.addWidget(self.win_spin, 1, 1)

        top.addWidget(field_label("组排胜利额外奖励"), 0, 2)
        self.bonus_spin = QSpinBox()
        self.bonus_spin.setRange(0, 100000)
        self.bonus_spin.setSingleStep(5)
        top.addWidget(self.bonus_spin, 1, 2)
        for col in range(3):
            top.setColumnStretch(col, 1)

        lay.addWidget(hline())

        cap = QLabel("各阶段败场扣分")
        cap.setProperty("role", "fieldLabel")
        lay.addWidget(cap)

        self._penalty_grid = QGridLayout()
        self._penalty_grid.setHorizontalSpacing(10)
        self._penalty_grid.setVerticalSpacing(6)
        lay.addLayout(self._penalty_grid)
        self._penalty_spins: List[QSpinBox] = []
        self._penalty_labels: List[QLabel] = []

    def rebuild_penalties(self, checkpoints: List[int]) -> None:
        old = [s.value() for s in self._penalty_spins]
        while self._penalty_grid.count():
            item = self._penalty_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._penalty_spins = []
        self._penalty_labels = []

        cols = 3
        for i, _cp in enumerate(checkpoints):
            low = 0 if i == 0 else checkpoints[i - 1]
            high = checkpoints[i]
            lbl = QLabel(f"{low} – {high}")
            lbl.setProperty("role", "fieldLabel")
            spin = QSpinBox()
            spin.setRange(0, 100000)
            spin.setValue(old[i] if i < len(old) else 0)
            row = (i // cols) * 2
            col = i % cols
            self._penalty_grid.addWidget(lbl, row, col)
            self._penalty_grid.addWidget(spin, row + 1, col)
            self._penalty_spins.append(spin)
            self._penalty_labels.append(lbl)
        for col in range(cols):
            self._penalty_grid.setColumnStretch(col, 1)

    def load(self, data: Dict[str, Any], checkpoints: List[int]) -> None:
        self.name_edit.setText(str(data.get("name", self.key)))
        self.win_spin.setValue(int(data.get("win_points", 0)))
        self.bonus_spin.setValue(int(data.get("group_bonus", 0)))
        self.rebuild_penalties(checkpoints)
        penalties = list(data.get("loss_penalties", []))
        for i, spin in enumerate(self._penalty_spins):
            spin.setValue(int(penalties[i]) if i < len(penalties) else 0)

    def dump(self) -> Dict[str, Any]:
        return {
            "name": self.name_edit.text().strip() or self.key,
            "win_points": int(self.win_spin.value()),
            "group_bonus": int(self.bonus_spin.value()),
            "loss_penalties": [int(s.value()) for s in self._penalty_spins],
        }


class SettingsPage(QWidget):
    authChanged = Signal()
    # 缓存被清理后通知其它页面同步内存缓存。参数为 detail / avatar / all。
    cacheCleared = Signal(str)

    def __init__(self, state: AppState, stats_service: Optional[OWStatsService] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._state = state
        self._stats_service = stats_service or OWStatsService()
        self._settings = QSettings("ow_drive_split", "drive_calculator")
        self._auth_dialog: Optional[DashenAuthDialog] = None
        self._editors: Dict[str, _ModeEditor] = {}
        self._cache_busy = False
        self._build_ui()
        self.load_from_rules(state.rules)
        self.refresh_auth_status()
        # 缓存大小统计走后台线程，避免首次进入设置页时卡住 UI。
        QTimer.singleShot(0, self.refresh_cache_usage)

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

        title = QLabel("规则设置")
        title.setProperty("role", "title")
        lay.addWidget(title)
        sub = QLabel("修改后点“应用并保存”写入 config.json，计分与预测会立即使用新规则。")
        sub.setProperty("role", "subtitle")
        lay.addWidget(sub)

        self.banner = Banner()
        lay.addWidget(self.banner)

        # 网易大神授权
        lay.addWidget(self._build_auth_card())

        # AI 锐评（DeepSeek API Key）
        lay.addWidget(self._build_ai_card())

        # 缓存管理
        lay.addWidget(self._build_cache_card())

        # 节点
        cp_card = Card("竞逐节点", "用逗号分隔的递增正整数，最后一个即最终目标分")
        lay.addWidget(cp_card)
        self.cp_edit = QLineEdit()
        self.cp_edit.setObjectName("RangeEdit")
        self.cp_edit.setPlaceholderText("500, 1000, 1600, 2200, 3000, 4000")
        self.cp_edit.editingFinished.connect(self._sync_penalty_labels)
        cp_card.body.addWidget(self.cp_edit)

        cp_row = QHBoxLayout()
        cp_row.setContentsMargins(0, 0, 0, 0)
        cp_row.setSpacing(8)
        refresh_btn = make_button("刷新阶段表", "ghost", "按当前节点重新生成下面的扣分格子")
        refresh_btn.clicked.connect(self._sync_penalty_labels)
        cp_row.addWidget(refresh_btn)
        cp_row.addStretch(1)
        self.cp_hint = QLabel("")
        self.cp_hint.setProperty("role", "hint")
        cp_row.addWidget(self.cp_hint)
        cp_card.body.addLayout(cp_row)

        # 模式
        for key in MODE_ORDER:
            card = Card(MODE_TITLES[key], "败场扣分按分数所处阶段生效，节点保底不会掉出下限")
            editor = _ModeEditor(key)
            card.body.addWidget(editor)
            self._editors[key] = editor
            lay.addWidget(card)

        # 操作
        act_card = Card("配置管理")
        lay.addWidget(act_card)
        btn_row = FlowFrame(margins=(0, 0, 0, 0), h_spacing=8, v_spacing=8, object_name="Plain")
        self.save_btn = make_button("应用并保存", "primary", "校验后写入 config.json")
        self.save_btn.clicked.connect(self._save)
        self.save_btn.setFixedWidth(116)
        btn_row.addWidget(self.save_btn)
        self.reset_btn = make_button("恢复默认")
        self.reset_btn.clicked.connect(self._restore_default)
        self.reset_btn.setFixedWidth(96)
        btn_row.addWidget(self.reset_btn)
        self.reload_btn = make_button("放弃修改", "ghost", "重新读取当前生效规则")
        self.reload_btn.clicked.connect(lambda: self._reload_current())
        self.reload_btn.setFixedWidth(96)
        btn_row.addWidget(self.reload_btn)
        self.import_btn = make_button("导入配置")
        self.import_btn.clicked.connect(self._import)
        self.import_btn.setFixedWidth(96)
        btn_row.addWidget(self.import_btn)
        self.export_btn = make_button("导出配置")
        self.export_btn.clicked.connect(self._export)
        self.export_btn.setFixedWidth(96)
        btn_row.addWidget(self.export_btn)
        act_card.body.addWidget(btn_row)

        self.preview = QLabel("")
        self.preview.setStyleSheet(
            f"font-family: {MONO_FAMILY}; color: {C.TEXT_DIM}; font-size: 12px;"
        )
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        act_card.body.addWidget(self.preview)

        lay.addStretch(1)


    def _build_auth_card(self) -> QWidget:
        card = Card("网易大神授权", "用于国服 BattleTag、段位与战绩查询；凭证仅保存在本机，不在界面中显示 token。")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        info = QVBoxLayout()
        info.setSpacing(3)
        self.dashen_status = QLabel("检查授权状态…")
        self.dashen_status.setProperty("role", "section")
        info.addWidget(self.dashen_status)
        self.dashen_account = QLabel("")
        self.dashen_account.setProperty("role", "hint")
        self.dashen_account.setWordWrap(True)
        info.addWidget(self.dashen_account)
        row.addLayout(info, 1)
        self.dashen_auth_btn = make_button("扫码授权", "primary")
        self.dashen_auth_btn.clicked.connect(self._open_auth_dialog)
        row.addWidget(self.dashen_auth_btn)
        self.dashen_clear_btn = make_button("清除授权", "ghost")
        self.dashen_clear_btn.clicked.connect(self._clear_auth)
        row.addWidget(self.dashen_clear_btn)
        card.body.addLayout(row)
        return card

    # ---------------- AI 锐评 ----------------
    def _build_ai_card(self) -> QWidget:
        card = Card(
            "AI 锐评",
            f"模型：{AIB.MODEL_DISPLAY_NAME}（DeepSeek 官方 API，由后端固定）。"
            "API Key 仅保存在本机用户配置目录，不写入项目目录或 config.json。",
        )
        card.body.addWidget(field_label("DeepSeek API Key"))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.ai_key_edit = QLineEdit()
        self.ai_key_edit.setEchoMode(QLineEdit.Password)
        self.ai_key_edit.setPlaceholderText("sk-…")
        self.ai_key_edit.setAccessibleName("DeepSeek API Key")
        # 不允许把 Key 通过拖拽 / 右键菜单复制出去；密码模式本身也禁止复制。
        self.ai_key_edit.setContextMenuPolicy(Qt.NoContextMenu)
        self.ai_key_edit.setDragEnabled(False)
        self.ai_key_edit.returnPressed.connect(self._save_ai_key)
        row.addWidget(self.ai_key_edit, 1)
        self.ai_save_btn = make_button("保存", "primary")
        self.ai_save_btn.clicked.connect(self._save_ai_key)
        row.addWidget(self.ai_save_btn)
        self.ai_test_btn = make_button("测试连接", "ghost")
        self.ai_test_btn.clicked.connect(self._test_ai_connection)
        row.addWidget(self.ai_test_btn)
        self.ai_clear_btn = make_button("清除", "ghost")
        self.ai_clear_btn.clicked.connect(self._clear_ai_key)
        row.addWidget(self.ai_clear_btn)
        card.body.addLayout(row)
        self.ai_status = StatusLine()
        card.body.addWidget(self.ai_status)
        self._ai_busy = False
        self._refresh_ai_status()
        return card

    def _refresh_ai_status(self, message: str = "", level: str = "info") -> None:
        if AIB.AR is None:
            for w in (self.ai_key_edit, self.ai_save_btn, self.ai_test_btn, self.ai_clear_btn):
                w.setEnabled(False)
            self.ai_status.show_message(AIB.IMPORT_ERROR or "AI 锐评后端不可用。", "error")
            return
        saved = AIB.key_store().has_key()
        self.ai_key_edit.setPlaceholderText("已保存（出于安全不回显）" if saved else "sk-…")
        self.ai_clear_btn.setEnabled(saved and not self._ai_busy)
        self.ai_save_btn.setEnabled(not self._ai_busy)
        self.ai_test_btn.setEnabled(not self._ai_busy)
        if message:
            self.ai_status.show_message(message, level)
        elif saved:
            self.ai_status.show_message("已配置 DeepSeek API Key。", "ok")
        else:
            self.ai_status.show_message("未配置 DeepSeek API Key，AI 锐评不可用。", "info")

    def _save_ai_key(self) -> bool:
        text = self.ai_key_edit.text().strip()
        if not text:
            self._refresh_ai_status("请输入 DeepSeek API Key。", "error")
            return False
        try:
            AIB.save_api_key(text)
        except (OSError, ValueError) as exc:
            self._refresh_ai_status(f"保存失败：{AIB.scrub(exc, [text])}", "error")
            return False
        self.ai_key_edit.clear()
        self._refresh_ai_status("已保存 DeepSeek API Key。", "ok")
        return True

    def _clear_ai_key(self) -> None:
        AIB.clear_api_key()
        self.ai_key_edit.clear()
        self._refresh_ai_status("已清除 DeepSeek API Key。", "ok")

    def _test_ai_connection(self) -> None:
        if self._ai_busy or AIB.AR is None:
            return
        # 输入框里有新 Key：先保存再测试；否则测试已保存的 Key。
        if self.ai_key_edit.text().strip() and not self._save_ai_key():
            return
        service = AIB.ai_service()
        if service is None or not AIB.key_store().has_key():
            self._refresh_ai_status("未配置 DeepSeek API Key。", "error")
            return
        self._ai_busy = True
        self._refresh_ai_status("正在连接 DeepSeek…", "info")

        def task() -> Any:
            # 异常在工作线程里转换成脱敏后的中文提示，避免原始异常文本外泄。
            try:
                return {"ok": True, "model": service.test_connection()}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "message": AIB.friendly_error(exc)}

        def done(result: Any) -> None:
            data = dict(result or {})
            if data.get("ok"):
                self._refresh_ai_status(f"连接成功，{AIB.MODEL_DISPLAY_NAME} 可用。", "ok")
            else:
                self._refresh_ai_status(f"连接失败：{data.get('message') or '未知错误'}", "error")

        def finished() -> None:
            self._ai_busy = False
            # 仅恢复按钮状态，保留测试结果文案。
            saved = AIB.key_store().has_key()
            self.ai_save_btn.setEnabled(True)
            self.ai_test_btn.setEnabled(True)
            self.ai_clear_btn.setEnabled(saved)

        submit(
            self,
            task,
            on_result=done,
            on_error=lambda msg: self._refresh_ai_status(f"连接失败：{AIB.scrub(msg)}", "error"),
            on_finished=finished,
        )

    # ---------------- 缓存管理 ----------------
    def _build_cache_card(self) -> QWidget:
        card = Card(
            "缓存",
            "只统计并清理可重新生成的运行缓存；游戏素材（assets）、授权信息、"
            "默认关联账号、用户设置与竞逐记录都不在范围内。",
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        self._cache_size_labels: Dict[str, QLabel] = {}
        for row, (kind, label, _size) in enumerate(CM.CacheUsage().rows()):
            name = QLabel(label)
            name.setObjectName("CacheKind")
            value = QLabel("—")
            value.setObjectName("CacheSize")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(name, row, 0)
            grid.addWidget(value, row, 1)
            self._cache_size_labels[kind] = value
        grid.setColumnStretch(0, 1)
        card.body.addLayout(grid)

        card.body.addWidget(hline())

        total_row = QHBoxLayout()
        total_row.setContentsMargins(0, 0, 0, 0)
        total_label = QLabel("总计")
        total_label.setObjectName("CacheTotalKind")
        total_row.addWidget(total_label, 1)
        self.cache_total = QLabel("—")
        self.cache_total.setObjectName("CacheTotalSize")
        self.cache_total.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        total_row.addWidget(self.cache_total, 0)
        card.body.addLayout(total_row)

        self.cache_paths = QLabel("")
        self.cache_paths.setObjectName("CachePath")
        self.cache_paths.setWordWrap(True)
        self.cache_paths.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.body.addWidget(self.cache_paths)

        btn_row = FlowFrame(margins=(0, 0, 0, 0), h_spacing=8, v_spacing=8, object_name="Plain")
        self.clear_detail_btn = make_button("清理详情缓存", "ghost", "删除单局详情磁盘缓存，下次打开会重新请求")
        self.clear_detail_btn.clicked.connect(lambda: self._clear_cache("detail"))
        btn_row.addWidget(self.clear_detail_btn)
        self.clear_avatar_btn = make_button("清理头像缓存", "ghost", "删除运行时下载的玩家头像，下次显示会重新下载")
        self.clear_avatar_btn.clicked.connect(lambda: self._clear_cache("avatar"))
        btn_row.addWidget(self.clear_avatar_btn)
        self.clear_all_btn = make_button("清理全部运行缓存", "primary")
        self.clear_all_btn.clicked.connect(lambda: self._clear_cache("all"))
        btn_row.addWidget(self.clear_all_btn)
        self.cache_refresh_btn = make_button("重新统计", "ghost")
        self.cache_refresh_btn.clicked.connect(self.refresh_cache_usage)
        btn_row.addWidget(self.cache_refresh_btn)
        card.body.addWidget(btn_row)
        return card

    def refresh_cache_usage(self) -> None:
        """后台统计缓存占用后刷新展示。目录不存在时按 0 B 处理。"""
        for label in getattr(self, "_cache_size_labels", {}).values():
            label.setText("统计中…")
        self.cache_total.setText("统计中…")
        submit(
            self,
            CM.collect_usage,
            on_result=self._apply_cache_usage,
            on_error=lambda _msg: self._apply_cache_usage(CM.CacheUsage()),
        )

    def _apply_cache_usage(self, usage: Any) -> None:
        data = usage if isinstance(usage, CM.CacheUsage) else CM.CacheUsage()
        for kind, _label, size in data.rows():
            widget = self._cache_size_labels.get(kind)
            if widget is not None:
                widget.setText(CM.format_size(size))
        self.cache_total.setText(CM.format_size(data.total))
        paths = data.paths or {}
        lines: List[str] = []
        for caption, key in (("详情缓存", CM.CACHE_KIND_DETAIL), ("图片缓存", CM.CACHE_KIND_AVATAR)):
            value = str(paths.get(key) or "").strip()
            if value:
                lines.append(f"{caption}：{value}")
        self.cache_paths.setText("\n".join(lines))
        self.cache_paths.setVisible(bool(lines))

    def _set_cache_busy(self, busy: bool) -> None:
        self._cache_busy = bool(busy)
        for btn in (self.clear_detail_btn, self.clear_avatar_btn, self.clear_all_btn,
                    self.cache_refresh_btn):
            btn.setEnabled(not busy)

    def _clear_cache(self, kind: str) -> None:
        if self._cache_busy:
            return
        target = str(kind or "all")
        if target == "all":
            confirm = QMessageBox(self)
            confirm.setWindowTitle("清理全部运行缓存")
            confirm.setIcon(QMessageBox.Question)
            confirm.setText(
                "将删除可重新生成的对局详情和图片缓存，\n"
                "不会删除游戏素材、授权信息或竞逐记录。"
            )
            confirm.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
            confirm.button(QMessageBox.Ok).setText("清理")
            confirm.button(QMessageBox.Cancel).setText("取消")
            if confirm.exec() != QMessageBox.Ok:
                return
        actions = {
            "detail": CM.clear_detail_cache,
            "avatar": CM.clear_avatar_cache,
            "all": CM.clear_all_runtime_cache,
        }
        action = actions.get(target)
        if action is None:
            return
        labels = {"detail": "详情缓存", "avatar": "头像缓存", "all": "全部运行缓存"}
        self._set_cache_busy(True)

        def done(freed: Any, t=target) -> None:
            size = CM.format_size(int(freed or 0))
            self.banner.show_message(f"已清理{labels[t]}，释放 {size}。", "ok")
            # 通知其它页面同步内存缓存；已打开页面的数据仍可继续使用。
            self.cacheCleared.emit(t)
            self.refresh_cache_usage()

        submit(
            self,
            action,
            on_result=done,
            on_error=lambda msg: self.banner.show_message(f"清理缓存失败：{msg}", "error"),
            on_finished=lambda: self._set_cache_busy(False),
        )

    def refresh_auth_status(self) -> None:
        status = self._stats_service.auth_status()
        configured = bool(status.get("configured"))
        masked = str(status.get("role_id_masked") or "")
        saved_masked = str(self._settings.value("dashen/role_id_masked", "") or "")
        account_name = str(self._settings.value("dashen/account_name", "") or "") if saved_masked == masked else ""
        if configured:
            self.dashen_status.setText("已授权")
            self.dashen_account.setText((f"当前账号：{account_name} · " if account_name else "当前账号：") + (masked or "已保存账号"))
            self.dashen_auth_btn.setText("重新授权")
            self.dashen_clear_btn.setEnabled(True)
        else:
            self.dashen_status.setText("未授权")
            self.dashen_account.setText("完成扫码后即可在“国服战绩”页查询 BattleTag。")
            self.dashen_auth_btn.setText("扫码授权")
            self.dashen_clear_btn.setEnabled(False)

    def _open_auth_dialog(self) -> None:
        dialog = DashenAuthDialog(self._stats_service, self)
        self._auth_dialog = dialog
        dialog.authorized.connect(self._auth_completed)
        dialog.finished.connect(lambda _=0: setattr(self, "_auth_dialog", None))
        dialog.open()

    def _auth_completed(self, _status: Any) -> None:
        self.refresh_auth_status()
        self.banner.show_message("网易大神授权成功。", "ok")
        self.authChanged.emit()

    def _clear_auth(self) -> None:
        self._stats_service.clear_credentials()
        self._settings.remove("dashen/account_name")
        self._settings.remove("dashen/role_id_masked")
        self.refresh_auth_status()
        self.banner.show_message("已清除网易大神授权。", "ok")
        self.authChanged.emit()

    # ---------------- 数据 ----------------
    def _parse_checkpoints(self) -> List[int]:
        raw = self.cp_edit.text().replace("，", ",")
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        out: List[int] = []
        for p in parts:
            if not p.isdigit():
                raise ValueError(f"节点必须是正整数：{p}")
            out.append(int(p))
        if not out:
            raise ValueError("至少需要一个节点")
        return out

    def _sync_penalty_labels(self) -> None:
        try:
            cps = self._parse_checkpoints()
        except ValueError as exc:
            self.cp_hint.setText(str(exc))
            return
        self.cp_hint.setText(f"{len(cps)} 个节点，最终目标 {cps[-1]} 分")
        for editor in self._editors.values():
            editor.rebuild_penalties(cps)

    def load_from_rules(self, rules: DriveRules) -> None:
        self._load_config(rules_to_dict(rules))

    def _load_config(self, config: Dict[str, Any]) -> None:
        cps = [int(x) for x in config.get("checkpoints", [])]
        self.cp_edit.setText(", ".join(str(x) for x in cps))
        self.cp_hint.setText(f"{len(cps)} 个节点，最终目标 {cps[-1] if cps else '-'} 分")
        modes = config.get("modes", {})
        for key, editor in self._editors.items():
            editor.load(modes.get(key, {}), cps)
        self._dp = config.get("dp", {"max_games": 5000, "epsilon": 1e-13})
        self._update_preview(config)

    def _reload_current(self, message: str = "已恢复为当前生效规则") -> None:
        self.load_from_rules(self._state.rules)
        self.banner.show_message(message, "info")

    def _collect(self) -> Dict[str, Any]:
        cps = self._parse_checkpoints()
        return {
            "version": 1,
            "checkpoints": cps,
            "modes": {key: editor.dump() for key, editor in self._editors.items()},
            "dp": getattr(self, "_dp", {"max_games": 5000, "epsilon": 1e-13}),
        }

    def _update_preview(self, config: Dict[str, Any]) -> None:
        cps = config.get("checkpoints", [])
        lines = [f"节点：{', '.join(str(x) for x in cps)}"]
        for key in MODE_ORDER:
            m = config.get("modes", {}).get(key)
            if not m:
                continue
            lines.append(
                f"{m.get('name', key)}：胜 +{m.get('win_points')}，"
                f"组排额外 +{m.get('group_bonus')}，"
                f"败 {', '.join('-' + str(x) for x in m.get('loss_penalties', []))}"
            )
        self.preview.setText("\n".join(lines))

    # ---------------- 动作 ----------------
    def _apply(self, config: Dict[str, Any], persist: bool) -> bool:
        try:
            rules = rules_from_dict(config)
        except ValueError as exc:
            self.banner.show_message(f"规则校验失败：{exc}", "error")
            return False
        if persist:
            try:
                path = save_rules(rules)
            except OSError as exc:
                self.banner.show_message(f"写入失败：{exc}", "error")
                return False
            self._state.set_rules(rules)
            self._update_preview(rules_to_dict(rules))
            self.banner.show_message(f"已保存并生效：{path.name}", "ok")
        else:
            self._state.set_rules(rules)
            self._update_preview(rules_to_dict(rules))
            self.banner.show_message("已生效（未写入文件）", "ok")
        return True

    def _save(self) -> None:
        try:
            config = self._collect()
        except ValueError as exc:
            self.banner.show_message(str(exc), "error")
            return
        self._apply(config, persist=True)

    def _restore_default(self) -> None:
        rules = default_rules()
        self._load_config(rules_to_dict(rules))
        self._state.set_rules(rules)
        self.banner.show_message("已恢复内置默认规则，点“应用并保存”写入文件", "warn")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入规则配置", str(Path.cwd()), "JSON 配置 (*.json);;所有文件 (*.*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                config = json.load(f)
            rules = rules_from_dict(config)
        except (OSError, json.JSONDecodeError) as exc:
            self.banner.show_message(f"读取失败：{exc}", "error")
            return
        except ValueError as exc:
            self.banner.show_message(f"配置不合法：{exc}", "error")
            return
        self._load_config(rules_to_dict(rules))
        self._state.set_rules(rules)
        self.banner.show_message(f"已导入 {Path(path).name} 并生效", "ok")

    def _export(self) -> None:
        try:
            config = self._collect()
            rules = rules_from_dict(config)
        except ValueError as exc:
            self.banner.show_message(f"当前规则不合法，无法导出：{exc}", "error")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出规则配置",
            str(Path.cwd() / "drive_rules.json"),
            "JSON 配置 (*.json)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rules_to_dict(rules), f, ensure_ascii=False, indent=2)
                f.write("\n")
        except OSError as exc:
            self.banner.show_message(f"导出失败：{exc}", "error")
            return
        self.banner.show_message(f"已导出到 {Path(path).name}", "ok")


__all__ = ["SettingsPage", "BUILTIN_CONFIG"]
