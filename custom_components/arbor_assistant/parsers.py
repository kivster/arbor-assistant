"""Parse observed portal formats without executing HTML or storing UI actions."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from html.parser import HTMLParser
from typing import Any
from zoneinfo import ZoneInfo

from .client import PortalFormatError, allowed_read_path


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("div", "br", "p", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        if tag in ("div", "p", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def plain(value: Any) -> str:
    parser = TextExtractor()
    parser.feed(str(value) if value is not None else "")
    return "\n".join(
        " ".join(line.split()) for line in "".join(parser.parts).splitlines() if line.strip()
    )


def walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for child in node.values():
            yield from walk(child)
    elif isinstance(node, list):
        for child in node:
            yield from walk(child)


def field(item: dict, key: str) -> Any:
    return item.get("fields", {}).get(key, {}).get("value")


@dataclass(frozen=True)
class Student:
    id: str
    name: str


def discover_students(page: dict) -> list[Student]:
    found: dict[str, Student] = {}
    for node in walk(page.get("content")):
        if node.get("xtype") != "mis-info-panel":
            continue
        props = node.get("props", {})
        match = re.fullmatch(r"/guardians/student-ui/overview/id/(\d+)/?", props.get("url", ""))
        if match:
            found[match[1]] = Student(match[1], plain(props.get("title")))
    if not found:
        raise PortalFormatError("No supported student profile found on the dashboard")
    return list(found.values())


def discover_links(page: dict) -> dict[str, str]:
    """Navigation only: never follow profile edit URLs or arbitrary component actions."""
    links = {}
    for node in walk([page.get("subNav"), page.get("content")]):
        path = field(node, "url") or node.get("url")
        name = field(node, "text") or node.get("text")
        if isinstance(path, str) and name and allowed_read_path(path):
            links[plain(name)] = path
    return links


@dataclass(frozen=True)
class Metric:
    key: str
    name: str
    value: float
    unit: str | None = None


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


class KpiHTML(HTMLParser):
    """Extract the main value and bar/label text from Arbor's KPI markup."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str | None] = []
        self.main = ""
        self.bars: list[str] = []
        self.labels: list[str] = []

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        kind = None
        if "mis-htmlpanel-measure-value" in classes:
            kind = "main"
        elif "mis-htmlpanel-measure-barchart-chart-bar-value" in classes:
            kind = "bar"
            self.bars.append("")
        elif "mis-htmlpanel-measure-barchart-chart-label" in classes:
            kind = "label"
            self.labels.append("")
        if tag not in ("br", "img", "input", "hr", "meta", "link"):
            self.stack.append(kind or (self.stack[-1] if self.stack else None))

    def handle_endtag(self, tag):
        if self.stack and tag not in ("br", "img", "input", "hr", "meta", "link"):
            self.stack.pop()

    def handle_data(self, data):
        kind = self.stack[-1] if self.stack else None
        if kind == "main":
            self.main += data
        elif kind == "bar":
            self.bars[-1] += data
        elif kind == "label":
            self.labels[-1] += data


def number(text: str) -> float:
    match = re.fullmatch(r"\s*([+-]?[\d,]+(?:\.\d+)?)\s*%?\s*", text)
    if not match:
        raise PortalFormatError("Arbor KPI value format changed")
    return float(match[1].replace(",", ""))


def parse_kpis(data: dict) -> dict[str, Metric]:
    metrics = {}
    for item in data.get("items", []):
        title, html = field(item, "title"), field(item, "html")
        if not isinstance(title, str) or not isinstance(html, str):
            raise PortalFormatError("Arbor KPI fields changed")
        parser = KpiHTML()
        parser.feed(html)
        attendance = title.startswith("Attendance (")
        name = "Attendance this year" if attendance else title.replace(" - ", " ")
        key = slug(name)
        unit = "%" if attendance else "incidents" if "Incidents" in title else "points"
        metrics[key] = Metric(key, name, number(parser.main), unit)
        for index, label in enumerate(parser.labels):
            label = plain(label)
            if ":" in label:
                period, value = label.split(":", 1)
                base = title.split(" - ")[0]
                value = re.sub(r"\s+(points|incidents)$", "", value.strip())
            elif attendance and index < len(parser.bars):
                period, value, base = label, parser.bars[index], "Attendance"
            else:
                continue
            period = "this year" if period.lower() == "year" else period.lower()
            name = f"{base} {period}"
            key = slug(name)
            metrics[key] = Metric(key, name, number(value), unit)
    if not metrics:
        raise PortalFormatError("Arbor returned no readable KPIs")
    return metrics


def attendance_metrics(page: dict) -> dict[str, Metric]:
    result = {}
    for section in walk(page.get("content")):
        if section.get("xtype") != "mis-section":
            continue
        title = plain(section.get("props", {}).get("title"))
        period = "year" if title.startswith("Statistics for Academic Year") else "recent"
        if period == "recent" and not title.startswith("Recent Attendance"):
            continue
        for node in section.get("content", []):
            props = node.get("props", {})
            label, value = plain(props.get("fieldLabel")), plain(props.get("value"))
            match = re.fullmatch(r"(\d+) sessions \(([\d.]+)%\)", value)
            key = f"attendance_{period}_{slug(label)}_sessions"
            if match:
                result[key] = Metric(key, f"{label} sessions {period}", float(match[1]), "sessions")
            elif label == "Possible sessions" and value.isdigit():
                result[key] = Metric(key, f"Possible sessions {period}", float(value), "sessions")
            else:
                raise PortalFormatError("Arbor attendance summary format changed")
    return result


def normalise_page(page: dict, *, include_personal: bool = False) -> dict:
    """Return display data only, excluding scripts, photos, hidden forms and actions.

    Retains repeated rows and section hierarchy. Missing and empty are distinct.
    Dynamic stores are reported as unresolved, never silently claimed complete.
    """

    def parse(node):
        if not isinstance(node, dict):
            return None
        kind, props = node.get("xtype", ""), node.get("props", {})
        if kind == "mis-info-panel":
            return {
                "kind": "student",
                "properties": [
                    {"label": plain(p.get("label")), "value": plain(p.get("value"))}
                    for p in props.get("properties", [])
                ],
            }
        if kind == "mis-property-row":
            return {
                "kind": "record",
                "label": plain(props.get("fieldLabel")),
                "value": plain(props.get("value")),
                "description": plain(props.get("description")),
            }
        if kind in ("mis-section", "mis-subsection"):
            title = plain(props.get("title"))
            private = title in (
                "Student Details",
                "Student Contact Details",
                "Family, Guardians & Contacts",
                "Medical",
                "Consents",
                "Meal Preferences & Dietary Requirements",
                "Previous Schools",
            )
            if private and not include_personal:
                return {"kind": "section", "title": title, "status": "disabled"}
            children = [result for child in node.get("content") or [] if (result := parse(child))]
            return {
                "kind": "section",
                "title": title,
                "records": children,
                "empty_text": plain(props.get("emptyText")) if not children else "",
                "hidden_rows": props.get("hiddenRowsCount", 0),
                "tree": normalise_tree(props.get("treeData", {})),
            }
        if kind in ("container", "mis-layoutcolumn", "mis-event-box"):
            return {
                "kind": "group",
                "records": [
                    result for child in node.get("content") or [] if (result := parse(child))
                ],
            }
        if kind == "mis-event-box-section":
            return {
                "kind": "event_summary",
                "label": plain(props.get("title")),
                "value": plain(node.get("content")),
            }
        return None

    return {
        "records": [result for child in page.get("content", []) if (result := parse(child))],
        "unresolved_stores": len(page.get("stores", [])),
    }


def normalise_tree(node: Any) -> Any:
    if isinstance(node, list):
        return [normalise_tree(child) for child in node]
    if not isinstance(node, dict):
        return None
    result = {}
    if isinstance(node.get("fields"), dict):
        result["fields"] = {
            key: plain(value.get("value"))
            for key, value in node["fields"].items()
            if key not in ("url", "id", "selected", "disabled") and isinstance(value, dict)
        }
    for key in ("items", "children"):
        if key in node:
            result[key] = normalise_tree(node[key])
    return result


def parse_calendar(data: dict, timezone: str = "Europe/London") -> list[dict]:
    events = []
    for item in data.get("items", []):
        try:
            start = datetime.fromisoformat(field(item, "start_datetime")).replace(
                tzinfo=ZoneInfo(timezone)
            )
            end = datetime.fromisoformat(field(item, "end_datetime")).replace(
                tzinfo=ZoneInfo(timezone)
            )
            title = plain(field(item, "title"))
            if end <= start or not title:
                raise ValueError
        except (TypeError, ValueError):
            raise PortalFormatError("Arbor calendar event format changed") from None
        identity = str(field(item, "url") or (start.isoformat() + title))
        events.append(
            {
                "uid": sha256(identity.encode()).hexdigest()[:24],
                "summary": title,
                "start": start,
                "end": end,
                "location": plain(field(item, "location")),
            }
        )
    return sorted(events, key=lambda event: event["start"])
