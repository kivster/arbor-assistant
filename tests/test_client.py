"""Exercise the actual aiohttp transport against a local fake Arbor server."""

import json
from dataclasses import dataclass, field

import pytest
from aiohttp import web

from custom_components.arbor_assistant.client import (
    ArborClient,
    ArborError,
    AuthenticationError,
    PortalFormatError,
    RateLimitError,
    normalize_school_url,
)

BASE = "https://example-school.uk.arbor.sc"
PAGE = "/guardians/home-ui/dashboard"


@dataclass
class FakePortal:
    url: str = ""
    replies: list = field(default_factory=list)
    requests: list = field(default_factory=list)

    async def handle(self, request):
        self.requests.append(
            (
                request.method,
                request.path,
                dict(request.query),
                await request.text(),
                dict(request.cookies),
            )
        )
        if not self.replies:
            return web.Response(status=500)
        status, body, headers = self.replies.pop(0)
        return web.Response(status=status, text=body, headers=headers)

    def reply(self, payload=None, *, status=200, body=None, headers=None):
        self.replies.append((status, json.dumps(payload) if body is None else body, headers or {}))

    def login(self):
        self.reply({"success": True, "items": [{"logged_in": True, "session_id": "fake-token"}]})
        self.reply(
            status=302,
            headers={
                "Set-Cookie": "fake_session=test; Path=/; HttpOnly",
                "Location": "https://unrelated.example/",
            },
        )

    def client(self):
        client = ArborClient(BASE, "parent@example.test", "fictional-password")
        # Test-only routing to loopback; production constructor permits Arbor HTTPS only.
        client.school_url = self.url
        return client


@pytest.fixture
async def portal():
    fake = FakePortal()
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", fake.handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    fake.url = f"http://localhost:{site._server.sockets[0].getsockname()[1]}"
    yield fake
    await runner.cleanup()


@pytest.mark.parametrize(
    "url",
    [
        "http://example-school.uk.arbor.sc",
        "https://evil.test",
        "https://example-school.uk.arbor.sc.evil.test",
        "https://x.uk.arbor.sc@evil.test",
        "https://user:password@example-school.uk.arbor.sc",
        BASE + "/path",
        BASE + "?session=secret",
        BASE + ":8443",
        "https://127.0.0.1",
    ],
)
def test_rejects_unsafe_origins(url):
    with pytest.raises(ValueError):
        normalize_school_url(url)


def test_normalizes_origin():
    assert normalize_school_url(BASE.upper() + "/") == BASE


@pytest.mark.parametrize("payload", [None, [], {}, {"success": True, "items": []}])
async def test_rejected_login(portal, payload):
    portal.reply(payload)
    client = portal.client()
    try:
        with pytest.raises(AuthenticationError):
            await client.page(PAGE)
    finally:
        await client.close()


@pytest.mark.parametrize(
    "status,error",
    [
        (401, AuthenticationError),
        (403, AuthenticationError),
        (429, RateLimitError),
        (500, ArborError),
        (302, AuthenticationError),
    ],
)
async def test_http_errors_do_not_disclose_response(portal, status, error):
    portal.reply(status=status, body="PRIVATE CHILD DATA")
    client = portal.client()
    try:
        with pytest.raises(error) as result:
            await client.page(PAGE)
        assert "PRIVATE" not in str(result.value)
        assert "fictional-password" not in str(result.value)
        assert len(portal.requests) == 1
    finally:
        await client.close()


async def test_html_is_not_executed_or_exposed(portal):
    portal.reply(body="<html>Private page</html>")
    client = portal.client()
    try:
        with pytest.raises(PortalFormatError):
            await client.page(PAGE)
    finally:
        await client.close()


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.test",
        "//evil.test",
        "/auth/logout",
        "/guardians/../delete",
        "/guardians/test?write=true",
        "/guardians/%2e%2e/delete",
        "/guardians/student-ui/delete/student-id/1",
    ],
)
async def test_invalid_paths_never_make_request(path):
    client = ArborClient(BASE, "parent@example.test", "fictional-password")
    with pytest.raises(ValueError):
        await client.page(path)
    assert client._session is None


async def test_login_cookie_exchange_and_reuse(portal):
    portal.login()
    portal.reply({"type": "page", "content": []})
    portal.reply({"type": "page", "content": []})
    client = portal.client()
    try:
        assert await client.page(PAGE) == {"type": "page", "content": []}
        await client.page(PAGE)
        assert [r[0] for r in portal.requests] == ["POST", "GET", "GET", "GET"]
        assert portal.requests[1][2] == {"session": "fake-token", "lang": "en"}
        assert portal.requests[2][4] == {"fake_session": "test"}
        assert portal.requests[2][2] == {"format": "javascript"}
        assert not portal.replies
    finally:
        await client.close()
    assert client._session is None


async def test_expired_session_reauthenticates_only_once(portal):
    portal.login()
    portal.reply(status=401)
    portal.login()
    portal.reply(status=401)
    client = portal.client()
    try:
        with pytest.raises(AuthenticationError):
            await client.page(PAGE)
        assert sum(r[0] == "POST" for r in portal.requests) == 2
    finally:
        await client.close()


async def test_rate_limit_does_not_reauthenticate(portal):
    portal.login()
    portal.reply(status=429)
    client = portal.client()
    try:
        with pytest.raises(RateLimitError):
            await client.page(PAGE)
        assert len(portal.requests) == 3
    finally:
        await client.close()


async def test_collection_validates_success(portal):
    portal.login()
    portal.reply({"items": [], "success": False})
    client = portal.client()
    try:
        with pytest.raises(PortalFormatError):
            await client.page("/guardians/student/kpis/id/1/", collection=True)
    finally:
        await client.close()
