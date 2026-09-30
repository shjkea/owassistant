from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


@dataclass(frozen=True, order=True)
class AccountKey:
    environment: str
    account_id: str

    @property
    def stable_id(self) -> str:
        return f"{self.environment}:{self.account_id}"


@dataclass(frozen=True)
class AccountInfo:
    key: AccountKey
    battle_tag: str
    internal_name: str
    region: str | None
    is_current: bool
    is_saved: bool
    last_saved_at: int | None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["key"] = asdict(self.key)
        return data


@dataclass(frozen=True)
class ProgressEvent:
    kind: Literal["switch", "login", "recovery", "client"]
    phase: str
    detail: str
    progress: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LoginSession:
    id: str
    target: AccountKey
    created_at: int

    def to_dict(self) -> dict:
        return {"id": self.id, "target": asdict(self.target), "created_at": self.created_at}


@dataclass(frozen=True)
class LoginProbe:
    status: Literal["pending", "wrong_account", "ready"]
    current: AccountKey | None
    message: str

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "current": asdict(self.current) if self.current else None,
            "message": self.message,
        }
