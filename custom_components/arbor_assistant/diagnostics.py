"""Diagnostics deliberately contain no student, school, credential or record values."""


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return {
        "last_update_success": coordinator.last_update_success,
        "student_count": len(coordinator.data or {}),
        "metric_counts": [len(student["metrics"]) for student in (coordinator.data or {}).values()],
        "section_statuses": [student["status"] for student in (coordinator.data or {}).values()],
    }
