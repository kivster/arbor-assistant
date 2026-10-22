# Arbor Assistant

An unofficial, read-only Arbor parent portal integration for Home Assistant.
House points, behaviour, attendance and a native timetable calendar, plus an
admin-only action for detailed parent-visible records. Runs inside Home Assistant;
no separate server, browser, MQTT broker or paid API is required.

**Early development release.** Built against observed UK parent-portal responses.
Arbor's parent portal is undocumented and school settings differ. This is not
an Arbor-approved product and it does not use the school/partner MIS API.

## Data coverage

| Area | Support |
| --- | --- |
| House and behaviour point scales | Term, year and previous-term sensors; additional numeric school-defined scales discovered |
| Positive and negative incidents | Summary sensors and displayed incident narratives in detailed records |
| Attendance | Year and recent percentage KPIs, session counts, and current academic year's displayed AM/PM marks |
| Timetable | Read-only native calendar with lesson/event title, start, end and location; UK time zone |
| Behaviour details | Displayed totals, individual awards, categories, staff, events, comments and detentions |
| Student profile | Form, year, house, tutor and displayed travel information |
| Personal records | Profile, contacts, medical, dietary, previous-school and consent sections, opt-in through options |
| Assignments, reports, exams, clubs | Displayed section records; the first test account has empty sections, so populated layouts remain unverified |
| Messages and notices | Displayed inbox and dashboard sections; no individual messages are opened or marked read |
| School shop | Read-only, paginated product listing; empty listing verified, populated layout remains unverified |

This does **not** promise every Arbor feature. It does not download report PDFs,
photos or attachments, extract exam files, backfill every historical academic year,
or read data the school has not exposed. Meal account/payment history, multi-child
switching, SSO/MFA login, and populated grid layouts
need further validation. The initial dashboard's student profiles are discovered;
a child only reachable through a switcher may not yet be discovered.

Notification-centre polling is deliberately excluded: opening the observed portal's
notification panel cleared its unread badges. This integration must not silently
consume the parent's portal notifications.

A failed section is marked `error`; an unresolved dynamic store is marked `partial`.
Neither is reported as zero. `not_exposed` means the expected navigation link was
absent. A successful section read is not a claim that every nested detail or
attachment has been retrieved. See [coverage and protocol](docs/protocol.md).

## Install

Requires Home Assistant **2026.10 or newer**.

1. In HACS, open **Custom repositories**, add this repository URL, and select
   **Integration**. This is a custom repository, not a listing in the default HACS catalogue.
2. Download Arbor Assistant and restart Home Assistant.
3. Open **Settings → Devices & services → Add integration → Arbor Assistant**.
4. Enter the school-specific origin (for example `https://example-school.uk.arbor.sc`),
   your parent email and password. Complete first-login checks in Arbor beforehand.
5. Under the integration's options, choose the refresh interval (60 minutes by
   default, minimum 30) and whether to include personal details.

The main summary sensors are enabled initially. Extra period and attendance-count
sensors can be enabled on the integration's entity list. A device is created for
each student profile discovered on the initial dashboard.

Manual installation: copy `custom_components/arbor_assistant` into your Home
Assistant `custom_components` folder, restart, then follow the setup steps above.

## Detailed records

`arbor_assistant.get_student_data` returns the last successful snapshot for a
configured student. It accepts `config_entry_id` (integration selector) and
`student_id` (the student's numeric identifier in the Arbor portal URL). It requires
an administrator when called by a user. Home Assistant automations can use its
response data for local dashboards or automations.

The result contains `last_success`, per-section `status`, and `sections` with
plain-text records. The action performs no additional network request. Detailed
records are held in memory rather than in entity attributes, avoiding repeated
copies of educational/medical records in the recorder database. Responses used in
automations may still be stored in traces; avoid posting them in support issues.

## Privacy and behaviour

- Credentials are stored by Home Assistant in its config entry, as with other
  password integrations. Home Assistant configuration and backups must be protected.
- Requests are confined to a validated school HTTPS origin. Redirects are not
  followed; TLS verification remains enabled. Each account has its own cookie jar.
- Only explicitly allowed read routes, authentication and the shop's read-only
  list POST are implemented. No payments, consent changes, edits or message sending.
- Diagnostics contain counts and section status only. No students, credentials,
  school addresses, signed media URLs, record contents or cookies.
- Requests are serialised and paced. Rate limiting pauses requests, and calendar
  day results are cached for 30 minutes. Calendar queries are limited to 42 days.
- Personal details are off by default and are never sensor attributes.
- Nothing is sent to the repository maintainer or an additional cloud service.

## Development

Python 3.14.2+ and `uv`:

```sh
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests use fictional records and a local fake HTTP server. They exercise the real
HTTP client, authentication and cookie exchange, bounded retry, origin validation,
parser failures, period separation, negative/zero values, UK daylight saving,
calendar caching, HA entities and privacy. Passing local tests does not prove a
school's live portal works; validate it before relying on an automation.

Please contribute synthetic fixtures describing additional portal layouts. Never
commit screenshots, HAR files, session cookies, real student records or passwords.

## Acknowledgements

[eddgrant/arbor-leaderboard](https://github.com/eddgrant/arbor-leaderboard) documents
the parent portal's login/session exchange and component-tree page format. Its
protocol observations informed the transport investigation; this integration's
implementation is independently written in Python.

[Arbor parent portal guide](https://support.arbor-education.com/hc/en-us/articles/212097029-A-quick-introduction-to-Arbor-for-guardians-and-parents)
explains parent-visible areas. [School settings](https://support.arbor-education.com/hc/en-us/articles/203813132-Parent-Portal-and-Parent-App-Settings)
control which are available. The [official API](https://support.arbor-education.com/hc/en-us/articles/360009421273-Setting-up-and-managing-third-party-API-integrations-in-Arbor)
is a separate school/partner approval route.
