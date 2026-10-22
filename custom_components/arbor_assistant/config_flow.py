"""UI setup and reauthentication. Credentials remain in Home Assistant."""

from dataclasses import asdict
from hashlib import sha256

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .client import ArborClient, ArborError, AuthenticationError, normalize_school_url
from .const import CONF_PERSONAL, CONF_SCHOOL_URL, CONF_STUDENTS, DEFAULT_INTERVAL, DOMAIN
from .parsers import discover_students


class ArborConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            client = None
            try:
                data = dict(user_input)
                data[CONF_SCHOOL_URL] = normalize_school_url(data[CONF_SCHOOL_URL])
                data["username"] = data["username"].strip().lower()
                identity = sha256(
                    f"{data[CONF_SCHOOL_URL]}|{data['username']}".encode()
                ).hexdigest()
                await self.async_set_unique_id(identity)
                if self.source == "reauth":
                    self._abort_if_unique_id_mismatch()
                else:
                    self._abort_if_unique_id_configured()
                client = ArborClient(data[CONF_SCHOOL_URL], data["username"], data["password"])
                students = discover_students(await client.page("/guardians/home-ui/dashboard"))
                data[CONF_STUDENTS] = [asdict(student) for student in students]
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except ArborError:
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["base"] = "invalid_url"
            else:
                if self.source == "reauth":
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(), data_updates=data
                    )
                return self.async_create_entry(title="Arbor Assistant", data=data)
            finally:
                if client:
                    await client.close()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCHOOL_URL): str,
                    vol.Required("username"): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
                    ),
                    vol.Required("password"): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data):
        return await self.async_step_user()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ArborOptionsFlow()


class ArborOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "scan_interval",
                        default=self.config_entry.options.get("scan_interval", DEFAULT_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=30, max=360)),
                    vol.Required(
                        CONF_PERSONAL, default=self.config_entry.options.get(CONF_PERSONAL, False)
                    ): bool,
                }
            ),
        )
