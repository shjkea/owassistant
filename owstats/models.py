# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class DashenCredential:
    role_id: int
    token: str
    dts: int = 2026
    server: int = 1

    def to_dict(self, *, include_token: bool = True) -> Dict[str, Any]:
        data = asdict(self)
        if not include_token:
            token = self.token
            data["token"] = (token[:4] + "…" + token[-4:]) if len(token) > 10 else "***"
        return data


@dataclass(frozen=True)
class ResolvedPlayer:
    query: str
    full_id: str
    bnet_id: str
    customer_token: str
    icon_url: str = ""
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self, *, include_customer_token: bool = True) -> Dict[str, Any]:
        data = {
            "query": self.query,
            "full_id": self.full_id,
            "bnet_id": self.bnet_id,
            "icon_url": self.icon_url,
            "has_customer_token": bool(self.customer_token),
        }
        if include_customer_token:
            data["customer_token"] = self.customer_token
        return data


@dataclass(frozen=True)
class SignRequest:
    purpose: str
    body: str
    context: Dict[str, str]
    needs_signer_version: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QRStart:
    session_id: str
    qr_png: bytes
    expires_in: int
    poll_interval_ms: int = 2000

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "qr_png": self.qr_png,
            "expires_in": self.expires_in,
            "poll_interval_ms": self.poll_interval_ms,
            "state": "waiting_scan",
        }


@dataclass(frozen=True)
class QRState:
    state: str
    expires_in: int
    poll_interval_ms: int = 2000
    roles: List[Dict[str, Any]] = field(default_factory=list)
    sign_request: Optional[SignRequest] = None
    credential: Optional[DashenCredential] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "expires_in": self.expires_in,
            "poll_interval_ms": self.poll_interval_ms,
            "roles": list(self.roles),
            "sign_request": self.sign_request.to_dict() if self.sign_request else None,
            "credential": self.credential.to_dict(include_token=False) if self.credential else None,
        }
