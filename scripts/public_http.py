"""Bounded retries for idempotent public GETs; validation is never retried."""
import email.utils
import http.client
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ATTEMPTS = 3
TRANSIENT_HTTP = {429, 500, 502, 503, 504}


def transient(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in TRANSIENT_HTTP
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, (ssl.SSLError, ssl.CertificateError)):
        return False
    return isinstance(reason, (TimeoutError, ConnectionError, http.client.RemoteDisconnected,
                               http.client.IncompleteRead)) or (
        isinstance(reason, socket.gaierror) and reason.errno == socket.EAI_AGAIN)


def delay(exc, attempt):
    fallback = (2, 8)[attempt]
    if not isinstance(exc, urllib.error.HTTPError) or not exc.headers:
        return fallback
    value = exc.headers.get("Retry-After", "")
    try:
        seconds = float(value) if value.isdecimal() else (
            email.utils.parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        return min(60, max(fallback, seconds))
    except (TypeError, ValueError, OverflowError):
        return fallback


def request_bytes(request, *, limit, timeout=30, expected_host=None):
    if request.get_method() != "GET":
        raise ValueError("Retry helper accepts GET only")
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if expected_host is not None:
                    if urllib.parse.urlparse(response.url).hostname != expected_host:
                        raise ValueError("Unexpected cross-host redirect")
                return response.read(limit + 1)
        except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
            if attempt == ATTEMPTS - 1 or not transient(exc):
                raise
            wait = delay(exc, attempt)
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()
            time.sleep(wait)
