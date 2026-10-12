# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Optional


class OWStatsError(RuntimeError):
    """OW 国服战绩后端统一异常基类。"""


class MissingCredentialError(OWStatsError):
    """本地尚未保存网易大神 role_id / token。"""


class PlayerNotFoundError(OWStatsError):
    """BattleTag 无法解析为网易大神 customerToken。"""


class AuthSessionError(OWStatsError):
    """网易大神扫码会话失败或过期。"""


class UpstreamError(OWStatsError):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        code: Any = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.payload = payload


class RequestBlockedError(UpstreamError):
    """账号查询已停止；业务层不得吞掉或自动重试。"""
