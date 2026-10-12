import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool

_APP = None
from ui.state import AppState
from ui.pages import owstats as module
from owstats_ui_smoke_test import FakeService


def test_overview_load_more_and_click_detail(monkeypatch, tmp_path):
    global _APP
    _APP = QApplication.instance() or QApplication([])
    app = _APP
    class Fake(FakeService):
        def match_overview_page(self, *, customer_token, cursor=None, limit=20):
            rows = super().match_history(customer_token=customer_token)['matches']
            rows.sort(key=lambda r: r['begin_ts'], reverse=True)
            start = cursor or 0
            return {'matches':rows[start:start+limit], 'cursor':start+limit,
                    'has_more':start+limit<len(rows), 'current_season':self.current_season}
        def match_history(self, **kwargs):
            raise AssertionError('UI must not fetch full history')
    def sync(owner, task, on_result=None, on_error=None, on_finished=None):
        try:
            result=task()
            if on_result: on_result(result)
        finally:
            if on_finished: on_finished()
    monkeypatch.setattr(module, 'submit', sync)
    monkeypatch.setattr(module.AvatarCache, "_request", lambda self, url: None)
    fake=Fake(matches_per_season=30)
    state = AppState()
    page=module.OWStatsPage(state, fake)
    from ui.detail_cache import MatchDetailCache
    page._detail_cache=MatchDetailCache(tmp_path)
    try:
        # 先处理构造函数安排的零时定时器，避免留到进程退出时执行。
        app.processEvents()
        page.query_edit.setText('Demo#1234')
        page.search()
        assert len(page._matches)==20
        assert fake.detail_calls==[]
        assert not page.load_more_btn.isHidden()
        page.load_more_matches()
        assert len(page._matches)==40
        assert len(page._match_index)==40
        assert fake.detail_calls==[]
        page.open_detail(page._matches[0]['match_id'])
        assert len(fake.detail_calls)==1
        page._close_detail()
        page.open_detail(page._matches[0]['match_id'])
        assert len(fake.detail_calls)==1
    finally:
        page.close()
        assert QThreadPool.globalInstance().waitForDone(3000), "测试后台任务未结束"
        app.processEvents()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
