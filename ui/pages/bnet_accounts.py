# -*- coding: utf-8 -*-
"""战网账号页：把 bnet_backend（Battle.net 切号后端）接入现有 UI。

本文件只做前端：
- 所有 Battle.net 操作都调用 BattleNetBackend 的公开 API，不自行读写登录缓存 / UnifiedAuth；
- 所有后端调用都经 ui.async_worker.submit 放到线程池，结果通过 Qt 信号回到 GUI 线程；
- 后端 progress 回调在工作线程触发，经 progressEvent 信号（自动 queued）转回 GUI 线程；
- 网易网页登录 / 手机验证码完全由官方 Battle.net 与浏览器完成，本页只负责引导。
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..async_worker import submit
from ..widgets import Banner, Card, make_button

LOG = logging.getLogger("ui.bnet_accounts")

# 后端目录：bnet_switcher_backend/bnet_backend。不移动后端，只在导入失败时补 sys.path。
_BACKEND_DIR = Path(__file__).resolve().parents[2] / "bnet_switcher_backend"


def _import_backend_module():
    try:
        import bnet_backend  # type: ignore
    except ImportError:
        if _BACKEND_DIR.is_dir() and str(_BACKEND_DIR) not in sys.path:
            sys.path.insert(0, str(_BACKEND_DIR))
        import bnet_backend  # type: ignore
    return bnet_backend


def create_backend() -> Tuple[Optional[Any], str]:
    """创建唯一的 BattleNetBackend 实例；失败时返回 (None, 友好错误文案)。"""
    try:
        module = _import_backend_module()
        return module.BattleNetBackend(), ""
    except Exception as exc:  # 平台不支持 / 目录不可写等
        LOG.exception("BattleNetBackend 初始化失败")
        return None, friendly_error(exc)


# ---------------- 文案映射 ----------------
REGION_NAMES = {"CN": "国服", "KR": "亚洲", "US": "美洲", "EU": "欧洲"}

# phase 名称以 bnet_backend/core.py 实际 _emit 调用为准；未知 phase 显示“正在处理…”。
PHASE_TEXT = {
    "validating": "正在校验目标账号快照…",
    "preparing": "正在备份当前配置…",
    "stoppingClient": "正在关闭 Battle.net…",
    "clearingPointer": "正在准备官方登录页…",
    "awaitingUser": "请在 Battle.net 中完成登录…",
    "capturing": "正在保存账号登录数据…",
    "restoring": "正在恢复目标账号…",
    "launchingClient": "正在启动 Battle.net…",
    "verifying": "正在确认登录账号…",
    "rollingBack": "正在恢复原配置…",
    "completed": "完成",
    "cancelled": "已取消",
}

MSG_OW_RUNNING = "检测到 Overwatch 正在运行。\n请先退出游戏后再切换 Battle.net 账号。"
MSG_STOP_FAILED = "无法关闭 Battle.net，请手动退出后重试。"
MSG_MISMATCH = "当前登录的账号与目标账号不一致，请确认后重试。"
MSG_SNAPSHOT = "该账号的本地登录数据校验失败。\n建议重新登录并保存该账号。"
MSG_CLIENT_PATH = "未找到可用的 Battle.net.exe。\n请点击右上角“客户端路径”手动选择一次。"
MSG_PENDING = "尚未检测到登录完成，请在 Battle.net / 浏览器中完成登录后再试。"


def _exc_names(exc: BaseException) -> set:
    return {cls.__name__ for cls in type(exc).__mro__}


def _specific_reason(exc: BaseException) -> str:
    """按异常类型 + 后端文案给出可理解提示；识别不了返回空串。"""
    names = _exc_names(exc)
    text = str(exc).strip()
    if "Overwatch" in text:
        return MSG_OW_RUNNING
    if "ClientStopTimeout" in names:
        return MSG_STOP_FAILED
    if "ClientPathError" in names:
        return MSG_CLIENT_PATH
    if "UnsupportedPlatformError" in names:
        return "Battle.net 账号切换仅支持 Windows 10/11。"
    if "SnapshotError" in names:
        if "仍在变化" in text:
            return "Battle.net 配置仍在写入，请稍后重试。"
        return MSG_SNAPSHOT
    if "AccountDatabaseError" in names:
        return "无法读取 Battle.net 账号数据库。\n请确认已安装 Battle.net 并至少正常登录过一次。"
    if "确认目标账号超时" in text:
        return "Battle.net 未能在限定时间内确认目标账号。\n该账号的登录状态可能已失效，建议重新登录并保存。"
    if "确认账号前已退出" in text:
        return "Battle.net 在确认账号前意外退出，请重试。"
    if "不一致" in text:
        return MSG_MISMATCH
    if "未完成的登录流程" in text or "未完成登录流程" in text:
        return "存在未完成的首次登录流程，请先完成或取消后再操作。"
    if "LoginError" in names:
        if text.startswith("等待") or "未检测到本次登录" in text or "未保留登录完成标记" in text:
            return MSG_PENDING
        if "会话" in text:
            return "登录流程已失效，请重新点击“登录并保存”。"
        return f"登录保存失败：\n{text}"
    return ""


def friendly_error(exc: BaseException) -> str:
    """把后端异常转成用户能看懂的提示；详细信息由调用方写日志。"""
    text = str(exc).strip() or type(exc).__name__
    if "SwitchError" in _exc_names(exc):
        if "自动恢复也失败" in text:
            return "切换失败，且自动恢复原配置也失败。\n请手动退出 Battle.net 后重新登录；详细信息已写入日志。"
        if "已恢复切换前配置" in text:
            cause = exc.__cause__
            reason = _specific_reason(cause) if cause is not None else ""
            return "切换失败，原账号配置已恢复。" + (f"\n{reason}" if reason else "")
    return _specific_reason(exc) or f"操作失败：\n{text}"


def region_label(account: Any) -> str:
    region = str(getattr(account, "region", "") or "").strip().upper()
    if region in REGION_NAMES:
        return REGION_NAMES[region]
    key = getattr(account, "key", None)
    env = str(getattr(key, "environment", "") or "").strip().lower()
    if env.startswith("cn") or env.endswith(".cn"):
        return "国服"
    for prefix, name in (("kr", "亚洲"), ("us", "美洲"), ("eu", "欧洲")):
        if env.startswith(prefix):
            return name
    return region or env or "未知地区"


def display_name(account: Any) -> str:
    tag = str(getattr(account, "battle_tag", "") or "").strip()
    if tag:
        return tag
    internal = str(getattr(account, "internal_name", "") or "").strip()
    if internal:
        return internal
    key = getattr(account, "key", None)
    return f"账号 {getattr(key, 'account_id', '?')}"


def status_text(account: Any) -> str:
    parts: List[str] = []
    if getattr(account, "is_current", False):
        parts.append("当前账号")
    if getattr(account, "is_saved", False):
        saved = "已保存"
        ts = getattr(account, "last_saved_at", None)
        if isinstance(ts, (int, float)) and ts > 0:
            saved += time.strftime("（%Y-%m-%d %H:%M）", time.localtime(ts / 1000.0))
        parts.append(saved)
    else:
        parts.append("未保存")
    return " · ".join(parts)


def phase_text(event: dict) -> str:
    phase = str(event.get("phase") or "")
    text = PHASE_TEXT.get(phase, "正在处理…")
    if event.get("kind") == "recovery" and phase != "completed":
        text = "检测到上次未完成的切换，" + text
    return text


def _guarded(function: Callable[[], Any]) -> Callable[[], Any]:
    """在工作线程内把后端异常转换成友好文案；完整 traceback 只写日志。"""

    def run() -> Any:
        try:
            return function()
        except Exception as exc:
            LOG.exception("Battle.net 操作失败")
            raise RuntimeError(friendly_error(exc)) from None

    return run


# ---------------- 账号行 ----------------
class _AccountRow(QFrame):
    switchRequested = Signal(object)
    loginRequested = Signal(object)
    removeRequested = Signal(object)

    def __init__(self, account: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SubCard")
        self.account = account
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)

        info = QVBoxLayout()
        info.setSpacing(3)
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        name = QLabel(display_name(account))
        name.setProperty("role", "section")
        name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        name_row.addWidget(name)
        if account.is_current:
            tag = QLabel("当前账号")
            tag.setObjectName("RowEndorse")  # 复用详情页的小描边徽标样式
            name_row.addWidget(tag, 0, Qt.AlignVCenter)
        name_row.addStretch(1)
        info.addLayout(name_row)
        region = QLabel(region_label(account))
        region.setProperty("role", "fieldLabel")
        info.addWidget(region)
        status = QLabel(status_text(account))
        status.setProperty("role", "hint")
        info.addWidget(status)
        lay.addLayout(info, 1)

        self.buttons: List[Any] = []
        if account.is_saved and not account.is_current:
            btn = make_button("切换", "primary", "切换 Battle.net 到该账号")
            btn.clicked.connect(lambda: self.switchRequested.emit(self.account))
            self._add_button(lay, btn)
        if not account.is_saved:
            btn = make_button("登录并保存", "primary", "通过官方 Battle.net 登录一次并保存登录状态")
            btn.clicked.connect(lambda: self.loginRequested.emit(self.account))
            self._add_button(lay, btn)
        else:
            btn = make_button("重新保存", "ghost", "重新走一次官方登录并覆盖本工具保存的登录数据")
            btn.clicked.connect(lambda: self.loginRequested.emit(self.account))
            self._add_button(lay, btn)
            btn = make_button("移除保存", "ghost", "删除本工具保存的登录数据，不会删除 Battle.net 账号")
            btn.clicked.connect(lambda: self.removeRequested.emit(self.account))
            self._add_button(lay, btn)

    def _add_button(self, lay: QHBoxLayout, btn: Any) -> None:
        lay.addWidget(btn, 0, Qt.AlignVCenter)
        self.buttons.append(btn)

    def set_actions_enabled(self, enabled: bool) -> None:
        for btn in self.buttons:
            btn.setEnabled(enabled)


# ---------------- 页面 ----------------
class BattleNetPage(QWidget):
    """战网账号页。backend 由 MainWindow 创建并持有，整个应用生命周期只有一个实例。"""

    POLL_INTERVAL_MS = 1500
    # 后端 progress 回调在工作线程触发；跨线程 emit 走 queued 连接回到 GUI 线程。
    progressEvent = Signal(object)

    def __init__(self, backend: Optional[Any], init_error: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._init_error = init_error
        self._busy = False
        self._busy_kind = ""
        self._pending_refresh = False
        self._after_success: Optional[Callable[[], None]] = None
        self._op_succeeded = False
        self._progress_title = ""
        self._expect_current: Optional[Any] = None
        self._expect_name = ""
        self._accounts: List[Any] = []
        self._rows: List[_AccountRow] = []

        # 首次登录会话：页面切换时保留，完成 / 取消 / 应用退出时清理。
        self._active_login_session: Optional[Any] = None
        self._login_target_name = ""
        self._probe_inflight = False

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._probe_login)

        self.progressEvent.connect(self._on_progress)
        self._build_ui()
        if self._backend is None:
            self.banner.show_message(self._init_error or "Battle.net 切号后端不可用。", "error")
            self._show_empty("Battle.net 切号功能当前不可用。")
        self._update_controls()

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

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("战网账号")
        title.setProperty("role", "title")
        titles.addWidget(title)
        sub = QLabel("管理 Battle.net 已登录账号。首次保存需通过官方 Battle.net / 网易页面完成登录验证。")
        sub.setProperty("role", "subtitle")
        sub.setWordWrap(True)
        titles.addWidget(sub)
        head.addLayout(titles, 1)
        self.path_btn = make_button("客户端路径", "ghost", "手动选择 Battle.net.exe")
        self.path_btn.clicked.connect(self._choose_client_path)
        head.addWidget(self.path_btn, 0, Qt.AlignTop)
        self.refresh_btn = make_button("刷新", "ghost", "重新读取 Battle.net 账号与保存状态")
        self.refresh_btn.clicked.connect(self.refresh)
        head.addWidget(self.refresh_btn, 0, Qt.AlignTop)
        lay.addLayout(head)

        self.banner = Banner()
        lay.addWidget(self.banner)

        # 进度：直接展示后端 progress 事件，不做无意义的永久 loading。
        self.progress_card = Card()
        self.progress_label = QLabel("")
        self.progress_label.setProperty("role", "section")
        self.progress_label.setWordWrap(True)
        self.progress_card.body.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress_card.body.addWidget(self.progress_bar)
        self.progress_card.setVisible(False)
        lay.addWidget(self.progress_card)

        # 首次登录等待
        self.login_card = Card("等待官方登录")
        self.login_text = QLabel("")
        self.login_text.setWordWrap(True)
        self.login_card.body.addWidget(self.login_text)
        self.login_status = QLabel("")
        self.login_status.setProperty("role", "hint")
        self.login_status.setWordWrap(True)
        self.login_card.body.addWidget(self.login_status)
        login_row = QHBoxLayout()
        login_row.setContentsMargins(0, 0, 0, 0)
        login_row.setSpacing(8)
        self.login_done_btn = make_button("我已完成登录", "primary", "检测登录状态并保存")
        self.login_done_btn.clicked.connect(lambda: self._complete_login())
        login_row.addWidget(self.login_done_btn)
        self.login_cancel_btn = make_button("取消", "ghost", "取消登录流程并恢复登录前的 Battle.net 配置")
        self.login_cancel_btn.clicked.connect(lambda: self._cancel_login())
        login_row.addWidget(self.login_cancel_btn)
        login_row.addStretch(1)
        self.login_card.body.addLayout(login_row)
        self.login_card.setVisible(False)
        lay.addWidget(self.login_card)

        self.accounts_card = Card(
            "已识别账号",
            "来自本机 Battle.net 账号缓存。“移除保存”只删除本工具保存的登录数据，不会删除 Battle.net 账号。",
        )
        self.empty_label = QLabel("")
        self.empty_label.setObjectName("EmptyHint")
        self.empty_label.setWordWrap(True)
        self.empty_label.setVisible(False)
        self.accounts_card.body.addWidget(self.empty_label)
        self._list_lay = QVBoxLayout()
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(8)
        self.accounts_card.body.addLayout(self._list_lay)
        lay.addWidget(self.accounts_card)
        lay.addStretch(1)

    # ---------------- 生命周期 ----------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not event.spontaneous():
            self.refresh()

    def has_active_login(self) -> bool:
        return self._active_login_session is not None

    def can_close(self, retry_close: Callable[[], None]) -> bool:
        """应用退出前调用。True 表示可以直接关闭；False 表示页面接管后续处理。"""
        if self._busy and self._busy_kind != "refresh":
            QMessageBox.information(self, "战网账号", "正在执行 Battle.net 操作，请等待完成后再退出。")
            return False
        if self._active_login_session is None:
            self._poll_timer.stop()
            return True
        box = QMessageBox(self)
        box.setWindowTitle("首次登录流程未完成")
        box.setIcon(QMessageBox.Question)
        box.setText("还有未完成的 Battle.net 登录保存流程。\n退出前需要取消该流程并恢复登录前的配置。")
        box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        box.button(QMessageBox.Ok).setText("取消流程并退出")
        box.button(QMessageBox.Cancel).setText("返回")
        if box.exec() == QMessageBox.Ok:
            self._cancel_login(then=lambda: QTimer.singleShot(0, retry_close))
        return False

    # ---------------- 通用任务 ----------------
    def _progress_cb(self, event: Any) -> None:
        """工作线程调用：只 emit 信号，不触碰任何 QWidget。"""
        try:
            data = event.to_dict()
        except Exception:
            data = {"phase": getattr(event, "phase", ""), "kind": getattr(event, "kind", ""),
                    "progress": getattr(event, "progress", 0), "detail": getattr(event, "detail", "")}
        self.progressEvent.emit(data)

    @Slot(object)
    def _on_progress(self, data: Any) -> None:
        if not isinstance(data, dict) or not self.progress_card.isVisible():
            return
        text = phase_text(data)
        self.progress_label.setText(f"{self._progress_title} · {text}" if self._progress_title else text)
        self.progress_label.setToolTip(str(data.get("detail") or ""))
        try:
            value = int(data.get("progress") or 0)
        except (TypeError, ValueError):
            value = 0
        self.progress_bar.setValue(max(0, min(100, value)))

    def _show_progress(self, title: str, first_text: str) -> None:
        self._progress_title = title
        self.progress_label.setText(f"{title} · {first_text}")
        self.progress_label.setToolTip("")
        self.progress_bar.setValue(0)
        self.progress_card.setVisible(True)

    def _start(
        self,
        kind: str,
        function: Callable[[], Any],
        *,
        on_result: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        after_success: Optional[Callable[[], None]] = None,
    ) -> bool:
        """同一时间只允许一个 Battle.net 任务。"""
        if self._backend is None or self._busy:
            return False
        self._busy = True
        self._busy_kind = kind
        self._op_succeeded = False
        self._after_success = after_success
        self._update_controls()

        def result(value: Any) -> None:
            self._op_succeeded = True
            if on_result is not None:
                on_result(value)

        def finished() -> None:
            self._busy = False
            self._busy_kind = ""
            self.progress_card.setVisible(False)
            follow = self._after_success if self._op_succeeded else None
            self._after_success = None
            self._update_controls()
            if self._pending_refresh:
                self._pending_refresh = False
                self.refresh()
            if follow is not None:
                follow()

        submit(self, _guarded(function), on_result=result, on_error=on_error, on_finished=finished)
        return True

    def _update_controls(self) -> None:
        idle = self._backend is not None and not self._busy
        login_active = self._active_login_session is not None
        self.refresh_btn.setEnabled(idle)
        self.refresh_btn.setText("刷新中…" if self._busy_kind == "refresh" else "刷新")
        self.path_btn.setEnabled(idle and not login_active)
        for row in self._rows:
            row.set_actions_enabled(idle and not login_active)
        self.login_done_btn.setEnabled(idle and login_active)
        self.login_cancel_btn.setEnabled(idle and login_active)
        if self._backend is not None:
            path = getattr(self._backend, "client_path", None)
            self.path_btn.setToolTip(f"当前 Battle.net.exe：{path}" if path else "未找到 Battle.net.exe，点击手动选择")

    # ---------------- 刷新 ----------------
    def refresh(self) -> None:
        if self._backend is None:
            return
        if self._busy:
            if self._busy_kind != "refresh":
                self._pending_refresh = True
            return
        self._start("refresh", self._backend.list_accounts,
                    on_result=self._apply_accounts, on_error=self._on_refresh_error)

    def _clear_rows(self) -> None:
        for row in self._rows:
            self._list_lay.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self._rows = []

    def _show_empty(self, text: str) -> None:
        self._clear_rows()
        self.empty_label.setText(text)
        self.empty_label.setVisible(True)

    def _apply_accounts(self, accounts: Any) -> None:
        self._accounts = list(accounts or [])
        self._clear_rows()
        if not self._accounts:
            self._show_empty("未在本机 Battle.net 中找到登录过的账号。\n请先在 Battle.net 中正常登录一次，然后点击“刷新”。")
        else:
            self.empty_label.setVisible(False)
            for account in self._accounts:
                row = _AccountRow(account)
                row.switchRequested.connect(self._switch)
                row.loginRequested.connect(self._begin_login)
                row.removeRequested.connect(self._remove)
                self._list_lay.addWidget(row)
                self._rows.append(row)
        self._check_expected_current()
        self._update_controls()

    def _on_refresh_error(self, message: str) -> None:
        self._accounts = []
        self._show_empty("无法读取 Battle.net 账号列表。")
        self.banner.show_message(message, "error")

    def _check_expected_current(self) -> None:
        """切换成功后以重新读取的账号列表为准确认当前账号，不由 UI 自行假定。"""
        expected = self._expect_current
        if expected is None:
            return
        self._expect_current = None
        current = next((a for a in self._accounts if a.is_current), None)
        if current is not None and current.key == expected:
            self.banner.show_message(f"已切换到 {self._expect_name}，Battle.net 已确认当前登录账号。", "ok")
        else:
            self.banner.show_message(
                f"后端已完成切换，但重新读取时当前账号不是 {self._expect_name}。请稍后点击“刷新”确认。", "warn"
            )

    # ---------------- 切换 ----------------
    def _switch(self, account: Any) -> None:
        if self._busy or self._active_login_session is not None:
            return
        if not account.is_saved or account.is_current:
            return
        name = display_name(account)
        key = account.key
        self.banner.clear()
        self._show_progress(f"正在切换到 {name}", "正在准备…")

        def done(_info: Any, k=key, n=name) -> None:
            self._expect_current = k
            self._expect_name = n
            self._pending_refresh = True

        def failed(message: str) -> None:
            self._expect_current = None
            text = message if message.startswith("切换失败") else f"切换失败\n{message}"
            self.banner.show_message(text, "error")
            self._pending_refresh = True  # 无论是否 rollback 都以后端实际状态重新刷新

        self._start("switch", lambda: self._backend.switch_account(key, progress=self._progress_cb),
                    on_result=done, on_error=failed)

    # ---------------- 首次登录 / 重新保存 ----------------
    def _begin_login(self, account: Any) -> None:
        if self._busy or self._active_login_session is not None:
            return
        name = display_name(account)
        confirm = QMessageBox(self)
        confirm.setWindowTitle("登录并保存")
        confirm.setIcon(QMessageBox.Question)
        confirm.setText(
            f"将关闭并重新打开 Battle.net，进入官方登录页。\n"
            f"请在 Battle.net / 浏览器中登录 {name}（国服可能需要网易网页登录和手机验证码）。\n\n是否继续？"
        )
        confirm.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        confirm.button(QMessageBox.Ok).setText("继续")
        confirm.button(QMessageBox.Cancel).setText("取消")
        if confirm.exec() != QMessageBox.Ok:
            return
        key = account.key
        self.banner.clear()
        self._show_progress(f"准备登录 {name}", "正在准备…")

        def started(session: Any, n=name) -> None:
            self._active_login_session = session
            self._login_target_name = n
            self.login_text.setText(
                f"请在 Battle.net / 浏览器中完成 {n} 的登录。\n"
                "登录完成后本页会自动检测并保存；也可以点击“我已完成登录”。"
            )
            self.login_status.setText("等待登录…")
            self.login_card.setVisible(True)
            self._poll_timer.start()

        def failed(message: str) -> None:
            self.banner.show_message(f"无法开始登录流程\n{message}", "error")
            self._pending_refresh = True

        self._start("begin_login", lambda: self._backend.begin_login(key, progress=self._progress_cb),
                    on_result=started, on_error=failed)

    def _probe_login(self) -> None:
        session = self._active_login_session
        if session is None or self._backend is None:
            self._poll_timer.stop()
            return
        if self._busy or self._probe_inflight:
            return
        self._probe_inflight = True
        sid = session.id

        def done() -> None:
            self._probe_inflight = False

        submit(
            self,
            _guarded(lambda: self._backend.probe_login(sid)),
            on_result=lambda probe, s=sid: self._on_probe(s, probe),
            on_error=lambda msg, s=sid: self._on_probe_error(s, msg),
            on_finished=done,
        )

    def _session_matches(self, sid: str) -> bool:
        session = self._active_login_session
        return session is not None and session.id == sid

    def _on_probe(self, sid: str, probe: Any) -> None:
        if not self._session_matches(sid) or self._busy:
            return
        status = str(getattr(probe, "status", "") or "")
        if status == "ready":
            self.login_status.setText("已检测到登录完成，正在保存…")
            self._complete_login()
        elif status == "wrong_account":
            current = getattr(probe, "current", None)
            current_name = ""
            for account in self._accounts:
                if current is not None and account.key == current:
                    current_name = display_name(account)
            suffix = f"（当前：{current_name}）" if current_name else ""
            self.login_status.setText(
                f"当前登录的账号与目标账号不一致{suffix}。\n请在 Battle.net 中退出后改用 {self._login_target_name} 登录。"
            )
        else:
            self.login_status.setText("尚未检测到登录完成，请在 Battle.net / 浏览器中继续操作…")

    def _on_probe_error(self, sid: str, message: str) -> None:
        if self._session_matches(sid):
            self.login_status.setText(message)

    def _complete_login(self) -> None:
        session = self._active_login_session
        if session is None or self._busy:
            return
        sid = session.id
        name = self._login_target_name
        self._poll_timer.stop()
        self._show_progress(f"正在保存 {name}", "正在确认登录状态…")

        def saved(_info: Any, n=name) -> None:
            self._clear_login_session()
            self.banner.show_message(f"账号已保存：{n}", "ok")
            self._pending_refresh = True

        def failed(message: str) -> None:
            if "登录流程已失效" in message:
                self._clear_login_session()
                self.banner.show_message(message, "error")
                self._pending_refresh = True
                return
            self.login_status.setText(message)
            if self._active_login_session is not None:
                self._poll_timer.start()

        self._start("complete_login",
                    lambda: self._backend.complete_login_and_save(sid, progress=self._progress_cb),
                    on_result=saved, on_error=failed)

    def _cancel_login(self, then: Optional[Callable[[], None]] = None) -> None:
        session = self._active_login_session
        if session is None:
            if then is not None:
                then()
            return
        if self._busy:
            return
        sid = session.id
        self._poll_timer.stop()
        self._show_progress("正在取消登录流程", "正在恢复原配置…")

        def cancelled(_value: Any) -> None:
            self._clear_login_session()
            self.banner.show_message("已取消登录流程，Battle.net 已恢复到登录前的配置。", "info")
            self._pending_refresh = True

        def failed(message: str) -> None:
            self.banner.show_message(f"取消登录流程失败\n{message}", "error")
            if "登录流程已失效" in message:
                self._clear_login_session()
            elif self._active_login_session is not None:
                self._poll_timer.start()

        self._start("cancel_login", lambda: self._backend.cancel_login(sid, progress=self._progress_cb),
                    on_result=cancelled, on_error=failed, after_success=then)

    def _clear_login_session(self) -> None:
        self._poll_timer.stop()
        self._active_login_session = None
        self._login_target_name = ""
        self.login_card.setVisible(False)
        self._update_controls()

    # ---------------- 移除保存 ----------------
    def _remove(self, account: Any) -> None:
        if self._busy or self._active_login_session is not None or not account.is_saved:
            return
        name = display_name(account)
        confirm = QMessageBox(self)
        confirm.setWindowTitle("移除保存")
        confirm.setIcon(QMessageBox.Question)
        confirm.setText(f"{name}\n\n移除保存后，下次使用该账号需要重新完成官方登录验证。\n\n是否继续？")
        confirm.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        confirm.button(QMessageBox.Ok).setText("移除保存")
        confirm.button(QMessageBox.Cancel).setText("取消")
        if confirm.exec() != QMessageBox.Ok:
            return
        key = account.key

        def done(_value: Any, n=name) -> None:
            self.banner.show_message(f"已移除 {n} 的保存数据。", "ok")
            self._pending_refresh = True

        def failed(message: str) -> None:
            self.banner.show_message(f"移除保存失败\n{message}", "error")
            self._pending_refresh = True

        self._start("remove", lambda: self._backend.remove_saved_account(key), on_result=done, on_error=failed)

    # ---------------- 客户端路径 ----------------
    def _choose_client_path(self) -> None:
        if self._busy or self._backend is None:
            return
        current = getattr(self._backend, "client_path", None)
        start_dir = str(Path(current).parent) if current else r"C:\Program Files (x86)"
        path, _ = QFileDialog.getOpenFileName(self, "选择 Battle.net.exe", start_dir, "Battle.net.exe (Battle.net.exe)")
        if not path:
            return

        def done(value: Any) -> None:
            self.banner.show_message(f"已设置 Battle.net 路径：{value}", "ok")

        def failed(message: str) -> None:
            self.banner.show_message(f"设置 Battle.net 路径失败\n{message}", "error")

        self._start("set_path", lambda: self._backend.set_client_path(path), on_result=done, on_error=failed)


__all__ = ["BattleNetPage", "create_backend", "friendly_error", "region_label", "phase_text"]
