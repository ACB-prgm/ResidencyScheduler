# Residency Scheduler Development Plan

## Product Goal

Build a local-first scheduling application that generates fair monthly night-shift schedules for medical residents, supports hard requests and soft preferences, allows scheduler review/manual adjustment, and exports approved schedules as ICS calendar files.

## Current Architecture

```text
Streamlit UI
	Home page for year-month and named draft creation
	Resident roster page
	Availability and Preferences page
	Scheduling Rules page
	Generate page with FullCalendar review, manual edits, and ICS export

SQLite
	Calendar months seeded for the next 10 years
	Residents
	Named schedule drafts
	Schedule requests
	Scheduling rules
	Generated assignments
	Solver run audit records

Python solver
	OR-Tools CP-SAT
	Expanded request ranges
	Hard constraints
	Soft fairness/preference objective
	Weekday-count rules

Calendar export module
	FullCalendar event builder
	ICS file builder for local downloads
```

## Completed MVP Work

### Draft Workflow

Status: complete.

- Calendar months are seeded for the next 10 years.
- Multiple named drafts can exist for the same year-month.
- Pages that operate on schedules use shared Year-Month and Draft selectors.
- Drafts store required nightly coverage.

### Availability and Preferences

Status: complete.

- Availability, preferences, vacation ranges, and hard assignments are represented as schedule requests.
- Request rows use resident-name dropdowns instead of raw resident IDs.
- Requests support `start_date` and `end_date`.
- Empty request tables do not create placeholder records.
- Request priorities default by type and can be overridden.
- Vacation ranges derive a soft prefer-work request for the Thursday before vacation starts when applicable.

### Solver Correctness

Status: MVP complete, with future tuning expected.

Hard constraints implemented:

- Every night is covered.
- Hard unavailable/vacation/approved absence/medical leave ranges are honored.
- Hard assign requests are honored.
- Hard assign requests cannot exceed required coverage for a date.
- Residents cannot exceed configured max monthly shifts.
- Hard weekday-count and adjacent-pair rules are enforced exactly.

Soft constraints implemented:

- Fairly distribute total shifts using floor/ceiling targets.
- Fairly distribute Sat/Sun weekend shifts using floor/ceiling targets.
- Avoid repeating surplus total and Sat/Sun weekend shifts from the previous 3 calendar months where feasible.
- Protect higher PGY levels from surplus total and weekend shifts where feasible.
- Randomize equal-cost leftover assignments on each solver run.
- Penalize prefer-off violations.
- Reward prefer-work matches.
- Avoid back-to-back night shifts where possible.
- Penalize soft weekday-count and adjacent-pair rule deviation.

### Review, Manual Adjustment, and Export

Status: complete.

- Generate page shows assignments in a FullCalendar month view.
- Generate page shows assignment rows, workload summary, and prefer-off violations.
- Unlocked assignments can be manually reassigned.
- Manual edits validate hard constraints before saving.
- Manual edits can optionally create hard assign requests.
- Generate page exports assignments as a single call-schedule ICS file for manual calendar import.

### Calendar Publishing

Status: complete for local export.

- ICS export uses one 6:00 PM-7:00 AM event per assignment.
- API-based Google Calendar publishing is intentionally out of scope to avoid embedded credentials and OAuth server maintenance.

## Test Coverage

Implemented tests cover:

- Multiple drafts for one year-month.
- Calendar month seeding.
- Empty schedule request table behavior.
- Resident dropdown label to ID mapping.
- Date-range vacation blocking.
- Vacation-derived Thursday prefer-work.
- Hard assign request honoring and conflicts.
- Too many hard assign requests on one date.
- Max-shift infeasibility.
- Preference-heavy feasible schedules.
- Back-to-back avoidance.
- Weekday-count and Friday+Saturday adjacent-pair rules.
- Manual reassignment with assign request creation.
- Calendar summary output.
- Streamlit page smoke loading.

## Remaining Follow-Up Work

- Add CSV import/export for residents, requests, rules, and assignments.
- Add seed/demo data.
- Add holiday labeling and holiday-specific distribution reporting.
- Add richer calendar styling and event filtering.
- Add structured logging.
- Add a configuration page for default time zone and shift times.
- Add packaged executable support with PyInstaller or a similar tool.
