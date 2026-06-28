# Residency Scheduler

Local-first MVP for generating monthly medical residency night-shift schedules.

The initial design uses:

- Streamlit for the UI
- SQLite for local persistence
- Google OR-Tools CP-SAT for schedule optimization
- Google Calendar API for publishing approved schedules

## Scheduling scope

Initial assumptions:

- One night shift per calendar day
- Shift time: 6:00 PM to 7:00 AM the next day
- One resident required per night by default
- Vacation / hard unavailable dates must be honored
- Locked preassignments must not be moved
- Preferences are soft constraints
- Workload should be distributed as evenly as possible

## Project structure

```text
app.py
pages/
	1_Residents.py
	2_Availability.py
	3_Locked_Assignments.py
	4_Generate_Schedule.py
	5_Review.py
	6_Publish.py
residency_scheduler/
	db.py
	repository.py
	solver.py
	calendar/google_calendar.py
scripts/
	init_db.py
```

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/init_db.py
streamlit run app.py
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts\init_db.py
streamlit run app.py
```

## Data storage

The app stores local data in:

```text
data/residency_scheduler.sqlite
```

The database is intentionally ignored by git.

## MVP workflow

1. Create/select a schedule period.
2. Maintain resident roster.
3. Enter hard exceptions, vacations, and preferences.
4. Enter locked manual assignments.
5. Generate schedule.
6. Review fairness and preference violations.
7. Manually adjust if needed.
8. Approve and publish to Google Calendar.

## Current status

This repository contains the initial application skeleton and development plan. The solver is intentionally simple but structured so constraint logic can be extended without rewriting the UI.
