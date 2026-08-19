# Residency Scheduler

MVP for generating monthly medical residency night-shift schedules.

The app uses:

- Streamlit for the UI
- Neon Postgres for durable persistence, with local SQLite fallback for tests and local read-through caching
- Google OAuth sign-in before scheduler access
- Google OR-Tools CP-SAT for schedule optimization
- FullCalendar schedule review and local ICS export

## Scheduling Scope

- One night shift per calendar day
- Shift time: 6:00 PM to 7:00 AM the next day
- `required_count` residents required per night, defaulting to one
- Each year-month has one editable schedule
- Schedule requests support single dates and date ranges
- Vacation, unavailable, approved absence, medical leave, and assign requests default to hard priority
- Prefer off and prefer work requests default to soft priority
- Vacation ranges automatically add a soft prefer-work request for the Thursday before vacation starts when that date is in the same month
- Generic scheduling rules support weekday counts and adjacent weekday pairs such as Friday+Saturday for a resident
- Raw workload, day-category counts, preferences, back-to-back shifts, and rolling category surplus fairness are optimized where possible

## Local Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/init_db.py
streamlit run app.py
```

Or use the local launcher after activating your environment:

```bash
bash scripts/run_app.sh
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts\init_db.py
streamlit run app.py
```

## Data Storage

For deployment, configure Streamlit secrets with the Neon connection URL:

```toml
[connections.neon]
url = "postgresql+psycopg://USER:PASSWORD@HOST/DBNAME?sslmode=require"
```

For local development, mirror Streamlit Cloud by putting the primary database URL in `.streamlit/secrets.toml` under `connections.neon.url`. The app also accepts `RESIDENCY_SCHEDULER_DATABASE_URL`, `DATABASE_URL`, or `NEON_DATABASE_URL` for scripts and tests. When Neon is configured, it remains the source of truth and local SQLite is used only as a read-through cache to avoid repeated remote loads.

If no Neon/Postgres URL is configured, the app falls back to a local SQLite source database:

```text
data/residency_scheduler.sqlite
```

The local read-through cache is stored separately in:

```text
data/residency_scheduler_cache.sqlite
```

Set `RESIDENCY_SCHEDULER_DB` to use another SQLite file. Tests use this override to avoid touching the local app database.

Database files, local secrets, credentials, tokens, virtual environments, and build artifacts are ignored by git.

## Google Sign-In

The app requires Google sign-in before any scheduler page loads. Streamlit OIDC keeps the user's signed identity for up to 30 days, while Calendar access and refresh tokens are stored encrypted in Neon. Add both local callbacks to the Google OAuth Web application client:

```text
http://localhost:8501/oauth2callback
http://localhost:8501/component/streamlit_oauth.authorize_button
```

For Streamlit deployment, add both production callbacks:

```text
https://huntingtonhealthresidencyscheduler.streamlit.app/oauth2callback
https://huntingtonhealthresidencyscheduler.streamlit.app/component/streamlit_oauth.authorize_button
```

Configure deployment secrets:

```toml
[auth]
redirect_uri = "https://huntingtonhealthresidencyscheduler.streamlit.app/oauth2callback"
cookie_secret = "strong-random-cookie-signing-secret"
client_id = "..."
client_secret = "..."
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"

[google]
client_id = "..."
client_secret = "..."
redirect_uri = "https://huntingtonhealthresidencyscheduler.streamlit.app"
token_encryption_key = "base64-url-safe-fernet-key"
```

For local development, use the same sections with `auth.redirect_uri = "http://localhost:8501/oauth2callback"` and `google.redirect_uri = "http://localhost:8501"`. Authenticated user details are cached in the Streamlit session, and roster authorization is served from an in-memory snapshot rather than querying Neon on every rerun. New browser sessions use Streamlit's OIDC cookie to identify the user, restore the encrypted Calendar token from Neon, refresh it when needed, and request Calendar authorization only when no usable refresh token remains.

Access is limited to Google accounts whose email appears in the Residents table, plus the administrator account `aaronbastian31@gmail.com`. Residents without email addresses cannot sign in.

Add these Google Calendar API scopes to the OAuth consent screen:

```text
https://www.googleapis.com/auth/calendar.calendarlist.readonly
https://www.googleapis.com/auth/calendar.events
```

## MVP Workflow

1. Select a year-month.
2. Maintain resident roster. Existing resident IDs are preserved; removed rows are marked inactive.
3. Enter availability, preferences, vacation ranges, and hard assignments using resident-name dropdowns.
4. Enter special weekday-count, adjacent-pair, and away-rotation rules.
5. Generate schedule with OR-Tools.
6. Review the FullCalendar view, assignments, workload points, day-category distribution, and prefer-off violations.
7. Manually reassign unlocked generated shifts with hard-constraint validation.
8. Download a single call-schedule ICS file or publish directly to Google Calendar.

## Solver Notes

Hard constraints:

- Every night must be covered.
- Hard unavailable/vacation/approved absence/medical leave request ranges are honored.
- Hard assign requests are honored.
- Hard assign requests cannot exceed required coverage for a date.
- Residents cannot exceed configured `max_shifts`.
- Hard weekday-count, adjacent-pair, and away-rotation rules are enforced.

Soft objective weights:

- Total workload is distributed by floor/ceiling fairness first.
- Monday-Thursday, Friday, Saturday, and Sunday counts are balanced independently after raw total shifts.
- Category imbalance multipliers are Monday-Thursday = 1, Friday = 1.5, Saturday = 2, and Sunday = 1.5. Workload Points is informational, not an aggregate solver target.
- Previous 3 calendar months discourage repeating surplus total shifts or surplus shifts in the same day category for the same resident.
- Higher PGY levels are protected from surplus total shifts and surplus shifts within each day category where feasible.
- Equal-cost leftover assignments use fresh random tie-breaking on each generate run.
- Prefer-off violation: 100
- Prefer-work miss: 10
- Back-to-back shift: 40
- Soft weekday-count and adjacent-pair deviation: 60

The solver validates common infeasible inputs before solving and records each run in `schedule_runs`.

## Calendar Export

The Generate Schedule page provides a downloadable call-schedule `.ics` file and a Google Calendar publish workflow.

- ICS exports use stable assignment-based UIDs and calendar names such as `2026-08-call-schedule`.
- Google Calendar publishing wipes prior Residency Scheduler events for the selected month/calendar, then inserts the current assignments.
- Published Google events include private extended properties identifying the app, schedule month, period ID, and assignment ID.

See `.env.example` for optional local configuration.

For user-facing workflow details and rule examples, see [docs/user_guide.md](docs/user_guide.md).

For Streamlit Community Cloud deployment steps, secrets, and Google OAuth settings, see [docs/streamlit_deployment_checklist.md](docs/streamlit_deployment_checklist.md).

For deployment-facing legal and OAuth consent documents, see [docs/privacy_policy.md](docs/privacy_policy.md) and [docs/terms_of_service.md](docs/terms_of_service.md).

## Tests

```bash
python -m pytest -q
```

The test suite covers monthly schedule periods, seeded calendar months, request date ranges, vacation-derived Thursday preferences, hard assign requests, max-shift infeasibility, soft preferences, weekday-count and adjacent-pair rules, calendar summaries, manual edits, and Streamlit page smoke tests.
