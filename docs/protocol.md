# Protocol and verification boundaries

Observed on a UK parent portal in October 2026. No live personal data is included
in this repository. The code should be treated as a first version, not a stable
public Arbor API client.

## Transport

The login protocol described by the community reference posts an `items` array
containing username/password to `/auth/login?lang=en`. A successful response
contains `logged_in` and `session_id`; a same-origin session exchange establishes
cookies. This flow must be verified during integration setup. Browser sign-in
alone does not validate the Python client's authentication.

Guardian page GETs use `format=javascript` but return JSON component trees, even
with an HTML content type. No JavaScript is evaluated. The statistics and daily
calendar feeds return `items[].fields.<field>.value` JSON.

Only a finite route allowlist is permitted. Navigation links select which known
pages to fetch and supply candidate IDs. Form actions, hidden fields, arbitrary
URLs, photo URLs, and write methods in store definitions are ignored.

## Mapping

- KPI HTML: `.mis-htmlpanel-measure-value`, bar values and period labels. Negative
  points and actual zero remain distinct from missing or invalid data.
- Attendance: property rows inside recent/academic-year sections; displayed
  absence and attendance rows remain available as text records.
- Detailed records: sections/subsections, property rows and displayed tree fields.
  Repeated labels are retained as separate records.
- Calendar: each day's observed widget feed; naive timestamps represent UK local
  time and are converted with `Europe/London`. Native calendar ranges use cached
  day reads. No all-day inference or event-type inference is made.
- Shop: the portal uses POST for its read-only list, with `action_params` carrying
  page/start/limit. Fetch at most 900 records and report partial if capped.

## Known gaps requiring real examples

- Multi-child switchers, multiple school memberships and account changes.
- SSO, MFA, child-DOB challenges and new terms/consents during login.
- Report attachments, exam document bodies, homework files and photos.
- Populated lazy grids and richer message layouts. Unknown stores are reported
  partial, not followed speculatively.
- Meal/payment accounts and notification-centre contents.
- Historical-year backfill. Polling reads the portal's current default year.
- Community-school coverage beyond the first observed portal.

Before claiming broader coverage, add synthetic fixtures and tests from observed
shapes, add only verified read endpoints, then verify the affected section live.
HACS custom-repository installation and acceptance into the default catalogue are
separate milestones; catalogue submission follows successful checks and testing.
