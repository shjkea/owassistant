"""Independent signer feasibility probe; public assets and synthetic context only."""
from __future__ import annotations

import hashlib
import json
import urllib.request

from py_mini_racer import MiniRacer

BASE = "https://g.166.net/opd/latest/sig/"


def download(name: str) -> bytes:
    with urllib.request.urlopen(BASE + name, timeout=25) as response:
        data = response.read(4 * 1024 * 1024)
    print(name, "bytes=", len(data), "sha256=", hashlib.sha256(data).hexdigest())
    return data


def main() -> None:
    bootstrap = download("bootstrap.js").decode("utf-8")
    chunk = download("046dd0fe4cb367dce119.js").decode("utf-8")
    wasm = download("7952eec11d6277f8be47.module.wasm")
    if not wasm.startswith(b"\x00asm"):
        raise RuntimeError("Invalid WebAssembly response")
    with MiniRacer() as runtime:
        runtime.set_hard_memory_limit(128 * 1024 * 1024)
        runtime.eval(r'''
            globalThis.window = globalThis;
            globalThis.navigator = {
                userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                language: "zh-CN", platform: "Win32", cookieEnabled: true
            };
            globalThis.localStorage = {csrf: "probe-only", cst: "probe-only", time_diff: "0"};
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
        ''')
        runtime.eval("globalThis.probeWasm = new Uint8Array(" + json.dumps(list(wasm)) + ");")
        runtime.eval("globalThis.fetch = () => Promise.resolve({arrayBuffer: () => Promise.resolve(probeWasm.buffer)});")
        # Preloading the official webpack chunk avoids any DOM/network implementation.
        runtime.eval(chunk, timeout=5)
        runtime.eval(bootstrap, timeout=5)
        promise = runtime.eval("sig.default().then(s => {globalThis.probeSigner = s; return s.get_version();})")
        version = promise.get(timeout=15)
        print("SIGNER_VERSION", version)
        for body in ('null', '{"appKey":"bn"}', '{"name":"测试#1234"}'):
            result = runtime.call("probeSigner.gen_sign", body, timeout=5)
            value = json.loads(result)
            print("SIGN_RESULT", {"body": body, "keys": sorted(value), "sign_length": len(value.get("sign", "")), "timestamp_type": type(value.get("timestamp")).__name__})
        print("Runtime probe passed; real authenticated server acceptance NOT tested.")


if __name__ == "__main__":
    main()
