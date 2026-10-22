"""Read-only native calendar backed by the parent portal's day feed."""

from datetime import timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .client import ArborError
from .entity import ArborEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ArborCalendar(entry.runtime_data, sid) for sid in entry.runtime_data.data])


def calendar_event(event):
    return CalendarEvent(
        start=event["start"],
        end=event["end"],
        summary=event["summary"],
        location=event["location"],
        uid=event["uid"],
    )


class ArborCalendar(ArborEntity, CalendarEntity):
    _attr_name = "Timetable"

    def __init__(self, coordinator, sid):
        super().__init__(coordinator, sid)
        self._attr_unique_id = f"{self.identity}_timetable"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        @callback
        def refresh_state(_):
            self.async_write_ha_state()

        self.async_on_remove(
            async_track_time_interval(self.hass, refresh_state, timedelta(minutes=1))
        )

    @property
    def event(self):
        now = dt_util.utcnow()
        upcoming = [event for event in self.student.get("calendar", []) if event["end"] > now]
        return calendar_event(upcoming[0]) if upcoming else None

    async def async_get_events(self, hass, start_date, end_date):
        try:
            events = await self.coordinator.calendar_range(self.student_id, start_date, end_date)
            return [calendar_event(event) for event in events]
        except (ArborError, ValueError) as err:
            raise HomeAssistantError("Unable to retrieve Arbor calendar events") from err
