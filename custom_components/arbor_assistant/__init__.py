"""Arbor Assistant: native read-only parent portal integration."""

from copy import deepcopy

import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .client import ArborClient
from .const import CONF_SCHOOL_URL, DOMAIN, PLATFORMS
from .coordinator import ArborCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass, entry):
    client = ArborClient(
        entry.data[CONF_SCHOOL_URL], entry.data["username"], entry.data["password"]
    )
    coordinator = ArborCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await client.close()
        raise
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    if not hass.services.has_service(DOMAIN, "get_student_data"):

        async def get_student_data(call):
            selected = hass.config_entries.async_get_entry(call.data["config_entry_id"])
            if (
                selected is None
                or selected.domain != DOMAIN
                or not hasattr(selected, "runtime_data")
            ):
                raise HomeAssistantError("Select a loaded Arbor Assistant entry")
            runtime = selected.runtime_data
            if not runtime.last_update_success:
                raise HomeAssistantError("Arbor data is unavailable; refresh or sign in again")
            sid = call.data["student_id"]
            if sid not in runtime.data:
                raise HomeAssistantError("Student is not configured in this entry")
            student = runtime.data[sid]
            return {
                "last_success": runtime.last_success.isoformat(),
                "status": deepcopy(student["status"]),
                "sections": deepcopy(student["sections"]),
            }

        async_register_admin_service(
            hass,
            DOMAIN,
            "get_student_data",
            get_student_data,
            schema=vol.Schema(
                {vol.Required("config_entry_id"): str, vol.Required("student_id"): str}
            ),
            supports_response=SupportsResponse.ONLY,
        )
    return True


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.client.close()
        loaded_others = [
            other
            for other in hass.config_entries.async_entries(DOMAIN)
            if other.entry_id != entry.entry_id and hasattr(other, "runtime_data")
        ]
        if not loaded_others:
            hass.services.async_remove(DOMAIN, "get_student_data")
    return unloaded
