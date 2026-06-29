# Residency Scheduler

Local-first MVP for generating monthly medical residency night-shift schedules.

The app uses:

- Streamlit for the UI
- SQLite for local persistence
- Google OR-Tools CP-SAT for schedule optimization
- FullCalendar schedule review and local ICS export

## Scheduling Scope

- One night shift per calendar day
- Shift time: 6:00 PM to 7:00 AM the next day
- `required_count` residents required per night, defaulting to one
- Each month can have multiple named drafts
- Schedule requests support single dates and date ranges
- Vacation, unavailable, approved absence, medical leave, and assign requests default to hard priority
- Prefer off and prefer work requests default to soft priority
- Vacation ranges automatically add a soft prefer-work request for the Thursday before vacation starts when that date is in the same draft month
- Generic special rules support weekday counts and adjacent weekday pairs such as Friday+Saturday for a resident
- Workload, weekend shifts, preferences, back-to-back shifts, and rolling surplus fairness are optimized where possible

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

1. Create/select a year-month and named draft.
2. Maintain resident roster. Existing resident IDs are preserved; removed rows are marked inactive.
3. Enter availability, preferences, vacation ranges, and hard assignments using resident-name dropdowns.
4. Enter special weekday-count and adjacent-pair rules.
5. Generate schedule with OR-Tools.
6. Review the FullCalendar view, assignments, workload, weekend distribution, and prefer-off violations.
7. Manually reassign unlocked generated shifts with hard-constraint validation.
8. Download a PGY-grouped ICS ZIP for manual calendar import.

## Solver Notes

Hard constraints:

- Every night must be covered.
- Hard unavailable/vacation/approved absence/medical leave request ranges are honored.
- Hard assign requests are honored.
- Hard assign requests cannot exceed required coverage for a date.
- Residents cannot exceed configured `max_shifts`.
- Hard weekday-count and adjacent-pair rules are enforced exactly.

Soft objective weights:

- Total workload is distributed by floor/ceiling fairness first.
- Sat/Sun weekend workload is distributed by floor/ceiling fairness first.
- Previous 3 calendar months discourage repeating surplus total or Sat/Sun weekend shifts for the same resident.
- Higher PGY levels are protected from surplus total and weekend shifts where feasible.
- Equal-cost leftover assignments use fresh random tie-breaking on each generate run.
- Prefer-off violation: 100
- Prefer-work miss: 10
- Back-to-back shift: 40
- Soft weekday-count and adjacent-pair deviation: 60

The solver validates common infeasible inputs before solving and records each run in `schedule_runs`.

## Calendar Export

The Generate Schedule page provides a downloadable ZIP of PGY-specific `.ics` files for manual import into Google Calendar or another calendar app.

- ICS exports use stable assignment-based UIDs and calendar names such as `2026-08-PGY3`.
- The file is an import artifact, not a live subscribed calendar feed.
- Re-import behavior is handled by the target calendar application.

See `.env.example` for optional local configuration.

## Tests

```bash
python -m pytest -q
```

The test suite covers named drafts, seeded calendar months, request date ranges, vacation-derived Thursday preferences, hard assign requests, max-shift infeasibility, soft preferences, weekday-count and adjacent-pair rules, calendar summaries, manual edits, and Streamlit page smoke tests.
