"""Bounded public CDN image fetcher. No credentials, redirects or environment proxies."""
from __future__ import annotations

import io
import ipaddress
import socket
import time
import warnings
from urllib.parse import urlsplit

import httpx

from .errors import OWStatsError

MAX_BYTES = 4 * 1024 * 1024
MAX_PIXELS = 4_000_000
MAX_SIDE = 4096
# Narrow CDN suffixes; never accept arbitrary 163.com user-controlled services.
ALLOWED_HOSTS = ("nos.netease.com", "nosdn.127.net", "bnetcmsus-a.akamaihd.net",
                 "d15f34w2p8l1cc.cloudfront.net", "static.playoverwatch.com",
                 "images.blz-contentstack.com", "g.166.net", "img.166.net")
# 大神 queryCard 返回的玩家头像实际托管在这台图片代理上（queryCard.data["icon"]
# → https://ld5picproxy.ds.163.com/overwatch/<sha256>.png）。只精确放行这一台主机，
# 不做后缀通配，也不放开 *.163.com。
ALLOWED_EXACT_HOSTS = ("ld5picproxy.ds.163.com",)


def _host_allowed(host: str) -> bool:
    if host in ALLOWED_EXACT_HOSTS:
        return True
    return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS)


class ImageError(OWStatsError):
    """Image unavailable or rejected by the safety policy."""


def _target(url: str) -> tuple[str, str]:
    if not isinstance(url, str) or not url or len(url) > 2048 or any(ord(c) < 33 for c in url):
        raise ImageError("图片地址无效")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if (parts.scheme != "https" or parts.port not in (None, 443)
                or parts.username is not None or parts.password is not None
                or parts.fragment or "\\" in url
                or not _host_allowed(host)):
            raise ValueError()
    except ValueError:
        raise ImageError("图片地址不在允许的 HTTPS CDN 范围内") from None
    try:
        addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(
            host, 443, type=socket.SOCK_STREAM)))
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ImageError("图片地址解析到非公共网络")
    except (OSError, ValueError):
        raise ImageError("图片域名解析失败") from None
    ip = addresses[0]
    authority = f"[{ip}]" if ":" in ip else ip
    # Pin the checked IP; keep the original hostname for TLS certificate verification.
    return str(httpx.URL(url).copy_with(host=ip, port=None)), host


def _sanitize(data: bytes) -> bytes:
    try:
        from PIL import Image
    except ImportError:
        raise ImageError("图片功能需要安装 owstats/requirements.txt 中的 Pillow") from None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if (image.format not in {"PNG", "JPEG", "WEBP", "GIF"}
                        or image.width <= 0 or image.height <= 0
                        or max(image.size) > MAX_SIDE or image.width * image.height > MAX_PIXELS):
                    raise ValueError()
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.seek(0)
                clean = image.convert("RGBA")
                clean.info.clear()
                output = io.BytesIO()
                clean.save(output, format="PNG")
                result = output.getvalue()
                if len(result) > MAX_BYTES:
                    raise ValueError()
                return result
    except Exception:
        raise ImageError("图片损坏、格式不支持或超过尺寸限制") from None


def load_image(url: str) -> bytes:
    """Return verified, metadata-free PNG bytes; raise ImageError on any failure."""
    pinned_url, host = _target(url)
    deadline = time.monotonic() + 20
    try:
        with httpx.Client(timeout=httpx.Timeout(5, connect=5), trust_env=False,
                          follow_redirects=False) as client:
            with client.stream("GET", pinned_url,
                               headers={"Host": host, "Accept": "image/png,image/jpeg,image/webp,image/gif",
                                        "Accept-Encoding": "identity"},
                               extensions={"sni_hostname": host}) as response:
                if response.status_code != 200:
                    raise ImageError("图片下载失败或发生重定向")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ImageError("不接受压缩的图片传输")
                length = response.headers.get("content-length")
                if length is not None and (not length.isdecimal() or int(length) > MAX_BYTES):
                    raise ImageError("图片超过大小限制")
                data = bytearray()
                for chunk in response.iter_raw(65536):
                    data.extend(chunk)
                    if len(data) > MAX_BYTES or time.monotonic() > deadline:
                        raise ImageError("图片超过大小或时间限制")
        return _sanitize(bytes(data))
    except httpx.HTTPError:
        raise ImageError("图片网络请求失败") from None
