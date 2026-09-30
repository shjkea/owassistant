# -*- coding: utf-8 -*-
"""AI 锐评前端桥接层：只负责接线，不实现任何 API / Prompt / 数据整理逻辑。

- 导入 ``ai_review_backend/ai_review``（不移动后端目录，导入失败时补 sys.path，
  与 ``ui/pages/bnet_accounts.py`` 接入 Battle.net 后端的方式一致）；
- 应用级只创建一个 ``AIReviewService``；
- DeepSeek API Key 的本地持久化（复用大神凭证同一配置目录与原子写入方式）；
- 把后端异常转换成可直接展示的中文提示，并保证 API Key 不会出现在任何文案里；
- 流式锐评 Worker：QThreadPool + QRunnable，回调通过 Qt 信号回到 GUI 线程。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from owstats.credential_store import default_config_dir

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "ai_review_backend"


def _import_backend():
    try:
        import ai_review  # type: ignore
    except ImportError:
        if _BACKEND_DIR.is_dir() and str(_BACKEND_DIR) not in sys.path:
            sys.path.insert(0, str(_BACKEND_DIR))
        import ai_review  # type: ignore
    return ai_review


try:
    AR: Any = _import_backend()
    IMPORT_ERROR = ""
except Exception as exc:  # 后端缺失时 UI 仍可启动，只是 AI 锐评不可用。
    AR = None
    IMPORT_ERROR = f"AI 锐评后端不可用：{type(exc).__name__}"

# 界面上展示的模型名。实际模型由后端固定（DEEPSEEK_MODEL），前端不做选择。
MODEL_DISPLAY_NAME = "DeepSeek V4.1 Flash"


# --------------------------------------------------------------------------- API Key 存储
class APIKeyStore:
    """DeepSeek API Key 本地存储。

    与 ``owstats.credential_store.CredentialStore``（大神 token）相同的机制：
    写在用户配置目录（Windows：%LOCALAPPDATA%\\OWDriveSplit\\），不进项目目录 /
    config.json，原子替换写入并尽量收紧文件权限。明文 JSON，未额外加密。
    """

    FILE_NAME = "deepseek_api_key.json"

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_config_dir() / self.FILE_NAME

    @staticmethod
    def normalize(value: Any) -> str:
        key = str(value or "").strip()
        if not key:
            raise ValueError("API Key 不能为空")
        if len(key) > 512 or any(ord(c) < 33 or ord(c) == 127 for c in key):
            raise ValueError("API Key 格式无效")
        return key

    def load(self) -> str:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return ""
        if not isinstance(data, dict):
            return ""
        try:
            return self.normalize(data.get("api_key"))
        except ValueError:
            return ""

    def has_key(self) -> bool:
        return bool(self.load())

    def save(self, api_key: str) -> Path:
        key = self.normalize(api_key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "provider": "deepseek", "api_key": key, "updated_at": int(time.time())}
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


_key_store: Optional[APIKeyStore] = None
_service: Any = None


def key_store() -> APIKeyStore:
    global _key_store
    if _key_store is None:
        _key_store = APIKeyStore()
    return _key_store


def set_key_store(store: Optional[APIKeyStore]) -> None:
    """测试用：替换存储位置。"""
    global _key_store
    _key_store = store


def ai_service() -> Any:
    """应用级唯一的 AIReviewService。后端不可用时返回 None。"""
    global _service
    if _service is None and AR is not None:
        _service = AR.AIReviewService(api_key=key_store().load() or None)
    return _service


def set_ai_service(service: Any) -> None:
    """测试用：注入假服务。"""
    global _service
    _service = service


def save_api_key(api_key: str) -> Path:
    """保存并立即应用到运行中的服务。"""
    store = key_store()
    path = store.save(api_key)
    service = ai_service()
    if service is not None:
        service.set_api_key(store.load())
    return path


def clear_api_key() -> None:
    key_store().clear()
    service = ai_service()
    if service is not None:
        service.set_api_key("")


def service_has_api_key(service: Any) -> bool:
    client = getattr(service, "client", None)
    value = getattr(client, "has_api_key", None)
    return True if value is None else bool(value)


# --------------------------------------------------------------------------- 错误文案
_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")


def scrub(text: Any, secrets: Iterable[str] = ()) -> str:
    """去掉文本里可能出现的 API Key。"""
    out = str(text or "")
    for secret in secrets:
        secret = str(secret or "")
        if len(secret) >= 6:
            out = out.replace(secret, "***")
    return _KEY_PATTERN.sub("sk-***", out)


def friendly_error(exc: BaseException, secrets: Iterable[str] = ()) -> str:
    """后端异常 → 界面提示。不含 traceback，不含 API Key。"""
    secrets = list(secrets) + [key_store().load()]
    raw = scrub(str(exc).strip(), secrets)
    if AR is None:
        return raw or type(exc).__name__
    status = getattr(exc, "status_code", None)
    if isinstance(exc, AR.MissingAPIKeyError):
        return "未配置 DeepSeek API Key，请在「规则设置 → AI 锐评」中填写。"
    if isinstance(exc, AR.DeepSeekAuthenticationError):
        return "DeepSeek API Key 无效或没有权限，请在「规则设置 → AI 锐评」中检查。"
    if isinstance(exc, AR.DeepSeekRateLimitError):
        return "DeepSeek 请求过于频繁，请稍后再试。"
    if isinstance(exc, AR.DeepSeekNetworkError):
        return raw or "无法连接 DeepSeek，请检查网络。"
    if isinstance(exc, AR.DeepSeekAPIError):
        if status == 402:
            return "DeepSeek 账户余额不足。"
        suffix = f"（HTTP {status}）" if status else ""
        return f"DeepSeek 返回错误{suffix}：{raw or '未知错误'}"
    if isinstance(exc, AR.ReviewBusyError):
        return "上一次 AI 锐评仍在进行，请稍候。"
    if isinstance(exc, AR.AIReviewError):
        return raw or "AI 锐评失败。"
    return f"AI 锐评失败：{raw}" if raw else f"AI 锐评失败（{type(exc).__name__}）"


# --------------------------------------------------------------------------- 流式 Worker
class ReviewStreamSignals(QObject):
    # 所有信号都带 run_id，页面据此丢弃已过期任务的回调。
    delta = Signal(int, str)
    status = Signal(int, str)
    done = Signal(int, object)
    failed = Signal(int, str)
    cancelled = Signal(int)
    finished = Signal(int)


class ReviewStreamWorker(QRunnable):
    """在线程池里调用 ``AIReviewService.stream_review``。

    on_delta / on_status 在工作线程触发，经 Qt 信号（跨线程自动 queued）回到 GUI 线程。
    停止只通过 CancellationToken，绝不 terminate 线程。
    """

    def __init__(self, run_id: int, service: Any, context: Any, request: Any, cancel_token: Any) -> None:
        super().__init__()
        self.run_id = int(run_id)
        self.service = service
        self.context = context
        self.request = request
        self.cancel_token = cancel_token
        self.signals = ReviewStreamSignals()
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        rid = self.run_id
        sig = self.signals
        try:
            result = self.service.stream_review(
                self.context,
                self.request,
                on_delta=lambda text: sig.delta.emit(rid, str(text)),
                on_status=lambda phase: sig.status.emit(rid, str(phase)),
                cancel_token=self.cancel_token,
            )
        except Exception as exc:  # noqa: BLE001 - 统一转换成界面文案
            if AR is not None and isinstance(exc, AR.ReviewCancelled):
                sig.cancelled.emit(rid)
            else:
                sig.failed.emit(rid, friendly_error(exc))
        else:
            sig.done.emit(rid, result)
        finally:
            sig.finished.emit(rid)

    def start(self) -> None:
        QThreadPool.globalInstance().start(self)


__all__ = [
    "AR",
    "APIKeyStore",
    "IMPORT_ERROR",
    "MODEL_DISPLAY_NAME",
    "ReviewStreamWorker",
    "ai_service",
    "clear_api_key",
    "friendly_error",
    "key_store",
    "save_api_key",
    "scrub",
    "service_has_api_key",
    "set_ai_service",
    "set_key_store",
]
