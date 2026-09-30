from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .models import MatchReviewContext, ReviewRequest, ReviewResult
from .prompts import PROMPT_VERSION, build_api_payload


def default_cache_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "OWAssistant" / "ai_reviews"
    return Path.home() / ".cache" / "OWAssistant" / "ai_reviews"


class ReviewCache:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_cache_root()

    def make_key(self, context: MatchReviewContext, request: ReviewRequest, *, model: str) -> str:
        payload, target_ids = build_api_payload(context, request)
        canonical = {
            "prompt_version": PROMPT_VERSION,
            "model": model,
            "style": request.normalized_style().value,
            "scope": request.normalized_scope().value,
            "target_player_ids": target_ids,
            "payload": payload,
        }
        raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def load(self, key: str) -> Optional[ReviewResult]:
        path = self._path(key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        try:
            return ReviewResult(
                text=str(data["text"]),
                model=str(data["model"]),
                style=str(data["style"]),
                scope=str(data["scope"]),
                target_player_ids=[str(x) for x in data.get("target_player_ids", [])],
                usage=dict(data.get("usage") or {}),
                finish_reason=str(data.get("finish_reason") or ""),
                cached=True,
                cache_key=key,
            )
        except (KeyError, TypeError, ValueError):
            return None

    def save(self, key: str, result: ReviewResult) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        data: Dict[str, Any] = asdict(result)
        data["cached"] = False
        data["cache_key"] = key
        data["saved_at"] = datetime.now(timezone.utc).isoformat()
        target = self._path(key)
        fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def clear(self) -> int:
        if not self.root.exists():
            return 0
        count = 0
        for path in self.root.glob("*.json"):
            try:
                path.unlink()
                count += 1
            except OSError:
                continue
        return count
