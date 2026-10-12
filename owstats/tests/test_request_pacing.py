import copy
from unittest.mock import patch
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from owstats.client import DashenClient, _GATES, _account_gate
from owstats.models import DashenCredential
from owstats.errors import RequestBlockedError
from owstats.service import OWStatsService


@pytest.fixture(autouse=True)
def gates():
    _GATES.clear()


def client(handler, role=9001):
    mock = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    with patch('owstats.client.httpx.Client', return_value=mock):
        c = DashenClient(DashenCredential(role, 'test-only'))
    return c


def test_shared_clients_serial_and_100ms():
    events = []
    lock = threading.Lock()
    def handler(request):
        with lock:
            events.append(('start', time.monotonic()))
        time.sleep(.02)
        with lock:
            events.append(('end', time.monotonic()))
        return httpx.Response(200, json={'code': 0})
    a, b = client(handler), client(handler)
    with a, b, ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda c: c.query_card('target'), [a, b]))
    assert [e[0] for e in events] == ['start', 'end', 'start', 'end']
    assert events[2][1] - events[1][1] >= .099


@pytest.mark.parametrize('http_code,body', [(403, {}), (200, {'code':403}), (200, {'code':'403'})])
def test_403_stops_other_client(http_code, body):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(http_code, json=body)
    with client(handler) as a, client(handler) as b:
        with pytest.raises(RequestBlockedError): a.query_card('a')
        with pytest.raises(RequestBlockedError): b.query_card('b')
    assert len(calls) == 1


def test_429_retry_after_no_auto_retry():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={'Retry-After':'120'}, json={})
    with client(handler) as a:
        with pytest.raises(RequestBlockedError): a.query_card('a')
        assert _account_gate(a.credential).retry_at - time.monotonic() > 119
        with pytest.raises(RequestBlockedError): a.query_card('a')
    assert len(calls) == 1


class Lists:
    def __init__(self):
        self.calls = []
        self.fail = False
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def query_match_list(self, token, mode, *, page, season):
        self.calls.append((mode, page, season))
        if self.fail: raise RequestBlockedError('blocked', status_code=403)
        values = range(100 if mode == 'sport' else 99, 0, -2) if mode in ('sport','leisure') and season is None else []
        rows = [{'matchId':str(v), 'beginTs':v, 'gameMode':mode} for v in list(values)[(page-1)*10:page*10]]
        return {'code':0,'data':{'matchList':rows}}
    fight_query_match_list = query_match_list


def service(fake):
    s = OWStatsService.__new__(OWStatsService)
    s.current_season = 24
    s._season_explicit = True
    s._new_client = lambda: fake
    return s


def test_pagination_merges_without_full_history_or_refetch():
    fake = Lists(); s = service(fake)
    first = s.match_overview_page(customer_token='target')
    assert [m['begin_ts'] for m in first['matches']] == list(range(100,80,-1))
    assert all(p <= 2 and season in (None,24) for _,p,season in fake.calls)
    old = copy.deepcopy(first['cursor'])
    count = len(fake.calls)
    second = s.match_overview_page(customer_token='target', cursor=first['cursor'])
    assert first['cursor'] == old
    assert [m['begin_ts'] for m in second['matches']] == list(range(80,60,-1))
    assert all(page > 1 for _,page,_ in fake.calls[count:])
    all_rows = first['matches'] + second['matches']
    while second['has_more']:
        second = s.match_overview_page(customer_token='target', cursor=second['cursor'])
        all_rows += second['matches']
    assert [m['begin_ts'] for m in all_rows] == list(range(100,0,-1))


def test_cursor_preserved_on_error_and_stops_immediately():
    fake=Lists(); s=service(fake)
    first=s.match_overview_page(customer_token='target')
    old=copy.deepcopy(first['cursor']); n=len(fake.calls); fake.fail=True
    with pytest.raises(RequestBlockedError):
        s.match_overview_page(customer_token='target', cursor=first['cursor'])
    assert first['cursor'] == old
    assert len(fake.calls) == n+1


def test_legacy_fallback_does_not_swallow_block():
    class Blocked:
        def query_count_info(self,*args,**kwargs): raise RequestBlockedError('blocked',status_code=403)
    s=service(Blocked())
    with pytest.raises(RequestBlockedError):
        s._count_info_with_current_fallback(Blocked(),'target','sport',24,[])


def test_full_detail_stops_before_next_player():
    s = service(None)
    s.match_detail = lambda *a, **kw: {
        'resolved': {'customer_token':'focus'}, 'detail_root':{}, 'detail':{},
        'scoreboard_players':[{'customer_token':str(i), 'name':str(i)} for i in range(10)]}
    called=[]
    def blocked(target,*a,**kw):
        called.append(target)
        raise RequestBlockedError('blocked',status_code=403)
    s._fetch_full_player_match_detail = blocked
    with pytest.raises(RequestBlockedError):
        s.match_all_players_detail('m',customer_token='focus',max_workers=8)
    assert len(called)==1
