"""Pinned official signer. Only verified public assets are cached, never contexts."""
from __future__ import annotations

import hashlib
import json
import threading
import time

import httpx
from py_mini_racer import MiniRacer

from .auth import QR_USER_AGENT
from .errors import AuthSessionError
from .models import SignRequest

BASE = "https://g.166.net/opd/latest/sig/"
ASSETS = (
    ("bootstrap.js", "23c0ca1fe0ea587312135bce312ed5b1ca7ddf0d7d6d8dbcc3f285f448e820ce"),
    ("046dd0fe4cb367dce119.js", "67f57a88eaa8468054db20a1b26d3191d8d10294620d42af79a2f0f3f0db998a"),
    ("7952eec11d6277f8be47.module.wasm", "ebb0e65f3a08ef46db41ff6f6d23186d06d75e59549b015189cb3eb521fbc397"),
)
MAX_ASSET_BYTES = 4 * 1024 * 1024
ENVIRONMENT = r'''
globalThis.window = globalThis;
globalThis.TextEncoder = class {
    encode(text) {
        const raw = unescape(encodeURIComponent(text));
        return Uint8Array.from(raw, ch => ch.charCodeAt(0));
    }
};
globalThis.TextDecoder = class {
    decode(bytes) {
        if (!bytes) return "";
        let raw = "";
        for (const byte of bytes) raw += String.fromCharCode(byte);
        return decodeURIComponent(escape(raw));
    }
};
globalThis.document = {
    createElement() { return {setAttribute() {}}; },
    head: {appendChild() { throw new Error("Unexpected script load"); }}
};
WebAssembly.instantiateStreaming = undefined;
WebAssembly.compileStreaming = undefined;
'''


def _download(name: str) -> bytes:
    # Fixed URLs, no redirects/proxies/cookies or caller-controlled paths.
    deadline = time.monotonic() + 30
    with httpx.Client(timeout=httpx.Timeout(10, connect=5), follow_redirects=False,
                      trust_env=False) as client:
        with client.stream("GET", BASE + name, headers={"Accept-Encoding": "identity"}) as response:
            if response.status_code != 200:
                raise AuthSessionError("官方签名资源下载失败")
            data = bytearray()
            for chunk in response.iter_bytes(65536):
                data.extend(chunk)
                if len(data) > MAX_ASSET_BYTES or time.monotonic() > deadline:
                    raise AuthSessionError("官方签名资源超过大小或时间限制")
    return bytes(data)


class SignerBridge:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._assets: tuple[bytes, ...] | None = None

    def _load_assets(self) -> tuple[bytes, ...]:
        if self._assets is None:
            assets = tuple(_download(name) for name, _ in ASSETS)
        else:
            assets = self._assets
        for data, (_, digest) in zip(assets, ASSETS):
            if len(data) > MAX_ASSET_BYTES or hashlib.sha256(data).hexdigest() != digest:
                self._assets = None
                raise AuthSessionError("官方签名资源 SHA256 不匹配；拒绝执行")
        self._assets = assets  # Atomic publication; no disk cache to poison/symlink.
        return assets

    def sign(self, request: SignRequest) -> tuple[dict, str]:
        with self._lock:
            if not isinstance(request.body, str) or len(request.body.encode("utf-8")) > 65536:
                raise AuthSessionError("签名请求过大或无效")
            context = {key: request.context[key] for key in ("csrf", "cst", "time_diff") if key in request.context}
            if any(not isinstance(value, str) or len(value) > 8192 for value in context.values()):
                raise AuthSessionError("签名上下文无效")
            try:
                bootstrap, chunk, wasm = self._load_assets()
                # New runtime for EVERY request, including csrf-only web login.
                with MiniRacer() as runtime:
                    runtime.set_hard_memory_limit(128 * 1024 * 1024)
                    def evaluate(source):
                        return runtime.eval(source, timeout_sec=5)
                    evaluate(ENVIRONMENT)
                    evaluate("globalThis.navigator = " + json.dumps({
                        "userAgent": QR_USER_AGENT, "language": "zh-CN",
                        "platform": "Win32", "cookieEnabled": True,
                    }) + ";globalThis.localStorage = " + json.dumps(context) + ";")
                    evaluate("globalThis.signerWasm = new Uint8Array(" + json.dumps(list(wasm)) + ");")
                    evaluate("globalThis.fetch = () => Promise.resolve({arrayBuffer: () => Promise.resolve(signerWasm.buffer)});")
                    evaluate(chunk.decode("utf-8"))
                    evaluate(bootstrap.decode("utf-8"))
                    version = evaluate("sig.default().then(s => {globalThis.officialSigner = s; return s.get_version();})").get(timeout=15)
                    result = json.loads(runtime.call("officialSigner.gen_sign", request.body, timeout_sec=5))
                    result["user_agent"] = QR_USER_AGENT
                    if not isinstance(version, str) or not version or len(version) > 256:
                        raise ValueError("invalid signer version")
                    from .auth import DashenQRAuth
                    return DashenQRAuth._normalize_signature(result), version
            except AuthSessionError:
                raise
            except Exception:
                # Do not expose JS exceptions containing cookies or signed bodies.
                raise AuthSessionError("官方签名执行失败或超时") from None

    def close(self) -> None:
        with self._lock:
            self._assets = None
