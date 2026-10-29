"""Restricted outbound HTTP for user-configured providers and integrations."""
from __future__ import annotations

import http.client
import io
import ipaddress
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Iterable


MAX_REDIRECTS = 3


@dataclass(frozen=True)
class ResolvedTarget:
    url: str
    scheme: str
    host: str
    port: int
    request_target: str
    addresses: tuple[str, ...]


class SafeHTTPResponse:
    def __init__(self, response, connection, timeout: float = 60):
        self._response = response
        self._connection = connection
        self._timeout = max(1.0, float(timeout or 60))
        self.status = response.status
        self.reason = response.reason
        self.headers = response.headers
        self._closed = False

    def read(self, amount: int = -1):
        # http.client keeps the socket timeout that was set at connect time. Any
        # global/default short timeout would cut a long provider response early
        # and surface as "The read operation timed out", which looks like a
        # provider failure even though the provider already produced the result.
        # Re-apply the caller's intended timeout before every read attempt.
        try:
            sock = self._connection.sock
            if sock is not None:
                sock.settimeout(self._timeout)
        except Exception:
            pass
        return self._response.read(amount)

    def getheader(self, name: str, default=None):
        return self._response.getheader(name, default)

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._response.close()
        finally:
            self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False


def _is_public_address(raw: str) -> bool:
    try:
        address = ipaddress.ip_address(str(raw).split("%", 1)[0])
    except ValueError:
        return False
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def _resolve_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        rows = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ValueError(f"域名解析失败：{host}") from exc
    addresses = tuple(dict.fromkeys(str(row[4][0]) for row in rows))
    if not addresses:
        raise ValueError(f"域名没有可用地址：{host}")
    blocked = [address for address in addresses if not _is_public_address(address)]
    if blocked:
        raise ValueError(
            f"目标地址指向内网或保留网络，已拒绝：{host} -> {', '.join(blocked)}"
        )
    return addresses


def resolve_remote_url(url: str) -> ResolvedTarget:
    raw = str(url or "").strip()
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("只允许 http:// 或 https:// 出站地址")
    if parsed.username or parsed.password:
        raise ValueError("出站地址不允许包含用户名或密码")
    host = (parsed.hostname or "").strip().rstrip(".").lower()
    if not host:
        raise ValueError("出站地址缺少域名")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("出站地址域名非法") from exc
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port < 1 or port > 65535:
        raise ValueError("出站地址端口非法")
    addresses = _resolve_addresses(host, port)
    request_target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    return ResolvedTarget(
        url=urllib.parse.urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path or "/",
            parsed.query,
            "",
        )),
        scheme=parsed.scheme,
        host=host,
        port=port,
        request_target=request_target,
        addresses=addresses,
    )


def validate_remote_url(url: str) -> str:
    return resolve_remote_url(url).url


def assert_remote_url_allowed(url: str) -> None:
    resolve_remote_url(url)


def _connection_for(target: ResolvedTarget, timeout: float) -> http.client.HTTPConnection:
    last_error: Exception | None = None
    for address in target.addresses:
        try:
            raw_socket = socket.create_connection((address, target.port), timeout=timeout)
        except OSError as exc:
            last_error = exc
            continue
        if target.scheme == "https":
            context = ssl.create_default_context()
            raw_socket = context.wrap_socket(
                raw_socket,
                server_hostname=target.host,
            )
        connection = http.client.HTTPConnection(
            target.host,
            target.port,
            timeout=timeout,
        )
        connection.sock = raw_socket
        return connection
    raise urllib.error.URLError(
        f"无法连接目标主机：{target.host}（{last_error or 'unknown error'}）"
    )


def _request_once(
    request: urllib.request.Request,
    target: ResolvedTarget,
    timeout: float,
):
    connection = _connection_for(target, timeout)
    try:
        connection.request(
            request.get_method(),
            target.request_target,
            body=request.data,
            headers=dict(request.header_items()),
        )
        return connection, connection.getresponse()
    except Exception:
        connection.close()
        raise


def safe_urlopen(
    request: urllib.request.Request | str,
    timeout: float = 60,
    *,
    max_redirects: int = MAX_REDIRECTS,
):
    """Open a validated HTTP request without a second DNS resolution."""
    current = (
        request
        if isinstance(request, urllib.request.Request)
        else urllib.request.Request(str(request))
    )
    method = current.get_method()
    data = current.data
    headers = dict(current.header_items())
    redirects = 0
    while True:
        target = resolve_remote_url(current.full_url)
        active_request = urllib.request.Request(
            target.url,
            data=data,
            method=method,
            headers=headers,
        )
        try:
            connection, response = _request_once(active_request, target, timeout)
        except urllib.error.URLError:
            raise
        except OSError as exc:
            raise urllib.error.URLError(str(exc)) from exc
        location = response.getheader("Location", "")
        if response.status not in {301, 302, 303, 307, 308} or not location:
            if response.status >= 400:
                body = response.read()
                connection.close()
                raise urllib.error.HTTPError(
                    target.url,
                    response.status,
                    response.reason,
                    response.headers,
                    io.BytesIO(body),
                )
            return SafeHTTPResponse(response, connection, timeout=timeout)
        response.read()
        connection.close()
        redirects += 1
        if redirects > max_redirects:
            raise urllib.error.URLError("目标地址重定向次数过多")
        current = urllib.request.Request(
            urllib.parse.urljoin(target.url, location),
            method="GET" if response.status in {301, 302, 303} else method,
            data=None if response.status in {301, 302, 303} else data,
            headers=headers,
        )
        method = current.get_method()
        data = current.data
def public_hosts_for_tests(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(value for value in values if _is_public_address(value))
