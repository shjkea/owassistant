# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from .models import DashenCredential


def default_config_dir() -> Path:
    if os.name == "nt":
        root = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if root:
            return Path(root) / "OWDriveSplit"
    xdg = os.getenv("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "ow_drive_split"
    return Path.home() / ".config" / "ow_drive_split"


class CredentialStore:
    """本地凭证存储。不会把 token 写进项目目录或 config.json。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_config_dir() / "dashen_credentials.json"

    @staticmethod
    def normalize(role_id: Any, token: Any, *, dts: Any = 2026, server: Any = 1) -> DashenCredential:
        role_text = str(role_id or "").strip()
        if not role_text.isdigit() or int(role_text) <= 0 or len(role_text) > 20:
            raise ValueError("role_id 必须是正整数")
        token_text = str(token or "").strip()
        if not token_text or len(token_text) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in token_text):
            raise ValueError("token 格式无效")
        dts_i = int(dts)
        server_i = int(server)
        if dts_i <= 0 or server_i <= 0:
            raise ValueError("dts/server 必须是正整数")
        return DashenCredential(int(role_text), token_text, dts_i, server_i)

    def load(self) -> Optional[DashenCredential]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return self.normalize(
                data.get("role_id"),
                data.get("token"),
                dts=data.get("dts", 2026),
                server=data.get("server", 1),
            )
        except (TypeError, ValueError):
            return None

    def save(self, credential: DashenCredential) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "role_id": credential.role_id,
            "token": credential.token,
            "dts": credential.dts,
            "server": credential.server,
            "updated_at": int(time.time()),
        }
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        return self.path

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
