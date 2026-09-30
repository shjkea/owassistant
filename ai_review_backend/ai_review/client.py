from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterable, Iterator, List, Tuple

from .errors import (
    DeepSeekAPIError,
    DeepSeekAuthenticationError,
    DeepSeekNetworkError,
    DeepSeekRateLimitError,
    MissingAPIKeyError,
    ReviewCancelled,
)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise ReviewCancelled("AI 睿评已取消")


def _error_message(body: str, fallback: str) -> str:
    try:
        data = json.loads(body)
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            text = str(error.get("message") or "").strip()
            if text:
                return text
    except (ValueError, TypeError):
        pass
    return fallback


class DeepSeekClient:
    """Minimal stdlib-only client for the official DeepSeek API.

    The endpoint and model are intentionally fixed for v1 of the OW review feature.
    No OpenAI SDK dependency is required.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 120.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self._api_key = str(api_key or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        self.timeout = float(timeout)
        self._opener = opener or urllib.request.urlopen

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    def set_api_key(self, api_key: str) -> None:
        self._api_key = str(api_key or "").strip()

    def _require_key(self) -> str:
        if not self._api_key:
            raise MissingAPIKeyError("未配置 DeepSeek API Key")
        return self._api_key

    def _headers(self, *, stream: bool = False) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._require_key()}",
            "Content-Type": "application/json",
            "User-Agent": "OWAssistant-AIReview/1.0",
        }
        if stream:
            headers["Accept"] = "text/event-stream"
        return headers

    def _open(self, request: urllib.request.Request):
        try:
            return self._opener(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            message = _error_message(body, f"DeepSeek API HTTP {exc.code}")
            if exc.code in (401, 403):
                raise DeepSeekAuthenticationError(message, status_code=exc.code, body=body) from exc
            if exc.code == 429:
                raise DeepSeekRateLimitError(message, status_code=exc.code, body=body) from exc
            raise DeepSeekAPIError(message, status_code=exc.code, body=body) from exc
        except urllib.error.URLError as exc:
            raise DeepSeekNetworkError(f"无法连接 DeepSeek API：{exc.reason}") from exc
        except TimeoutError as exc:
            raise DeepSeekNetworkError("DeepSeek API 请求超时") from exc
        except OSError as exc:
            raise DeepSeekNetworkError(f"DeepSeek API 网络错误：{exc}") from exc

    def test_connection(self) -> Dict[str, Any]:
        request = urllib.request.Request(
            f"{DEEPSEEK_BASE_URL}/models",
            headers=self._headers(),
            method="GET",
        )
        with self._open(request) as response:
            raw = response.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise DeepSeekAPIError("DeepSeek /models 返回了无法解析的数据", body=raw) from exc
        models = data.get("data") if isinstance(data, dict) else None
        if not isinstance(models, list):
            raise DeepSeekAPIError("DeepSeek /models 响应缺少模型列表", body=raw)
        model = next((item for item in models if isinstance(item, dict) and item.get("id") == DEEPSEEK_MODEL), None)
        if model is None:
            raise DeepSeekAPIError(f"官方 API 当前未返回模型 {DEEPSEEK_MODEL}")
        return dict(model)

    @staticmethod
    def _request_body(
        messages: List[Dict[str, str]],
        *,
        reasoning_effort: str,
        max_tokens: int,
        stream: bool,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "thinking": {"type": "enabled"},
            "reasoning_effort": reasoning_effort,
            "max_tokens": int(max_tokens),
            "stream": bool(stream),
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        reasoning_effort: str = "high",
        max_tokens: int = 8192,
    ) -> Tuple[str, Dict[str, Any], str]:
        body = self._request_body(
            messages,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
            stream=False,
        )
        request = urllib.request.Request(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        with self._open(request) as response:
            raw = response.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
            choices = data.get("choices") or []
            choice = choices[0]
            message = choice.get("message") or {}
            text = str(message.get("content") or "")
            usage = dict(data.get("usage") or {})
            finish_reason = str(choice.get("finish_reason") or "")
        except (ValueError, TypeError, IndexError, KeyError) as exc:
            raise DeepSeekAPIError("DeepSeek 返回结构异常", body=raw) from exc
        if not text.strip():
            raise DeepSeekAPIError("DeepSeek 没有返回可展示的点评内容", body=raw)
        return text, usage, finish_reason

    @staticmethod
    def iter_sse_json(lines: Iterable[bytes | str]) -> Iterator[Dict[str, Any]]:
        """Parse OpenAI-style data-only SSE. Public for deterministic unit tests."""
        for raw_line in lines:
            if isinstance(raw_line, bytes):
                line = raw_line.decode("utf-8", errors="replace")
            else:
                line = raw_line
            line = line.strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                return
            try:
                value = json.loads(payload)
            except ValueError:
                continue
            if isinstance(value, dict):
                yield value

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        *,
        reasoning_effort: str = "high",
        max_tokens: int = 8192,
        on_delta: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> Tuple[str, Dict[str, Any], str]:
        token = cancel_token or CancellationToken()
        token.raise_if_cancelled()
        body = self._request_body(
            messages,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
            stream=True,
        )
        request = urllib.request.Request(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(stream=True),
            method="POST",
        )

        current_status = ""

        def status(value: str) -> None:
            nonlocal current_status
            if value != current_status:
                current_status = value
                if on_status:
                    on_status(value)

        status("requesting")
        parts: List[str] = []
        usage: Dict[str, Any] = {}
        finish_reason = ""
        saw_reasoning = False
        saw_content = False

        with self._open(request) as response:
            for chunk in self.iter_sse_json(response):
                token.raise_if_cancelled()
                if isinstance(chunk.get("usage"), dict):
                    usage = dict(chunk["usage"])
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0] if isinstance(choices[0], dict) else {}
                finish = choice.get("finish_reason")
                if finish:
                    finish_reason = str(finish)
                delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                reasoning = delta.get("reasoning_content")
                if reasoning:
                    saw_reasoning = True
                    if not saw_content:
                        status("thinking")
                    # 思维链不向前端暴露。
                content = delta.get("content")
                if content:
                    saw_content = True
                    status("answering")
                    text = str(content)
                    parts.append(text)
                    if on_delta:
                        on_delta(text)

        token.raise_if_cancelled()
        text = "".join(parts)
        if not text.strip():
            hint = "（模型返回了思考内容但没有最终正文）" if saw_reasoning else ""
            raise DeepSeekAPIError("DeepSeek 没有返回可展示的点评内容" + hint)
        status("done")
        return text, usage, finish_reason
