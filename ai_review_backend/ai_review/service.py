from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any, Callable, Dict, List

from .adapter import build_review_context
from .cache import ReviewCache
from .client import CancellationToken, DEEPSEEK_MODEL, DeepSeekClient
from .errors import ReviewBusyError
from .models import MatchReviewContext, ReviewRequest, ReviewResult
from .prompts import build_messages, max_tokens_for, reasoning_effort_for, resolve_targets


class AIReviewService:
    """Frontend-agnostic OW match review service.

    v1 intentionally supports only the official DeepSeek endpoint and deepseek-flash.
    All methods are synchronous; PySide6 callers should execute network methods in the
    project's existing worker/QThread infrastructure.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        cache: ReviewCache | None = None,
        enable_cache: bool = True,
        timeout: float = 120.0,
        client: DeepSeekClient | None = None,
    ) -> None:
        self.client = client or DeepSeekClient(api_key=api_key, timeout=timeout)
        self.cache = cache or ReviewCache()
        self.enable_cache = bool(enable_cache)
        self._review_lock = threading.Lock()

    @property
    def model(self) -> str:
        return DEEPSEEK_MODEL

    @property
    def busy(self) -> bool:
        locked = self._review_lock.acquire(blocking=False)
        if locked:
            self._review_lock.release()
            return False
        return True

    def set_api_key(self, api_key: str) -> None:
        self.client.set_api_key(api_key)

    def test_connection(self) -> Dict[str, Any]:
        return self.client.test_connection()

    @staticmethod
    def prepare_match(
        detail: Mapping[str, Any],
        *,
        match_meta: Mapping[str, Any] | None = None,
        self_identity: Mapping[str, Any] | str | None = None,
    ) -> MatchReviewContext:
        return build_review_context(detail, match_meta=match_meta, self_identity=self_identity)

    @staticmethod
    def players_for_ui(context: MatchReviewContext) -> List[Dict[str, Any]]:
        return context.ui_players()

    @staticmethod
    def validate_request(context: MatchReviewContext, request: ReviewRequest) -> List[str]:
        _, targets = resolve_targets(context, request)
        return targets

    @staticmethod
    def preview_messages(context: MatchReviewContext, request: ReviewRequest) -> List[Dict[str, str]]:
        messages, _, _ = build_messages(context, request)
        return messages

    def _cache_lookup(self, context: MatchReviewContext, request: ReviewRequest) -> tuple[str, ReviewResult | None]:
        key = self.cache.make_key(context, request, model=self.model)
        if self.enable_cache and not request.force_refresh:
            return key, self.cache.load(key)
        return key, None

    def review(self, context: MatchReviewContext, request: ReviewRequest | None = None) -> ReviewResult:
        request = request or ReviewRequest()
        if not self._review_lock.acquire(blocking=False):
            raise ReviewBusyError("已有 AI 睿评任务正在进行")
        try:
            cache_key, cached = self._cache_lookup(context, request)
            if cached is not None:
                return cached

            messages, _, target_ids = build_messages(context, request)
            text, usage, finish_reason = self.client.chat(
                messages,
                reasoning_effort=reasoning_effort_for(request),
                max_tokens=max_tokens_for(request),
            )
            result = ReviewResult(
                text=text,
                model=self.model,
                style=request.normalized_style().value,
                scope=request.normalized_scope().value,
                target_player_ids=target_ids,
                usage=usage,
                finish_reason=finish_reason,
                cached=False,
                cache_key=cache_key,
            )
            if self.enable_cache:
                self.cache.save(cache_key, result)
            return result
        finally:
            self._review_lock.release()

    def stream_review(
        self,
        context: MatchReviewContext,
        request: ReviewRequest | None = None,
        *,
        on_delta: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> ReviewResult:
        request = request or ReviewRequest()
        if not self._review_lock.acquire(blocking=False):
            raise ReviewBusyError("已有 AI 睿评任务正在进行")
        try:
            cache_key, cached = self._cache_lookup(context, request)
            if cached is not None:
                if on_status:
                    on_status("cache_hit")
                if on_delta and cached.text:
                    on_delta(cached.text)
                if on_status:
                    on_status("done")
                return cached

            messages, _, target_ids = build_messages(context, request)
            text, usage, finish_reason = self.client.stream_chat(
                messages,
                reasoning_effort=reasoning_effort_for(request),
                max_tokens=max_tokens_for(request),
                on_delta=on_delta,
                on_status=on_status,
                cancel_token=cancel_token,
            )
            result = ReviewResult(
                text=text,
                model=self.model,
                style=request.normalized_style().value,
                scope=request.normalized_scope().value,
                target_player_ids=target_ids,
                usage=usage,
                finish_reason=finish_reason,
                cached=False,
                cache_key=cache_key,
            )
            if self.enable_cache:
                self.cache.save(cache_key, result)
            return result
        finally:
            self._review_lock.release()

    def clear_cache(self) -> int:
        return self.cache.clear()
