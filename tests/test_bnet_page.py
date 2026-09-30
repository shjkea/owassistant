# -*- coding: utf-8 -*-
"""战网账号页离屏测试：用假后端模拟 BattleNetBackend，不触碰真实 Battle.net。

运行：
    python -m pytest tests/test_bnet_page.py -q
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
sys.path.insert(0, str(ROOT / "bnet_switcher_backend"))

import pytest  # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from bnet_backend import (  # noqa: E402
    AccountInfo,
    AccountKey,
    ClientStopTimeout,
    LoginProbe,
    LoginSession,
    ProgressEvent,
    SnapshotError,
    SwitchError,
    BattleNetBackendError,
)
from ui.pages import bnet_accounts as BA  # noqa: E402

CN = "cn.actual.battlenet.com.cn"
KR = "kr.actual.battle.net"
K1 = AccountKey(CN, "7:1")
K2 = AccountKey(KR, "7:2")
K3 = AccountKey(CN, "7:3")


def _app():
    return QApplication.instance() or QApplication([sys.argv[0]])


def pump(ms: int = 50) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(cond, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump(20)
        if cond():
            return
    raise AssertionError("等待超时")


class FakeBackend:
    def __init__(self) -> None:
        self.current = K1
        self.saved = {K1, K2}
        self.calls = []
        self.gui_thread = threading.get_ident()
        self.thread_violations = 0
        self.switch_gate = threading.Event()
        self.switch_gate.set()
        self.switch_error = None
        self.probe_status = "pending"
        self.env_override = None
        self.client_path = Path(r"C:\Battle.net\Battle.net.exe")

    def _mark(self, name):
        self.calls.append(name)
        if threading.get_ident() == self.gui_thread:
            self.thread_violations += 1

    def list_accounts(self):
        self._mark("list")
        rows = [(K1, "PublicDemo#12345", "CN"), (K2, "AltDemo#23456", "KR"), (K3, "Example#12345", "CN")]
        if self.env_override is not None:
            return self.env_override
        return [
            AccountInfo(k, tag, "", region, k == self.current, k in self.saved, 1700000000000 if k in self.saved else None)
            for k, tag, region in rows
        ]

    def switch_account(self, key, progress=None):
        self._mark("switch")
        for phase, pct in (("validating", 5), ("stoppingClient", 18), ("restoring", 58), ("weirdPhase", 70)):
            progress(ProgressEvent("switch", phase, "", pct))
        self.switch_gate.wait(5)
        if self.switch_error is not None:
            raise self.switch_error
        self.current = key
        progress(ProgressEvent("switch", "completed", "", 100))
        return None

    def begin_login(self, key, progress=None):
        self._mark("begin")
        progress(ProgressEvent("login", "preparing", "", 8))
        progress(ProgressEvent("login", "awaitingUser", "", 32))
        self.login_key = key
        return LoginSession("abc123", key, 0)

    def probe_login(self, sid):
        self._mark("probe")
        return LoginProbe(self.probe_status, self.login_key if self.probe_status == "ready" else None, "")

    def complete_login_and_save(self, sid, progress=None):
        self._mark("complete")
        progress(ProgressEvent("login", "capturing", "", 68))
        self.saved.add(self.login_key)
        self.current = self.login_key
        return None

    def cancel_login(self, sid, progress=None):
        self._mark("cancel")
        progress(ProgressEvent("login", "rollingBack", "", 60))
        progress(ProgressEvent("login", "cancelled", "", 100))

    def remove_saved_account(self, key):
        self._mark("remove")
        self.saved.discard(key)

    def set_client_path(self, path):
        return Path(path)


@pytest.fixture
def page(monkeypatch):
    _app()
    backend = FakeBackend()
    # 确认框一律点“确定”
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.Ok)
    p = BA.BattleNetPage(backend)
    p.resize(900, 700)
    p.show()
    wait_until(lambda: len(p._rows) == 3 and not p._busy)
    yield p, backend
    p._poll_timer.stop()
    p.close()


def _row(p, key):
    return next(r for r in p._rows if r.account.key == key)


def _texts(row):
    return [b.text() for b in row.buttons]


def test_list_and_labels(page):
    p, b = page
    assert "list" in b.calls
    r1, r2, r3 = _row(p, K1), _row(p, K2), _row(p, K3)
    assert "切换" not in _texts(r1) and "移除保存" in _texts(r1)
    assert _texts(r2)[0] == "切换"
    assert _texts(r3) == ["登录并保存"]
    assert BA.status_text(r1.account).startswith("当前账号 · 已保存")
    assert BA.status_text(r3.account) == "未保存"
    assert BA.region_label(r1.account) == "国服" and BA.region_label(r2.account) == "亚洲"
    odd = AccountInfo(AccountKey("xx.weird", "1:2"), "", "", None, False, False, None)
    assert BA.region_label(odd) == "xx.weird"
    assert BA.display_name(odd) == "账号 1:2"
    assert b.thread_violations == 0


def test_empty_list(page):
    p, b = page
    b.env_override = []
    p.refresh()
    wait_until(lambda: not p._busy)
    assert p._rows == [] and p.empty_label.isVisible()


def test_switch_success_and_busy_lock(page):
    p, b = page
    b.switch_gate.clear()
    p._switch(_row(p, K2).account)
    wait_until(lambda: "weirdPhase" not in p.progress_label.text() and "正在处理" in p.progress_label.text())
    # 进行中：所有操作禁用，重复点击无效
    assert p._busy and not p.refresh_btn.isEnabled()
    assert all(not btn.isEnabled() for r in p._rows for btn in r.buttons)
    p._switch(_row(p, K2).account)
    p.refresh()
    assert b.calls.count("switch") == 1
    # UI 不得提前假定成功
    assert _row(p, K1).account.is_current
    b.switch_gate.set()
    wait_until(lambda: not p._busy and _row(p, K2).account.is_current)
    assert p.banner.property("level") == "ok"
    assert b.thread_violations == 0


def test_switch_rollback_error(page):
    p, b = page
    try:
        raise SwitchError("切换失败：x；已恢复切换前配置") from SnapshotError("快照文件校验失败：a")
    except SwitchError as exc:
        b.switch_error = exc
    p._switch(_row(p, K2).account)
    wait_until(lambda: not p._busy and "list" in b.calls[-1:])
    wait_until(lambda: not p._busy)
    text = p.banner._text.text()
    assert "原账号配置已恢复" in text and "校验失败" in text
    assert "Traceback" not in text
    assert _row(p, K1).account.is_current


def test_error_messages():
    ow = BattleNetBackendError("检测到 Overwatch.exe 正在运行。为避免切号期间修改 Battle.net 状态，已取消操作。")
    assert BA.friendly_error(ow) == BA.MSG_OW_RUNNING
    assert BA.friendly_error(ClientStopTimeout("x")) == BA.MSG_STOP_FAILED
    assert BA.friendly_error(SnapshotError("目标账号没有有效快照")) == BA.MSG_SNAPSHOT
    assert BA.friendly_error(ValueError("boom")) == "操作失败：\nboom"
    assert BA.phase_text({"phase": "unknownX"}) == "正在处理…"


def test_login_flow_auto_probe(page):
    p, b = page
    p._begin_login(_row(p, K3).account)
    wait_until(lambda: p.has_active_login() and not p._busy)
    assert p.login_card.isVisible() and p._poll_timer.isActive()
    assert all(not btn.isEnabled() for r in p._rows for btn in r.buttons)
    b.probe_status = "ready"
    wait_until(lambda: "complete" in b.calls and not p._busy and not p.has_active_login(), timeout=8)
    wait_until(lambda: not p._busy and _row(p, K3).account.is_saved)
    assert not p.login_card.isVisible()
    assert b.thread_violations == 0


def test_login_cancel_and_close_guard(page):
    p, b = page
    p._begin_login(_row(p, K3).account)
    wait_until(lambda: p.has_active_login() and not p._busy)
    closed = []
    # 应用退出时：确认取消流程，完成后再真正关闭
    assert p.can_close(lambda: closed.append(True)) is False
    wait_until(lambda: closed and not p.has_active_login())
    assert "cancel" in b.calls


def test_remove_saved(page):
    p, b = page
    p._remove(_row(p, K2).account)
    wait_until(lambda: "remove" in b.calls)
    wait_until(lambda: not p._busy and not _row(p, K2).account.is_saved)
    assert _texts(_row(p, K2)) == ["登录并保存"]


def test_backend_unavailable():
    _app()
    p = BA.BattleNetPage(None, "该后端只支持 Windows 10/11")
    p.show()
    pump(50)
    assert p.banner.isVisible() and not p.refresh_btn.isEnabled()
    p.close()
