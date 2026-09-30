"""Host formatting: IPv6 literals must be usable by aiohttp."""
from __future__ import annotations

import pytest

from aionanoleaf2 import Nanoleaf


def _format(host: str) -> str:
    return Nanoleaf._format_host(Nanoleaf.__new__(Nanoleaf), host)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("192.168.1.28", "192.168.1.28"),
        ("  192.168.1.28  ", "192.168.1.28"),
        ("nanoleaf.local", "nanoleaf.local"),
        ("  nanoleaf.local ", "nanoleaf.local"),
        ("", ""),
        ("2001:db8::5", "[2001:db8::5]"),
        ("[2001:db8::5]", "[2001:db8::5]"),
        ("::1", "[::1]"),
    ],
)
def test_format_host(given: str, expected: str) -> None:
    assert _format(given) == expected


def test_zone_id_separator_is_literal() -> None:
    """RFC 6874 asks for "%25", but aiohttp/yarl only resolve a literal "%".

    Encoding it makes every link-local connection fail with
    ClientConnectorError, so the separator has to survive verbatim.
    """
    assert _format("fe80::1%eth0") == "[fe80::1%eth0]"
    assert "%25" not in _format("fe80::1%eth0")


def test_api_url_is_bracketed_for_ipv6() -> None:
    nanoleaf = Nanoleaf.__new__(Nanoleaf)
    nanoleaf._host = _format("fe80::1%eth0")
    nanoleaf._port = 16021
    assert nanoleaf._api_url == "http://[fe80::1%eth0]:16021/api/v1"
