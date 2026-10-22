"""Shared, bounded polling for summaries and detailed parent-visible records."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import ArborClient, ArborError, AuthenticationError, RateLimitError
from .const import CONF_PERSONAL, CONF_STUDENTS, DEFAULT_INTERVAL, DOMAIN
from .parsers import (
    attendance_metrics,
    discover_links,
    normalise_page,
    normalise_tree,
    parse_calendar,
    parse_kpis,
    walk,
)

LOGGER = logging.getLogger(__name__)
SECTION_LINKS = {
    "Profile": "profile",
    "Summary": "attendance",
    "By Date": "attendance_by_date",
    "Behaviour": "behaviour",
    "Report Cards": "reports",
    "Examinations": "exams",
    "Clubs": "clubs",
    "Overview": "assignments",
    "Assignments that are due": "assignments_due",
    "Overdue Assignments": "assignments_overdue",
    "Submitted Assignments": "assignments_submitted",
    "Products": "shop",
}


class ArborCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: ArborClient):
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(minutes=entry.options.get("scan_interval", DEFAULT_INTERVAL)),
        )
        self.client = client
        self.entry = entry
        self.timezone = ZoneInfo("Europe/London")
        self._calendar_cache: dict[tuple[str, date], tuple[datetime, list[dict]]] = {}
        self.last_success: datetime | None = None

    async def calendar_day(self, student_id: str, day: date) -> list[dict]:
        now = dt_util.utcnow()
        key = student_id, day
        if key in self._calendar_cache:
            cached_at, events = self._calendar_cache[key]
            if now - cached_at < timedelta(minutes=30):
                return events
        data = await self.client.page(
            f"/guardians/widget-data/get-calendar-data/student-id/{student_id}/date/{day.isoformat()}",
            collection=True,
        )
        events = parse_calendar(data)
        self._calendar_cache[key] = now, events
        if len(self._calendar_cache) > 100:
            del self._calendar_cache[next(iter(self._calendar_cache))]
        return events

    async def calendar_range(self, student_id: str, start: datetime, end: datetime) -> list[dict]:
        if end <= start or end - start > timedelta(days=42):
            raise ValueError("Request a calendar range of at most 42 days")
        day = start.astimezone(self.timezone).date()
        last = (end - timedelta(microseconds=1)).astimezone(self.timezone).date()
        events = {}
        while day <= last:
            for event in await self.calendar_day(student_id, day):
                if event["start"] < end and event["end"] > start:
                    events[event["uid"]] = event
            day += timedelta(days=1)
        return sorted(events.values(), key=lambda event: event["start"])

    async def _async_update_data(self):
        try:
            result = {}
            for student in self.entry.data[CONF_STUDENTS]:
                sid = student["id"]
                dashboard = await self.client.page(f"/guardians/home-ui/dashboard/student-id/{sid}")
                kpis = await self.client.page(f"/guardians/student/kpis/id/{sid}/", collection=True)
                metrics = parse_kpis(kpis)
                # Profile supplies the school-configured navigation, including candidate IDs.
                profile = await self.client.page(f"/guardians/student-ui/overview/id/{sid}")
                links = discover_links(profile)
                sections = {"dashboard": normalise_page(dashboard)}
                status = {"dashboard": "ok"}
                personal = self.entry.options.get(CONF_PERSONAL, False)
                for label, key in SECTION_LINKS.items():
                    path = links.get(label)
                    if path is None:
                        status[key] = "not_exposed"
                        continue
                    try:
                        page = profile if key == "profile" else await self.client.page(path)
                        sections[key] = normalise_page(page, include_personal=personal)
                        status[key] = "partial" if sections[key]["unresolved_stores"] else "ok"
                        if key == "attendance":
                            metrics.update(attendance_metrics(page))
                        if key == "shop" and page.get("stores"):
                            products = await self.client.shop_products(sid)
                            sections[key]["products"] = normalise_tree(products["items"])
                            sections[key]["unresolved_stores"] = 0
                            status[key] = "ok" if products["complete"] else "partial"
                    except (AuthenticationError, RateLimitError):
                        raise
                    except (ArborError, ValueError, TypeError, KeyError):
                        status[key] = "error"
                        sections.pop(key, None)
                message_buttons = [
                    node
                    for node in walk(dashboard.get("content"))
                    if node.get("props", {}).get("pageUrl", "").rstrip("/")
                    == "/guardians/communication-center-ui/school-messages"
                ]
                if message_buttons:
                    try:
                        messages = await self.client.page(
                            "/guardians/communication-center-ui/school-messages/"
                        )
                        sections["messages"] = normalise_page(messages)
                        status["messages"] = "partial" if messages.get("stores") else "ok"
                    except (AuthenticationError, RateLimitError):
                        raise
                    except ArborError:
                        status["messages"] = "error"
                today = datetime.now(self.timezone).date()
                events = await self.calendar_range(
                    sid,
                    datetime.combine(today, time.min, self.timezone),
                    datetime.combine(today + timedelta(days=2), time.min, self.timezone),
                )
                result[sid] = {
                    "name": student["name"],
                    "metrics": metrics,
                    "calendar": events,
                    "sections": sections,
                    "status": status,
                }
            self.last_success = dt_util.utcnow()
            return result
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed("Arbor sign-in is required") from err
        except (ArborError, ValueError, TypeError, KeyError) as err:
            # Do not log response bodies, URLs, student information or credentials.
            raise UpdateFailed("Unable to refresh Arbor data") from err
