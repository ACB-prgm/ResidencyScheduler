# Streamlit, Neon Postgres, and Google Calendar Development Plan

## Summary

Move the app from a local-first SQLite workflow to a Streamlit Community Cloud deployment backed by Neon Postgres. Collapse scheduling to one schedule per year-month, store deployment secrets in `st.secrets`, and add a later Google Calendar publishing flow where users authenticate, pick a calendar, and replace that month’s previously published events.

## Phase 1: Neon Postgres and Single Monthly Schedule

- Add `secrets/` and `.streamlit/secrets.toml` to `.gitignore`; never commit `secrets/neonpostgres.txt` or a real Streamlit secrets file.
- Add a safe `.streamlit/secrets.toml.example` showing the expected Neon key shape.
- Read the primary database URL in this order:
  - `RESIDENCY_SCHEDULER_DATABASE_URL`, `DATABASE_URL`, or `NEON_DATABASE_URL`
  - `st.secrets["connections"]["neon"]["url"]`
  - fallback local SQLite file for development/tests
- For local development, copy values from ignored local secret files into `.streamlit/secrets.toml`; the app should not read those raw files at runtime.
- Use a SQLAlchemy-backed connection adapter so the repository writes to Neon Postgres when configured while tests can still isolate local data.
- Use local SQLite as a read-through cache only; it must not become the source of truth when Neon is configured.
- Change `schedule_periods` to one row per `year, month` with `UNIQUE(year, month)`.
- Remove draft-facing UI and repository behavior. The user selects only a year-month, and the app creates or reuses that month’s single schedule period.
- If migrating old multi-draft data, keep one row per year-month: latest draft with assignments, otherwise highest-id draft. Drop child rows for discarded drafts.

## Phase 2: Streamlit Community Cloud Deployment

- Commit only code and safe examples.
- Configure Streamlit Cloud secrets with the Neon connection URL:

```toml
[connections.neon]
url = "postgresql+psycopg://USER:PASSWORD@HOST/DBNAME?sslmode=require"
```

- Initialize the database automatically during app startup with idempotent schema creation and calendar-month seeding.
- Keep deployment state in Neon, not the local `data/` folder. Local SQLite cache files are performance artifacts and can be regenerated from Neon.

## Phase 3: Google Calendar Publishing

- Add Google Calendar dependencies only when implementing this phase.
- Store Google OAuth client ID, client secret, redirect URI, and token encryption key in `st.secrets`.
- Let each user connect their Google account from Generate Schedule.
- Request the minimum practical scopes:
  - `openid`
  - `email`
  - `profile`
  - `https://www.googleapis.com/auth/calendar.calendarlist.readonly`
  - `https://www.googleapis.com/auth/calendar.events`
- Store encrypted refresh/access tokens in Neon, keyed by the Google account subject/email.
- On Generate Schedule, list writable calendars and let the user choose the calendar for the selected year-month.
- Save the selected calendar on the schedule period.
- Publish by wipe-and-reload:
  - Find existing events with private extended properties identifying this app and year-month.
  - Delete those events.
  - Insert one event per assignment.
  - Store the new Google event IDs on assignments.

Use Google Calendar `extendedProperties.private` for event ownership metadata:

```json
{
  "app": "residency_scheduler",
  "year_month": "YYYY-MM",
  "schedule_period_id": "123"
}
```

## Validation Checklist

- `python -m compileall app.py pages residency_scheduler scripts tests`
- `python -m pytest -q`
- Streamlit smoke test on Schedule Month, Residents, Availability and Preferences, Scheduling Rules, and Generate Schedule.
- Confirm no real secrets are tracked by git.
- Confirm Streamlit Cloud has `connections.neon.url` configured before deployment.

## Assumptions

- Neon Postgres is the production database for Streamlit deployment.
- The local ignored Neon files are development convenience inputs for the primary database URL.
- The local SQLite cache is disposable and never authoritative when Neon is configured.
- There is exactly one editable schedule per year-month.
- Google Calendar API publishing replaces manual ICS export in a later phase, but ICS can remain until that phase is implemented.
