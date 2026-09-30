# -*- coding: utf-8 -*-
"""网易大神扫码授权对话框。网络、轮询和 MiniRacer 签名均在后台线程。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QSettings, QTimer, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from owstats import OWStatsService

from .async_worker import submit
from .theme import C
from .widgets import Banner, Card, make_button


_STATE_TEXT = {
    "waiting_scan": "请使用网易大神 App 扫描二维码",
    "waiting_confirm": "已扫码，请在手机上确认登录",
    "authorizing": "正在完成网页登录签名与账号读取…",
    "roles_ready": "已读取战网账号，请选择账号并完成授权",
    "expired": "二维码已过期，请重新获取",
}


def missing_signer_dependency() -> str:
    """扫码授权必须执行官方 JS/WASM 签名；缺 mini-racer 时给出可执行提示。

    没有这层自检时，缺依赖只会在轮询到签名那一步冒出笼统的“授权失败”，
    排查成本很高。
    """
    try:
        import py_mini_racer  # noqa: F401
    except Exception:
        return (
            "缺少签名运行环境 mini-racer，无法完成扫码授权。\n"
            "请在项目目录执行：python -m pip install -r requirements.txt"
        )
    return ""


class DashenAuthDialog(QDialog):
    authorized = Signal(object)

    def __init__(self, service: OWStatsService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._service = service
        self._session_id = ""
        self._poll_busy = False
        self._closing = False
        self._selected_role: Dict[str, Any] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._poll)

        self.setWindowTitle("网易大神扫码授权")
        self.setModal(True)
        self.setMinimumWidth(430)
        self.resize(470, 570)
        self._build_ui()
        self._dependency_error = missing_signer_dependency()
        if self._dependency_error:
            self.qr.setText("签名环境不可用")
            self.status.setText("安装依赖后重新打开此窗口")
            self.banner.show_message(self._dependency_error, "error")
            self.restart_btn.setEnabled(False)
        else:
            QTimer.singleShot(0, self.restart)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel("网易大神扫码授权")
        title.setProperty("role", "title")
        root.addWidget(title)
        sub = QLabel("二维码、Cookie 与官方 JS/WASM 签名都在应用内部处理，不会启动浏览器或本地 HTTP 服务。")
        sub.setProperty("role", "subtitle")
        sub.setWordWrap(True)
        root.addWidget(sub)

        self.banner = Banner()
        root.addWidget(self.banner)

        card = Card()
        root.addWidget(card, 1)
        self.qr = QLabel("正在获取二维码…")
        self.qr.setAlignment(Qt.AlignCenter)
        self.qr.setMinimumSize(290, 290)
        self.qr.setStyleSheet(f"background:{C.SURFACE_2}; border:1px solid {C.BORDER}; border-radius:8px;")
        card.body.addWidget(self.qr, 0, Qt.AlignHCenter)

        self.status = QLabel("准备授权…")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setProperty("role", "hint")
        self.status.setWordWrap(True)
        card.body.addWidget(self.status)

        self.roles = QComboBox()
        self.roles.setVisible(False)
        card.body.addWidget(self.roles)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.restart_btn = make_button("重新获取二维码", "ghost")
        self.restart_btn.clicked.connect(self.restart)
        row.addWidget(self.restart_btn)
        row.addStretch(1)
        self.complete_btn = make_button("授权所选账号", "primary")
        self.complete_btn.setEnabled(False)
        self.complete_btn.clicked.connect(self._complete)
        row.addWidget(self.complete_btn)
        card.body.addLayout(row)

    def restart(self) -> None:
        if self._dependency_error:
            self.banner.show_message(self._dependency_error, "error")
            return
        old = self._session_id
        self._session_id = ""
        self._timer.stop()
        self._poll_busy = False
        self.roles.clear()
        self.roles.setVisible(False)
        self.complete_btn.setEnabled(False)
        self.restart_btn.setEnabled(False)
        self.qr.clear()
        self.qr.setText("正在获取二维码…")
        self.status.setText("连接网易大神二维码服务…")
        self.banner.clear()
        if old:
            submit(self, lambda sid=old: self._service.auth_cancel(sid))
        submit(self, self._service.auth_start, on_result=self._started, on_error=self._failed_start,
               on_finished=lambda: self.restart_btn.setEnabled(True))

    def _started(self, start: Any) -> None:
        if self._closing:
            return
        self._session_id = str(start.session_id)
        pix = QPixmap()
        if not pix.loadFromData(start.qr_png):
            self._failed_start("二维码图片解析失败")
            return
        self.qr.setText("")
        self.qr.setPixmap(pix.scaled(260, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.status.setText(_STATE_TEXT["waiting_scan"])
        self._timer.start(max(500, int(start.poll_interval_ms)))

    def _failed_start(self, message: str) -> None:
        if self._closing:
            return
        self.qr.setText("二维码获取失败")
        self.status.setText("请点击“重新获取二维码”")
        self.banner.show_message(message, "error")

    def _poll(self) -> None:
        if self._closing or self._poll_busy or not self._session_id:
            return
        self._poll_busy = True
        sid = self._session_id
        submit(
            self,
            lambda: self._service.auth_poll(sid),
            on_result=self._polled,
            on_error=self._poll_failed,
            on_finished=self._poll_finished,
        )

    def _poll_finished(self) -> None:
        self._poll_busy = False

    def _polled(self, state: Any) -> None:
        if self._closing or not self._session_id:
            return
        name = str(state.state)
        self.status.setText(_STATE_TEXT.get(name, f"授权状态：{name}"))
        if name == "roles_ready":
            self._timer.stop()
            self.roles.clear()
            for role in state.roles:
                role = dict(role or {})
                role_id = str(role.get("role_id") or "")
                role_name = str(role.get("name") or "战网账号")
                server = str(role.get("server") or "")
                masked = ("*" * max(0, len(role_id) - 4) + role_id[-4:]) if role_id else ""
                label = f"{role_name}  {server}".strip()
                if masked:
                    label += f"  · {masked}"
                self.roles.addItem(label, role)
            self.roles.setVisible(True)
            self.complete_btn.setEnabled(self.roles.count() > 0)
            return
        if name == "expired":
            self._timer.stop()
            self._session_id = ""
            return
        self._timer.start(max(500, int(getattr(state, "poll_interval_ms", 2000))))

    def _poll_failed(self, message: str) -> None:
        if self._closing:
            return
        self.banner.show_message(message, "error")
        # 网络瞬断不直接销毁扫码会话，下一轮仍可继续。
        if self._session_id:
            self._timer.start(2500)

    def _complete(self) -> None:
        if not self._session_id or self.roles.currentIndex() < 0:
            return
        role = dict(self.roles.currentData() or {})
        role_id = str(role.get("role_id") or "")
        if not role_id:
            self.banner.show_message("战网账号 ID 无效，请重新授权", "error")
            return
        self._selected_role = role
        self.complete_btn.setEnabled(False)
        self.restart_btn.setEnabled(False)
        self.status.setText("正在生成最终签名并保存授权…")
        sid = self._session_id
        submit(
            self,
            lambda: self._service.auth_complete(sid, role_id),
            on_result=self._completed,
            on_error=self._complete_failed,
            on_finished=lambda: self.restart_btn.setEnabled(True),
        )

    def _completed(self, status: Any) -> None:
        result = dict(status or {})
        result["account_name"] = str(self._selected_role.get("name") or "")
        result["server_name"] = str(self._selected_role.get("server") or "")
        settings = QSettings("ow_drive_split", "drive_calculator")
        settings.setValue("dashen/account_name", result["account_name"])
        settings.setValue("dashen/role_id_masked", str(result.get("role_id_masked") or ""))
        self._session_id = ""  # auth_complete 已关闭会话。
        self.banner.show_message("授权成功，已保存到本机。", "ok")
        self.status.setText("授权完成")
        self.authorized.emit(result)
        QTimer.singleShot(350, self.accept)

    def _complete_failed(self, message: str) -> None:
        self.complete_btn.setEnabled(self.roles.count() > 0)
        self.banner.show_message(message, "error")
        self.status.setText("授权未完成，可重试或重新获取二维码")

    def reject(self) -> None:
        self._closing = True
        self._timer.stop()
        sid = self._session_id
        self._session_id = ""
        if sid:
            submit(self, lambda: self._service.auth_cancel(sid))
        super().reject()
