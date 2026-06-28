# Residency Scheduler

Local-first MVP for generating monthly medical residency night-shift schedules.

The app uses:

- Streamlit for the UI
- SQLite for local persistence
- Google OR-Tools CP-SAT for schedule optimization
- A Google Calendar event preview and upsert-ready integration module

## Scheduling Scope

- One night shift per calendar day
- Shift time: 6:00 PM to 7:00 AM the next day
- `required_count` residents required per night, defaulting to one
- Vacation, hard unavailable, approved absence, and medical leave dates are hard constraints
- Locked assignments are hard constraints
- Preferences are soft constraints
- Workload and weekend shifts are balanced where possible
- Back-to-back shifts are avoided where possible

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

By default, the app stores local data in:

```text
data/residency_scheduler.sqlite
```

Set `RESIDENCY_SCHEDULER_DB` to use another SQLite file. Tests use this override to avoid touching the local app database.

Database files, credentials, tokens, virtual environments, and build artifacts are ignored by git.

## MVP Workflow

1. Create/select a schedule period.
2. Maintain resident roster. Existing resident IDs are preserved; removed rows are marked inactive.
3. Enter hard exceptions, vacations, and soft preferences.
4. Enter locked manual assignments.
5. Generate schedule with OR-Tools.
6. Review assignments, workload, weekend distribution, and prefer-off violations.
7. Manually reassign unlocked generated shifts with hard-constraint validation.
8. Preview Google Calendar events.

## Solver Notes

Hard constraints:

- Every night must be covered.
- Hard unavailable/vacation/approved absence/medical leave dates are honored.
- Locked assignments are honored.
- Locked assignments cannot exceed required coverage for a date.
- Residents cannot exceed configured `max_shifts`.

Soft objective weights:

- Total workload deviation: 50
- Weekend workload deviation: 75
- Prefer-off violation: 100
- Prefer-work miss: 10
- Back-to-back shift: 40

The solver validates common infeasible inputs before solving and records each run in `schedule_runs`.

## Google Calendar

The Publish page previews the event payloads. The calendar module also exposes `publish_assignments_to_google_calendar`, which upserts events through an authenticated Google Calendar v3 service:

- Missing `google_event_id`: create a new event and save the returned ID.
- Existing `google_event_id`: update the existing event.

To wire actual publishing, create a Google Cloud OAuth client with Calendar API enabled, keep credentials outside git, authenticate with `google-auth-oauthlib` or another supported Google auth flow, and pass the resulting service to the calendar module.

See `.env.example` for optional local configuration.

## Tests

```bash
python -m pytest -q
```

The test suite covers normal feasible schedules, hard unavailability, locked assignments, validation failures, max-shift infeasibility, soft preferences, and back-to-back avoidance.
