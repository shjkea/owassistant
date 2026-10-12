# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
import time
from threading import Lock
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

from .errors import UpstreamError, RequestBlockedError
from .models import DashenCredential

DASHEN_API_ROOT = "https://datamsapi.ds.163.com/v1/a19ld5tool"
DASHEN_CUSTOMER_API_BASE = f"{DASHEN_API_ROOT}/customer"
DASHEN_BILLBOARD_API_BASE = f"{DASHEN_API_ROOT}/billboard"
SEARCH_BNET_ACCOUNT_URL = f"{DASHEN_API_ROOT}/searchBnetAccount"

DEFAULT_CLIENT_TYPE = "60"
DEFAULT_ORIGIN = "https://act.ds.163.com"
DEFAULT_REFERER = "https://act.ds.163.com/"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36 "
    "app/df_client dfVersion/100111"
)


class _AccountGate:
    def __init__(self):
        self.lock = Lock()
        self.finished = 0.0
        self.forbidden = False
        self.retry_at = 0.0


_GATES = {}
_GATES_LOCK = Lock()
REQUEST_INTERVAL_SECONDS = 0.1


def _account_gate(credential):
    # 不随 client 实例或重新扫码更换 token 而重置。
    key = (str(credential.server), str(credential.role_id))
    with _GATES_LOCK:
        return _GATES.setdefault(key, _AccountGate())


def _retry_seconds(value):
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 60.0


class DashenClient:
    """直接请求网易大神国服战绩上游；不启动 localhost / HTTP 服务。"""

    def __init__(
        self,
        credential: DashenCredential,
        *,
        timeout: float = 20.0,
        proxy: str | None = None,
        client_type: str = DEFAULT_CLIENT_TYPE,
        origin: str = DEFAULT_ORIGIN,
        referer: str = DEFAULT_REFERER,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.credential = credential
        self.client_type = str(client_type)
        self.origin = str(origin)
        self.referer = str(referer)
        self.user_agent = str(user_agent)
        kwargs: Dict[str, Any] = {
            "timeout": httpx.Timeout(timeout, connect=min(timeout, 8.0)),
            "follow_redirects": True,
            "headers": {"Accept": "application/json, text/plain, */*", "User-Agent": self.user_agent},
        }
        if proxy:
            kwargs["proxy"] = proxy
        self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DashenClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _auth_headers(self, *, dts_override: Optional[int] = None) -> Dict[str, str]:
        c = self.credential
        return {
            "Accept": "application/json, text/plain, */*",
            "GL-Bigdata-Auth-Token": c.token,
            "GL-Bigdata-Dts": str(c.dts if dts_override is None else int(dts_override)),
            "GL-Bigdata-Role-Id": str(c.role_id),
            "GL-Bigdata-Server": str(c.server),
            "GL-ClientType": self.client_type,
            "Origin": self.origin,
            "Referer": self.referer,
            "User-Agent": self.user_agent,
        }

    def _request_json(self, method, url, **kwargs):
        gate = _account_gate(self.credential)
        with gate.lock:
            if gate.forbidden:
                raise RequestBlockedError("该账号已收到 403，本次运行已停止战绩查询，请先确认账号权限。", status_code=403)
            if time.monotonic() < gate.retry_at:
                raise RequestBlockedError("请求过于频繁，冷却期间已停止查询，请稍后手动重试。", status_code=429)
            delay = gate.finished + REQUEST_INTERVAL_SECONDS - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            try:
                payload = self._request_json_unthrottled(method, url, **kwargs)
                if str(payload.get("code")) in ("403", "429"):
                    raise UpstreamError("网易大神拒绝查询", code=payload["code"])
                return payload
            except UpstreamError as exc:
                code = str(exc.status_code or exc.code)
                if code == "403":
                    gate.forbidden = True
                    raise RequestBlockedError("网易大神返回 403，已停止该账号后续战绩请求。", status_code=403) from exc
                if code == "429":
                    gate.retry_at = time.monotonic() + getattr(exc, "retry_after_seconds", 60.0)
                    raise RequestBlockedError("网易大神返回 429，已暂停查询，请冷却后手动重试。", status_code=429) from exc
                raise
            finally:
                gate.finished = time.monotonic()

    def _request_json_unthrottled(
        self,
        method: str,
        url: str,
        *,
        params: Dict[str, Any] | None = None,
        json_body: Dict[str, Any] | None = None,
        dts_override: Optional[int] = None,
    ) -> Dict[str, Any]:
        try:
            r = self._client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=self._auth_headers(dts_override=dts_override),
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            payload: Any = None
            try:
                payload = exc.response.json()
            except Exception:
                pass
            error = UpstreamError(
                f"网易大神接口 HTTP {exc.response.status_code}",
                status_code=exc.response.status_code,
                payload=payload,
            )
            error.retry_after_seconds = _retry_seconds(exc.response.headers.get("Retry-After"))
            raise error from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"连接网易大神失败：{type(exc).__name__}: {exc}") from exc
        try:
            payload = r.json()
        except ValueError as exc:
            raise UpstreamError("网易大神接口返回了非 JSON 数据", status_code=r.status_code) from exc
        if not isinstance(payload, dict):
            raise UpstreamError("网易大神接口返回格式异常", status_code=r.status_code, payload=payload)
        return payload

    def search_bnet_account(self, battletag: str) -> Dict[str, Any]:
        c = self.credential
        name = str(battletag or "").replace("＃", "#").strip()
        return self._request_json(
            "POST",
            SEARCH_BNET_ACCOUNT_URL,
            json_body={
                "token": c.token,
                "roleId": c.role_id,
                "dts": c.dts,
                "server": c.server,
                "name": name,
            },
        )

    def query_card(self, customer_token: str) -> Dict[str, Any]:
        return self._request_json(
            "GET",
            f"{DASHEN_CUSTOMER_API_BASE}/queryCard",
            params={"token": str(customer_token)},
            dts_override=self.credential.dts,
        )

    def query_count_info(
        self,
        customer_token: str,
        game_mode: str,
        *,
        season: Optional[int] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"gameMode": game_mode, "token": str(customer_token)}
        if season is not None:
            params["season"] = int(season)
        return self._request_json(
            "GET",
            f"{DASHEN_CUSTOMER_API_BASE}/queryCountInfo",
            params=params,
            dts_override=self.credential.dts,
        )

    def query_match_list(
        self,
        customer_token: str,
        game_mode: str,
        *,
        page: int = 1,
        season: Optional[int] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "token": str(customer_token),
            "gameMode": str(game_mode),
            "page": max(1, int(page)),
        }
        if season is not None:
            params["season"] = int(season)
        return self._request_json(
            "GET",
            f"{DASHEN_CUSTOMER_API_BASE}/queryMatchList",
            params=params,
            dts_override=self.credential.dts,
        )

    def query_match_info(self, customer_token: str, match_id: str) -> Dict[str, Any]:
        return self._request_json(
            "GET",
            f"{DASHEN_CUSTOMER_API_BASE}/queryMatchInfo",
            params={"matchId": str(match_id), "token": str(customer_token)},
            dts_override=self.credential.dts,
        )


    def fight_query_match_info(self, customer_token: str, match_id: str) -> Dict[str, Any]:
        return self._request_json(
            "GET",
            f"{DASHEN_CUSTOMER_API_BASE}/fight/queryMatchInfo",
            params={"matchId": str(match_id), "token": str(customer_token)},
            dts_override=self.credential.dts,
        )

    def fight_query_count(
        self,
        customer_token: str,
        *,
        season: Optional[int] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"token": str(customer_token)}
        if season is not None:
            params["season"] = int(season)
        return self._request_json(
            "GET",
            f"{DASHEN_CUSTOMER_API_BASE}/fight/queryCount",
            params=params,
            dts_override=self.credential.dts,
        )

    def fight_leisure_role_card(
        self,
        customer_token: str,
        *,
        season: Optional[int] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"token": str(customer_token)}
        if season is not None:
            params["season"] = int(season)
        return self._request_json(
            "GET",
            f"{DASHEN_CUSTOMER_API_BASE}/fight/getLeisureFightRoleCard",
            params=params,
            dts_override=self.credential.dts,
        )

    def fight_query_match_list(
        self,
        customer_token: str,
        game_mode: str = "SportFight",
        *,
        page: int = 1,
        season: Optional[int] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "token": str(customer_token),
            "gameMode": str(game_mode),
            "page": max(1, int(page)),
        }
        if season is not None:
            params["season"] = int(season)
        return self._request_json(
            "GET",
            f"{DASHEN_CUSTOMER_API_BASE}/fight/queryMatchList",
            params=params,
            dts_override=self.credential.dts,
        )


    def query_province_rank(self, province: str, role_type: str) -> Dict[str, Any]:
        c = self.credential
        return self._request_json(
            "GET",
            f"{DASHEN_API_ROOT}/queryProvinceRank",
            params={
                "server": c.server,
                "dts": c.dts,
                "roleId": c.role_id,
                "token": c.token,
                "roleType": str(role_type),
                "province": str(province),
            },
            dts_override=c.dts,
        )

    def get_hero_billboard(self, province: str, game_mode: str, hero_guid: str) -> Dict[str, Any]:
        c = self.credential
        return self._request_json(
            "GET",
            f"{DASHEN_BILLBOARD_API_BASE}/getHeroBillboard",
            params={
                "server": c.server,
                "dts": c.dts,
                "roleId": c.role_id,
                "token": c.token,
                "gameMode": str(game_mode),
                "heroGuid": str(hero_guid),
                "province": str(province),
            },
            dts_override=c.dts,
        )

    def get_billboard_user(self, customer_token: str) -> Dict[str, Any]:
        return self._request_json(
            "GET",
            f"{DASHEN_BILLBOARD_API_BASE}/customGetUserHeroBillboard",
            params={"token": str(customer_token)},
            dts_override=self.credential.dts,
        )
