"""Small asynchronous parent-portal transport, independent of Home Assistant.

Only the authentication POST and explicitly requested guardian reads are allowed.
The portal is undocumented; no arbitrary link crawling or JavaScript evaluation.
"""

from __future__ import annotations

import asyncio
import json
import re
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from yarl import URL

MAX_RESPONSE_BYTES = 5_000_000
READ_PATHS = (
    r"/guardians/home-ui/dashboard(?:/student-id/\d+)?",
    r"/guardians/student-ui/(?:overview|calendar)/(?:id|student-id)/\d+",
    r"/guardians/student-ui/(?:recent-attendance|attendance-by-date|report-cards|assignments(?:-due|-overdue|-submitted)?)/student-id/\d+",
    r"/guardians/student-ui/examinations/candidate-id/\d+",
    r"/guardians/behaviour-ui/student-behaviour/(?:id|student-id)/\d+",
    r"/guardians/(?:club|school-shop)-ui/dashboard/student-id/\d+",
    r"/guardians/communication-center-ui/school-messages",
    r"/guardians/student/kpis/id/\d+",
    r"/guardians/student/assignments-kpi/student-id/\d+/academic-year-id/\d+",
    r"/guardians/widget-data/get-calendar-data/student-id/\d+(?:/date/\d{4}-\d{2}-\d{2})?",
)


def allowed_read_path(path: str) -> bool:
    """Only audited read routes, never arbitrary links or edit form actions."""
    return any(re.fullmatch(pattern + r"/?", path) for pattern in READ_PATHS)


class ArborError(Exception):
    """A safe error suitable for presentation without private response data."""


class AuthenticationError(ArborError):
    """Credentials or session rejected."""


class PortalFormatError(ArborError):
    """The portal response no longer matches the supported format."""


class RateLimitError(ArborError):
    """The portal requested that we slow down."""


def normalize_school_url(value: str) -> str:
    """Require a real HTTPS school origin; never send credentials to arbitrary hosts."""
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.uk\.arbor\.sc", parsed.hostname or "")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Enter the school's HTTPS origin ending in .uk.arbor.sc")
    return f"https://{parsed.hostname}"


class ArborClient:
    """Own a separate cookie jar for one school/account; never share HA's jar."""

    def __init__(self, school_url: str, username: str, password: str) -> None:
        self.school_url = normalize_school_url(school_url)
        self._username = username
        self._password = password
        self._session: aiohttp.ClientSession | None = None
        self._logged_in = False
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._retry_after = 0.0

    async def close(self) -> None:
        """Dispose of cookies and network resources."""
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._logged_in = False

    async def _request(
        self, method: str, path: str, *, session_start: bool = False, **kwargs: Any
    ) -> Any:
        if monotonic() < self._retry_after:
            raise RateLimitError("Arbor requested a pause before retrying")
        delay = 0.25 - (monotonic() - self._last_request)
        if delay > 0:
            await asyncio.sleep(delay)
        self._last_request = monotonic()
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "ArborAssistant/0.1", "Accept": "application/json"},
            )
        try:
            async with self._session.request(
                method, self.school_url + path, allow_redirects=False, **kwargs
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError("Arbor rejected access")
                if response.status == 429:
                    retry = response.headers.get("Retry-After", "1800")
                    self._retry_after = monotonic() + (
                        max(60, int(retry)) if retry.isdigit() else 1800
                    )
                    raise RateLimitError("Arbor rate limit reached; try later")
                if session_start and response.status in (200, 302, 303):
                    # Consume Set-Cookie without following any redirect. A subsequent
                    # guardian read proves the session works. Never expose the URL.
                    return None
                if 300 <= response.status < 400:
                    # Never forward credentials or session tokens to a redirect target.
                    raise AuthenticationError("Arbor redirected the request; sign in again")
                if response.status >= 400:
                    raise ArborError(f"Arbor returned HTTP {response.status}")
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise PortalFormatError("Arbor response exceeds the size limit")
                try:
                    return json.loads(body)
                except (ValueError, UnicodeError):
                    raise PortalFormatError("Arbor returned an unsupported response") from None
        except (aiohttp.ClientError, TimeoutError):
            raise ArborError("Unable to communicate with Arbor") from None

    async def _login(self) -> None:
        data = await self._request(
            "POST",
            "/auth/login?lang=en",
            json={"items": [{"username": self._username, "password": self._password}]},
        )
        items = data.get("items") if isinstance(data, dict) else None
        item = items[0] if isinstance(items, list) and items else None
        if not (
            isinstance(data, dict)
            and data.get("success") is True
            and isinstance(item, dict)
            and item.get("logged_in") is True
            and isinstance(item.get("session_id"), str)
            and item["session_id"]
        ):
            raise AuthenticationError("Arbor login was not accepted")
        # The portal exchanges the token for cookies on its own HTTPS origin.
        # Disable redirects and never log this request URL or response contents.
        await self._request(
            "GET", "/", session_start=True, params={"session": item["session_id"], "lang": "en"}
        )
        assert self._session is not None
        if not self._session.cookie_jar.filter_cookies(URL(self.school_url)):
            raise AuthenticationError("Arbor did not establish a session")
        self._logged_in = True

    async def page(self, path: str, *, collection: bool = False) -> dict[str, Any]:
        """Read an explicitly selected guardian page, retrying an expired login once."""
        if not allowed_read_path(path):
            raise ValueError("Only audited read-only Arbor paths are supported")
        return await self._read(
            "GET",
            path,
            collection=collection,
            params={} if collection else {"format": "javascript"},
        )

    async def shop_products(self, student_id: str) -> dict[str, Any]:
        """The observed shop list is a read-only POST, never a purchase action."""
        if not student_id.isascii() or not student_id.isdigit():
            raise ValueError("Invalid student identifier")
        items = []
        for page_number in range(1, 31):
            data = await self._read(
                "POST",
                f"/guardians/school-shop/list/student-id/{student_id}",
                collection=True,
                json={
                    "action_params": {
                        "mis_criteria": {},
                        "page": page_number,
                        "start": (page_number - 1) * 30,
                        "limit": 30,
                    }
                },
            )
            items.extend(data["items"])
            if len(data["items"]) < 30:
                return {"items": items, "complete": True}
        return {"items": items, "complete": False}

    async def _read(self, method: str, path: str, *, collection: bool, **kwargs) -> dict[str, Any]:
        async with self._lock:
            if not self._logged_in:
                await self._login()
            for attempt in range(2):
                try:
                    data = await self._request(method, path, **kwargs)
                    if not isinstance(data, dict):
                        raise PortalFormatError("Arbor did not return a supported object")
                    if collection and (
                        data.get("success") is not True or not isinstance(data.get("items"), list)
                    ):
                        raise PortalFormatError("Arbor did not return a supported collection")
                    if not collection and data.get("type") != "page":
                        raise PortalFormatError("Arbor did not return a guardian page")
                    return data
                except AuthenticationError:
                    self._logged_in = False
                    if attempt:
                        raise
                    await self._login()
        raise ArborError("Unable to read Arbor")
