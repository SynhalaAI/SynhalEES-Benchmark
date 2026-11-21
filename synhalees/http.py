"""HTTPS helpers that use both platform and certifi trust roots."""

from __future__ import annotations

from functools import lru_cache
import ssl
import urllib.request

import certifi


@lru_cache(maxsize=1)
def _default_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def open_url(request: urllib.request.Request, *, timeout: float):
    """Open a URL with certificate verification and a maintained CA bundle."""
    return urllib.request.urlopen(request, timeout=timeout, context=_default_ssl_context())


__all__ = ["open_url"]
