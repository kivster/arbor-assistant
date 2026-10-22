"""Native HA contracts: coordinator, entities, failures, calendar and diagnostics."""

from datetime import UTC, datetime
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.arbor_assistant.calendar import ArborCalendar
from custom_components.arbor_assistant.client import ArborError, AuthenticationError
from custom_components.arbor_assistant.coordinator import ArborCoordinator
from custom_components.arbor_assistant.diagnostics import async_get_config_entry_diagnostics
from custom_components.arbor_assistant.parsers import Metric
from custom_components.arbor_assistant.sensor import ArborMetricSensor


def make_entry():
    return ConfigEntry(
        domain="arbor_assistant",
        version=1,
        minor_version=1,
        data={
            "school_url": "https://example-school.uk.arbor.sc",
            "username": "PRIVATE",
            "password": "SECRET",
            "students": [{"id": "101", "name": "Private Student"}],
        },
        options={},
        source="user",
        title="Arbor Assistant",
        unique_id="opaque-id",
        discovery_keys=MappingProxyType({}),
        subentries_data=[],
    )


@pytest.fixture
async def setup(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = make_entry()
    client = AsyncMock()
    coordinator = ArborCoordinator(hass, entry, client)
    entry.runtime_data = coordinator
    yield hass, entry, client, coordinator
    await hass.async_stop()


async def test_metric_unavailable_when_field_disappears(setup):
    _, _, _, coordinator = setup
    metric = Metric("house_points_this_term", "House Points this term", 35, "points")
    coordinator.data = {"101": {"name": "Example Student", "metrics": {metric.key: metric}}}
    sensor = ArborMetricSensor(coordinator, "101", metric)
    assert sensor.native_value == 35
    assert sensor.available
    coordinator.data["101"]["metrics"] = {}
    assert sensor.native_value is None
    assert not sensor.available
    coordinator.last_update_success = False
    assert not sensor.available


async def test_diagnostics_exclude_private_values(setup):
    hass, entry, _, coordinator = setup
    coordinator.data = {
        "101": {"name": "Private Student", "metrics": {"a": "PRIVATE"}, "status": {"profile": "ok"}}
    }
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["student_count"] == 1
    assert "PRIVATE" not in str(result)
    assert "SECRET" not in str(result)
    assert "Private Student" not in str(result)
    assert "101" not in str(result)


async def test_auth_failure_requests_reauthentication(setup):
    _, _, client, coordinator = setup
    client.page.side_effect = AuthenticationError("Expired")
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_optional_section_error_does_not_fabricate_zero(setup):
    _, _, client, coordinator = setup
    profile = {
        "type": "page",
        "content": [],
        "subNav": {
            "fields": {
                "text": {"value": "Summary"},
                "url": {"value": "/guardians/student-ui/recent-attendance/student-id/101"},
            }
        },
    }

    async def fetch(path, **kwargs):
        if "overview" in path:
            return profile
        if "recent-attendance" in path:
            raise ArborError("Unavailable")
        return {"type": "page", "content": []}

    client.page.side_effect = fetch
    with (
        patch(
            "custom_components.arbor_assistant.coordinator.parse_kpis",
            return_value={
                "house_points_this_term": Metric(
                    "house_points_this_term", "House Points", 35, "points"
                )
            },
        ),
        patch.object(coordinator, "calendar_range", return_value=[]),
    ):
        data = await coordinator._async_update_data()
    assert data["101"]["status"]["attendance"] == "error"
    assert "attendance" not in data["101"]["sections"]
    assert list(data["101"]["metrics"]) == ["house_points_this_term"]


async def test_calendar_range_is_exclusive_and_cache_prevents_repeat_reads(setup):
    _, _, client, coordinator = setup
    client.page.return_value = {"success": True, "items": []}
    start = datetime(2026, 11, 1, tzinfo=UTC)
    end = datetime(2026, 11, 3, tzinfo=UTC)
    assert await coordinator.calendar_range("101", start, end) == []
    assert client.page.await_count == 2
    await coordinator.calendar_range("101", start, end)
    assert client.page.await_count == 2
    with pytest.raises(ValueError):
        await coordinator.calendar_range("101", start, datetime(2027, 1, 1, tzinfo=UTC))
    assert client.page.await_count == 2


async def test_calendar_entity_returns_native_events(setup):
    _, _, _, coordinator = setup
    event = {
        "uid": "opaque",
        "summary": "Lesson",
        "location": "Room A",
        "start": datetime(2026, 11, 1, 9, tzinfo=UTC),
        "end": datetime(2026, 11, 1, 10, tzinfo=UTC),
    }
    coordinator.data = {"101": {"name": "Example Student", "calendar": [event]}}
    calendar = ArborCalendar(coordinator, "101")
    with patch(
        "custom_components.arbor_assistant.calendar.dt_util.utcnow",
        return_value=datetime(2026, 11, 1, 8, tzinfo=UTC),
    ):
        assert calendar.event.summary == "Lesson"
        assert calendar.event.uid == "opaque"
