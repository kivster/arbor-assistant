"""Common device identity for Arbor entities."""

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class ArborEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, student_id: str):
        super().__init__(coordinator)
        self.student_id = student_id
        self.identity = f"{coordinator.entry.unique_id}_{student_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.identity)},
            name=coordinator.data[student_id]["name"],
            manufacturer="Arbor Education",
            model="Parent portal",
        )

    @property
    def student(self):
        return self.coordinator.data.get(self.student_id, {})
