"""Per-student points, incidents, attendance and sync status sensors."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from .entity import ArborEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    added = set()

    @callback
    def discover():
        new = []
        for sid, student in coordinator.data.items():
            if (sid, "sync") not in added:
                added.add((sid, "sync"))
                new.append(ArborSyncSensor(coordinator, sid))
            for key, metric in student["metrics"].items():
                if (sid, key) not in added:
                    added.add((sid, key))
                    new.append(ArborMetricSensor(coordinator, sid, metric))
        if new:
            async_add_entities(new)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class ArborMetricSensor(ArborEntity, SensorEntity):
    def __init__(self, coordinator, sid, metric):
        super().__init__(coordinator, sid)
        self.key = metric.key
        self._attr_unique_id = f"{self.identity}_{metric.key}"
        self._attr_name = metric.name
        self._attr_native_unit_of_measurement = metric.unit
        self._attr_icon = "mdi:school"
        # These values can reset each term/year: they are not total_increasing.
        self._attr_entity_registry_enabled_default = metric.key in {
            "house_points_this_term",
            "house_points_this_year",
            "behaviour_points_this_term",
            "attendance_this_year",
            "attendance_last_4_weeks",
            "positive_behavioural_incidents_this_term",
            "negative_behavioural_incidents_this_term",
        }

    @property
    def available(self):
        return super().available and self.key in self.student.get("metrics", {})

    @property
    def native_value(self):
        metric = self.student.get("metrics", {}).get(self.key)
        return metric.value if metric else None


class ArborSyncSensor(ArborEntity, SensorEntity):
    _attr_name = "Last successful sync"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, sid):
        super().__init__(coordinator, sid)
        self._attr_unique_id = f"{self.identity}_last_sync"

    @property
    def native_value(self):
        return self.coordinator.last_success

    @property
    def extra_state_attributes(self):
        return {"sections": self.student.get("status", {})}
