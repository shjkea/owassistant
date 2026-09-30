# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .credential_store import CredentialStore
from .errors import AuthSessionError
from .models import DashenCredential, QRStart, QRState, SignRequest

QR_PRODUCT = "godlike_web"
QR_API_ROOT = "https://q.reg.163.com/qrcode"
INFO_API_ROOT = "https://inf.ds.163.com"
QR_TTL_SECONDS = 300
QR_POLL_INTERVAL_MS = 2000
QR_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class _Session:
    session_id: str
    urs_uuid: str
    device_id: str
    client: httpx.Client
    created_at: float
    expires_at: float
    state: str = "waiting_scan"
    confirming: bool = False
    authorized: bool = False
    roles: List[Dict[str, Any]] = None  # type: ignore[assignment]
    login_context: Optional[Dict[str, str]] = None
    signing_context: Optional[Dict[str, str]] = None

    def __post_init__(self) -> None:
        if self.roles is None:
            self.roles = []


class DashenQRAuth:
    """网易大神扫码授权状态机。

    注意：当前大神网页登录的 GL-CheckSum 需要官方网页签名器。
    本类负责二维码、Cookie、role/token 交换；签名值由前端/独立 signer bridge 传入。
    因此仍然完全不需要 localhost Web 服务。
    """

    def __init__(self, credential_store: CredentialStore | None = None) -> None:
        self.store = credential_store or CredentialStore()
        self._sessions: Dict[str, _Session] = {}

    def start(self) -> QRStart:
        self._prune()
        client = httpx.Client(
            timeout=httpx.Timeout(15.0, connect=10.0),
            follow_redirects=True,
            headers={"Accept": "application/json, text/plain, */*", "User-Agent": QR_USER_AGENT},
        )
        try:
            response = client.get(f"{QR_API_ROOT}/getqrcodeid", params={"product": QR_PRODUCT, "usage": 0})
            response.raise_for_status()
            urs_uuid = self._parse_qr_uuid(self._response_json(response))
            image_params = {
                "uuid": urs_uuid,
                "size": 260,
                "format": "png",
                "product": QR_PRODUCT,
                "url": "https://ds.163.com",
                "url2": "https://ds.163.com",
            }
            image_bytes = b""
            for _ in range(3):
                img = client.get(
                    f"{QR_API_ROOT}/getGeneralUrlQrcode",
                    params=image_params,
                    headers={"Accept": "image/png,image/*;q=0.9,*/*;q=0.8"},
                )
                img.raise_for_status()
                image_bytes = img.content
                if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                    break
                time.sleep(0.15)
            if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n") or len(image_bytes) > 256 * 1024:
                raise AuthSessionError("网易二维码服务返回了无效图片")

            now = time.time()
            session_id = secrets.token_urlsafe(32)
            self._sessions[session_id] = _Session(
                session_id=session_id,
                urs_uuid=urs_uuid,
                device_id=str(uuid.uuid4()),
                client=client,
                created_at=now,
                expires_at=now + QR_TTL_SECONDS,
            )
            return QRStart(session_id=session_id, qr_png=image_bytes, expires_in=QR_TTL_SECONDS)
        except Exception:
            client.close()
            raise

    def poll(
        self,
        session_id: str,
        *,
        login_signature: Dict[str, Any] | None = None,
        signer_version: str | None = None,
        role_signature: Dict[str, Any] | None = None,
    ) -> QRState:
        session = self._get_session(session_id)
        if time.time() >= session.expires_at:
            self.cancel(session_id)
            return QRState("expired", 0)

        if session.authorized:
            if session.state == "login_signature_required" and login_signature and signer_version:
                self._web_login(session, signer_version, self._normalize_signature(login_signature))
                session.state = "signing_required"
            elif session.state == "signing_required" and role_signature:
                session.roles = self._fetch_roles(session, self._normalize_signature(role_signature))
                session.state = "roles_ready"
            return self._public_state(session)

        endpoint = "qrcodeauth" if session.confirming else "qrcodeauthstatus"
        params: Dict[str, Any] = {"uuid": session.urs_uuid, "product": QR_PRODUCT}
        if session.confirming:
            params.update({"domains": "", "newQrCode": 1})
        try:
            response = session.client.get(f"{QR_API_ROOT}/{endpoint}", params=params)
            response.raise_for_status()
            payload = self._response_json(response)
        except httpx.HTTPError as exc:
            raise AuthSessionError(f"连接网易二维码服务失败：{exc}") from exc

        ret_code = str(payload.get("retCode") or "")
        if ret_code == "408":
            session.state = "waiting_confirm" if session.confirming else "waiting_scan"
        elif ret_code == "409" or (ret_code == "200" and not payload.get("userName")):
            session.confirming = True
            session.state = "waiting_confirm"
        elif ret_code == "200":
            self._finish_qr_login(session, payload)
        elif ret_code in {"401", "404"}:
            self.cancel(session_id)
            return QRState("expired", 0)
        else:
            self.cancel(session_id)
            raise AuthSessionError(f"网易扫码登录失败（retCode={ret_code or 'unknown'}）")
        return self._public_state(session)

    def complete(
        self,
        session_id: str,
        role_id: str | int,
        signature: Dict[str, Any],
    ) -> DashenCredential:
        session = self._get_session(session_id)
        if time.time() >= session.expires_at:
            self.cancel(session_id)
            raise AuthSessionError("扫码会话已过期")
        if session.state != "roles_ready":
            raise AuthSessionError("扫码授权尚未进入账号选择阶段")
        role_text = str(role_id or "").strip()
        valid = {str(x.get("role_id") or "") for x in session.roles}
        if role_text not in valid:
            raise AuthSessionError("选择的战网账号 ID 无效")
        body = self.report_body(role_text)
        sig = self._normalize_signature(signature)
        response = session.client.post(
            f"{INFO_API_ROOT}/v1/web/game/report/getReportToken",
            content=body.encode("utf-8"),
            headers=self._signed_headers(session, body, sig),
        )
        response.raise_for_status()
        payload = self._response_json(response)
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        token = str(result.get("token") or "").strip()
        result_role_id = str(result.get("roleId") or role_text).strip()
        if int(payload.get("code") or 0) != 200 or not token:
            msg = str(payload.get("errmsg") or payload.get("message") or "").strip()
            raise AuthSessionError(msg or "网易大神 token 转换失败")
        credential = self.store.normalize(result_role_id, token, dts=2026, server=1)
        self.store.save(credential)
        self.cancel(session_id)
        return credential

    def complete_sign_request(self, session_id: str, role_id: str | int) -> SignRequest:
        session = self._get_session(session_id)
        if time.time() >= session.expires_at:
            self.cancel(session_id)
            raise AuthSessionError("扫码会话已过期")
        if str(role_id) not in {str(role.get("role_id")) for role in session.roles}:
            raise AuthSessionError("选择的战网账号 ID 无效")
        if session.state != "roles_ready" or not session.signing_context:
            raise AuthSessionError("扫码会话尚未准备好")
        return SignRequest(
            purpose="report_token",
            body=self.report_body(role_id),
            context=dict(session.signing_context),
        )

    @staticmethod
    def report_body(role_id: Any) -> str:
        return json.dumps(
            {"appKey": "bn", "roleId": str(role_id), "server": "1", "source": 1, "type": "yearly"},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def cancel(self, session_id: str) -> None:
        session = self._sessions.pop(str(session_id or ""), None)
        if session is not None:
            session.client.close()

    def close(self) -> None:
        for sid in list(self._sessions):
            self.cancel(sid)

    # ---------- 内部 ----------
    def _get_session(self, session_id: str) -> _Session:
        sid = str(session_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,96}", sid):
            raise AuthSessionError("扫码会话 ID 无效")
        session = self._sessions.get(sid)
        if session is None:
            raise AuthSessionError("扫码会话已失效，请重新获取二维码")
        return session

    def _prune(self) -> None:
        now = time.time()
        for sid, session in list(self._sessions.items()):
            if session.expires_at <= now:
                self.cancel(sid)
        while len(self._sessions) >= 6:
            oldest = min(self._sessions.values(), key=lambda s: s.created_at)
            self.cancel(oldest.session_id)

    @staticmethod
    def _response_json(response: httpx.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            text = response.text.strip()
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end < start or len(text) > 1024 * 1024:
                raise AuthSessionError("网易服务返回格式无效")
            payload = json.loads(text[start : end + 1])
        if not isinstance(payload, dict):
            raise AuthSessionError("网易服务返回格式无效")
        return payload

    @staticmethod
    def _parse_qr_uuid(payload: Dict[str, Any]) -> str:
        if isinstance(payload.get("content"), str):
            try:
                payload = json.loads(payload["content"])
            except ValueError:
                pass
        value = payload.get("l", {}).get("i") if isinstance(payload, dict) else ""
        text = str(value or "").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{32}", text):
            raise AuthSessionError("网易二维码服务返回了无效会话")
        return text

    @staticmethod
    def _cookie(client: httpx.Client, name: str) -> str:
        for cookie in client.cookies.jar:
            if cookie.name == name:
                return str(cookie.value or "")
        return ""

    def _web_headers(self, session: _Session) -> Dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "GL-ClientType": "61",
            "GL-DeviceId": session.device_id,
            "GL-Uid": self._cookie(session.client, "GOD_UUID"),
            "GL-X-XSRF-TOKEN": self._cookie(session.client, "GL-XSRF-TOKEN"),
            "Origin": "https://ds.163.com",
            "Referer": "https://ds.163.com/",
        }

    def _signed_headers(self, session: _Session, body: str, signature: Dict[str, str]) -> Dict[str, str]:
        headers = self._web_headers(session)
        headers.update(
            {
                "Content-Type": "application/json;charset=UTF-8",
                "GL-CheckSum": signature["sign"],
                "GL-Nonce": signature["timestamp"],
                "User-Agent": signature["user_agent"],
            }
        )
        return headers

    @staticmethod
    def _normalize_signature(value: Any) -> Dict[str, str]:
        if not isinstance(value, dict):
            raise AuthSessionError("大神签名尚未生成")
        sign = str(value.get("sign") or "").strip()
        try:
            timestamp = int(value.get("timestamp"))
        except (TypeError, ValueError):
            timestamp = 0
        ua = str(value.get("user_agent") or "").strip()
        if not 16 <= len(sign) <= 512:
            raise AuthSessionError("大神签名格式无效")
        if abs(int(time.time() * 1000) - timestamp) > 5 * 60 * 1000:
            raise AuthSessionError("大神签名已过期")
        if not 16 <= len(ua) <= 512:
            raise AuthSessionError("大神签名缺少浏览器 User-Agent")
        return {"sign": sign, "timestamp": str(timestamp), "user_agent": ua}

    @staticmethod
    def _auth_url_candidates(payload: Any) -> List[str]:
        keys = {
            "crosssetcookieurl", "crosssetcookieurls", "crosscookieurl", "crosscookieurls",
            "setcookieurl", "setcookieurls", "redirecturl", "redirecturls", "loginurl", "loginurls",
        }
        env_keys = {"content", "data", "result"}
        out: List[str] = []

        def visit(value: Any, depth: int = 0, allow_plain: bool = False) -> None:
            if depth > 6 or len(out) >= 24:
                return
            if isinstance(value, str):
                s = value.strip()
                if not s:
                    return
                if s[:1] in {"[", "{"}:
                    try:
                        visit(json.loads(s), depth + 1, allow_plain)
                        return
                    except ValueError:
                        pass
                out.extend(x for x in re.split(r"\s*,\s*(?=(?:https?:)?//)", s) if x.strip())
                return
            if isinstance(value, list):
                for item in value[:24]:
                    visit(item, depth + 1, allow_plain)
                return
            if not isinstance(value, dict):
                return
            for key, item in value.items():
                nk = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if nk in keys or (allow_plain and nk in {"url", "urls"}):
                    visit(item, depth + 1, True)
            for key, item in value.items():
                nk = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if nk in env_keys:
                    visit(item, depth + 1)

        visit(payload)
        return list(dict.fromkeys(x.strip() for x in out if x.strip()))[:24]

    @staticmethod
    def _cross_cookie_url(raw_url: Any, session: _Session) -> Optional[str]:
        url = str(raw_url or "").strip()
        if not url or len(url) > 2048:
            return None
        if url.startswith("//"):
            url = "https:" + url
        try:
            parts = urlsplit(url)
        except ValueError:
            return None
        host = (parts.hostname or "").lower().rstrip(".")
        allowed = (".163.com", ".126.com", ".yeah.net", ".netease.com", ".166.net")
        if (
            parts.scheme not in {"http", "https"}
            or not host
            or parts.username is not None
            or parts.password is not None
            or not any(host == suf[1:] or host.endswith(suf) for suf in allowed)
        ):
            return None
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in {"uuid", "product", "rtid"}]
        query.extend([("uuid", session.urs_uuid), ("product", QR_PRODUCT)])
        return urlunsplit(("https", parts.netloc, parts.path, urlencode(query), parts.fragment))

    def _apply_cross_cookies(self, session: _Session, payload: Dict[str, Any]) -> None:
        urls = [
            u for u in (self._cross_cookie_url(x, session) for x in self._auth_url_candidates(payload)) if u
        ][:12]
        if not urls:
            raise AuthSessionError("网易登录响应未包含有效授权地址")
        success = 0
        for url in urls:
            try:
                r = session.client.get(url)
                if r.status_code < 500:
                    success += 1
            except httpx.HTTPError:
                pass
        if not success:
            raise AuthSessionError("网易登录授权 Cookie 写入失败")

    def _finish_qr_login(self, session: _Session, payload: Dict[str, Any]) -> None:
        self._apply_cross_cookies(session, payload)
        session.authorized = True
        r = session.client.get(f"{INFO_API_ROOT}/v1/web/base/mine/userInfo", headers=self._web_headers(session))
        r.raise_for_status()
        info = self._response_json(r)
        if int(info.get("code") or 0) == 200:
            cst = self._cookie(session.client, "cst").strip()
            time_diff = self._cookie(session.client, "time_diff").strip()
            if cst:
                session.signing_context = {
                    "csrf": self._cookie(session.client, "GL-XSRF-TOKEN"),
                    "cst": cst,
                    "time_diff": time_diff or "0",
                }
                session.state = "signing_required"
                return
        csrf = self._cookie(session.client, "GL-XSRF-TOKEN").strip()
        if not csrf:
            raise AuthSessionError("网易大神网页登录缺少 XSRF 签名上下文")
        session.login_context = {"csrf": csrf}
        session.state = "login_signature_required"

    def _web_login(self, session: _Session, signer_version: str, signature: Dict[str, str]) -> None:
        version = str(signer_version or "").strip()
        if not version or len(version) > 256:
            raise AuthSessionError("大神签名组件版本无效")
        response = session.client.post(
            f"{INFO_API_ROOT}/v1/web/base/login",
            params={"csv": version},
            content=b"null",
            headers=self._signed_headers(session, "null", signature),
        )
        response.raise_for_status()
        payload = self._response_json(response)
        if int(payload.get("code") or 0) != 200:
            raise AuthSessionError(str(payload.get("errmsg") or payload.get("message") or "网页登录初始化失败"))
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        cst = str(result.get("cst") or "").strip()
        try:
            server_ts = int(result.get("timestamp"))
        except (TypeError, ValueError):
            server_ts = 0
        if not cst or server_ts <= 0:
            raise AuthSessionError("网易大神登录响应缺少签名上下文")
        time_diff = int(time.time() * 1000) - server_ts
        session.client.cookies.set("cst", cst, domain=".ds.163.com", path="/")
        session.client.cookies.set("time_diff", str(time_diff), domain=".ds.163.com", path="/")
        session.signing_context = {
            "csrf": self._cookie(session.client, "GL-XSRF-TOKEN"),
            "cst": cst,
            "time_diff": str(time_diff),
        }
        session.login_context = None

    def _fetch_roles(self, session: _Session, signature: Dict[str, str]) -> List[Dict[str, Any]]:
        body = json.dumps({"appKey": "bn"}, ensure_ascii=False, separators=(",", ":"))
        response = session.client.post(
            f"{INFO_API_ROOT}/v1/web/role-web/list/getRoleBindingListByAppKey",
            content=body.encode("utf-8"),
            headers=self._signed_headers(session, body, signature),
        )
        response.raise_for_status()
        payload = self._response_json(response)
        if int(payload.get("code") or 0) != 200:
            raise AuthSessionError(str(payload.get("errmsg") or payload.get("message") or "读取战网账号 ID 失败"))
        raw_roles = payload.get("result") if isinstance(payload.get("result"), list) else []
        result: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_roles[:30]:
            if not isinstance(raw, dict):
                continue
            role = raw.get("appRoleDto") if isinstance(raw.get("appRoleDto"), dict) else raw
            rid = str(role.get("roleId") or role.get("role_id") or "").strip()
            if not rid.isdigit() or int(rid) <= 0 or rid in seen:
                continue
            seen.add(rid)
            result.append(
                {
                    "role_id": rid,
                    "name": str(role.get("nick") or role.get("name") or role.get("roleName") or "战网账号").strip()[:80],
                    "server": str(role.get("serverName") or role.get("subtitle") or role.get("server") or "").strip()[:80],
                }
            )
        if not result:
            raise AuthSessionError("该网易大神账号尚未绑定战网账号 ID")
        return result

    def _public_state(self, session: _Session) -> QRState:
        expires = max(0, int(session.expires_at - time.time()))
        sign_request = None
        if session.state == "login_signature_required" and session.login_context:
            sign_request = SignRequest(
                purpose="web_login",
                body="null",
                context=dict(session.login_context),
                needs_signer_version=True,
            )
        elif session.state == "signing_required" and session.signing_context:
            sign_request = SignRequest(
                purpose="roles",
                body=json.dumps({"appKey": "bn"}, ensure_ascii=False, separators=(",", ":")),
                context=dict(session.signing_context),
            )
        return QRState(
            state=session.state,
            expires_in=expires,
            poll_interval_ms=QR_POLL_INTERVAL_MS,
            roles=list(session.roles),
            sign_request=sign_request,
        )
