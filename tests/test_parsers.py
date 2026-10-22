"""Synthetic examples use the formats observed in the parent portal."""

import json

import pytest

from custom_components.arbor_assistant.client import PortalFormatError
from custom_components.arbor_assistant.parsers import (
    attendance_metrics,
    discover_links,
    discover_students,
    normalise_page,
    parse_calendar,
    parse_kpis,
    plain,
)


def row(label, value, **props):
    return {"xtype": "mis-property-row", "props": {"fieldLabel": label, "value": value, **props}}


def section(title, children, **props):
    return {"xtype": "mis-section", "props": {"title": title, **props}, "content": children}


def page(*children):
    return {"type": "page", "content": list(children)}


def kpi(title, value, labels=(), bars=()):
    html = f'<div class="mis-htmlpanel-measure-value">{value}</div>'
    for index, label in enumerate(labels):
        if index < len(bars):
            html += (
                f'<div class="mis-htmlpanel-measure-barchart-chart-bar-value">{bars[index]}</div>'
            )
        html += f'<label class="mis-htmlpanel-measure-barchart-chart-label">{label}</label>'
    return {"fields": {"title": {"value": title}, "html": {"value": html}}}


def test_periods_negative_and_zero_are_not_conflated():
    parsed = parse_kpis(
        {
            "items": [
                kpi(
                    "House Points - this term",
                    "35",
                    ["This year: 90 points", "Last term: 55 points"],
                ),
                kpi(
                    "Behaviour Points - this term",
                    "-2",
                    ["This year: -4 points", "Last term: -2 points"],
                ),
                kpi("Negative Behavioural Incidents - this term", "0"),
                kpi(
                    "Attendance (2026/2027)",
                    "97<span>%</span>",
                    ["Year", "Last 4 weeks"],
                    ["97%", "100%"],
                ),
            ]
        }
    )
    assert parsed["house_points_this_term"].value == 35
    assert parsed["house_points_this_year"].value == 90
    assert parsed["house_points_last_term"].value == 55
    assert parsed["behaviour_points_this_term"].value == -2
    assert parsed["negative_behavioural_incidents_this_term"].value == 0
    assert parsed["attendance_this_year"].value == 97
    assert parsed["attendance_last_4_weeks"].value == 100
    assert "positive_behavioural_incidents_this_term" not in parsed


def test_unknown_numeric_point_scales_are_preserved():
    result = parse_kpis({"items": [kpi("Kindness Points - this term", "1,234")]})
    assert result["kindness_points_this_term"].value == 1234


@pytest.mark.parametrize(
    "data",
    [
        {"items": []},
        {"items": [{"fields": {}}]},
        {"items": [kpi("House Points - this term", "Unavailable")]},
    ],
)
def test_changed_kpis_fail_instead_of_fabricating_zero(data):
    with pytest.raises(PortalFormatError):
        parse_kpis(data)


def test_attendance_session_counts_use_the_right_period():
    parsed = attendance_metrics(
        page(
            section("Recent Attendance (example dates)", [row("Present", "8 sessions (80.00%)")]),
            section(
                "Statistics for Academic Year 2026/2027",
                [
                    row("Possible sessions", "50"),
                    row("Present", "48 sessions (96.00%)"),
                    row("Unauthorised absent", "0 sessions (0.00%)"),
                ],
            ),
        )
    )
    assert parsed["attendance_recent_present_sessions"].value == 8
    assert parsed["attendance_year_present_sessions"].value == 48
    assert parsed["attendance_year_possible_sessions_sessions"].value == 50


def test_student_identity_and_read_links_only():
    data = page(
        {
            "xtype": "mis-info-panel",
            "props": {
                "title": "Example <b>Student</b>",
                "url": "/guardians/student-ui/overview/id/101",
                "picture": "SECRET SIGNED IMAGE URL",
            },
        },
        {"url": "/guardians/student-ui/assignments/student-id/101", "text": "Overview"},
        {"url": "/guardians/student-ui/edit/id/101", "text": "Edit"},
    )
    students = discover_students(data)
    assert students[0].id == "101"
    assert students[0].name == "Example Student"
    assert list(discover_links(data)) == ["Overview"]


def test_private_sections_are_opt_in_and_actions_never_returned():
    data = page(
        section("Medical", [row("Doctor", "PRIVATE EXAMPLE")]),
        section("House Points", [row("Same date", "+1"), row("Same date", "+2")]),
        {"xtype": "hidden", "props": {"value": "SECRET"}},
        {"xtype": "mis-button-form-action", "props": {"url": "DANGEROUS"}},
    )
    public = json.dumps(normalise_page(data))
    assert "PRIVATE EXAMPLE" not in public
    assert "SECRET" not in public
    assert "DANGEROUS" not in public
    assert len(normalise_page(data)["records"][1]["records"]) == 2
    assert "PRIVATE EXAMPLE" in json.dumps(normalise_page(data, include_personal=True))


def test_nonempty_section_does_not_emit_misleading_empty_text():
    data = normalise_page(
        page(section("Positive Incidents", [row("Total", "2")], emptyText="No positive incidents"))
    )
    assert data["records"][0]["empty_text"] == ""


def test_html_content_is_text_only():
    assert (
        plain("<div>Points: <b>+2</b></div><script>secret()</script><div>A &amp; B</div>")
        == "Points: +2\nA & B"
    )


def test_calendar_uk_dst_and_opaque_stable_identity():
    def event(day):
        return {
            "fields": {
                key: {"value": value}
                for key, value in {
                    "start_datetime": f"2026-{day} 09:00:00",
                    "end_datetime": f"2026-{day} 10:00:00",
                    "title": "Example lesson",
                    "location": "Room A",
                    "url": "/private/student-id/101/event/1",
                }.items()
            }
        }

    summer = parse_calendar({"items": [event("10-23")]})[0]
    winter = parse_calendar({"items": [event("10-26")]})[0]
    assert summer["start"].utcoffset().total_seconds() == 3600
    assert winter["start"].utcoffset().total_seconds() == 0
    assert summer["uid"] == winter["uid"]
    assert "private" not in summer["uid"]


def test_calendar_invalid_event_is_not_silently_dropped():
    with pytest.raises(PortalFormatError):
        parse_calendar({"items": [{"fields": {}}]})
