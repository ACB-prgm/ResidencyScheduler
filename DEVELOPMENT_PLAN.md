# Residency Scheduler Development Plan

## Product Goal

Build a local-first scheduling application that generates fair monthly night-shift schedules for medical residents, supports hard exceptions and soft preferences, allows scheduler review/manual adjustment, and publishes the approved schedule to Google Calendar.

## Guiding Principles

- Hard constraints must never be violated.
- Soft constraints should be scored and explainable.
- The UI should make conflicts obvious before solving.
- Manual edits should be validated immediately.
- Calendar publishing must be idempotent and avoid duplicate events.
- The scheduling engine should be isolated from the UI so constraints can evolve.

## Current Architecture

```text
Streamlit UI
	Home and workflow pages
	Editable roster, availability, and locked assignment tables
	Review summaries and manual reassignment form
	Calendar event preview

SQLite
	Residents
	Schedule periods
	Availability/preferences
	Locked assignments
	Generated assignments
	Solver run audit records

Python solver
	OR-Tools CP-SAT
	Pre-solve validation
	Hard constraints
	Soft fairness/preference objective

Google Calendar module
	Event payload builder
	Preview rows for Streamlit
	Upsert-ready publishing function for authenticated callers
```

## Completed MVP Work

### Phase 1 - Local Skeleton

Status: complete.

- Repository structure is in place.
- SQLite schema initializes automatically.
- Streamlit navigation and workflow pages are present.
- Users can create schedule periods.
- Users can manage residents, availability/preferences, and locked assignments.
- Users can generate assignments and save them to SQLite.
- Calendar event preview exists.

### Phase 2 - Solver Correctness

Status: MVP complete, with future tuning expected.

Hard constraints implemented:

- Every night is covered.
- Hard unavailable/vacation/approved absence/medical leave dates are honored.
- Locked assignments are honored.
- Locked assignments cannot exceed required coverage for a date.
- Residents cannot exceed configured max monthly shifts.

Soft constraints implemented:

- Minimize total shift imbalance.
- Minimize weekend imbalance.
- Penalize prefer-off violations.
- Reward prefer-work matches.
- Avoid back-to-back night shifts where possible.

Validation implemented:

- Missing active residents.
- Invalid schedule period values.
- Invalid resident IDs.
- Invalid dates outside the period.
- Hard unavailable coverage gaps.
- Locked assignment conflicts.
- Max-shift capacity shortfalls.

### Phase 3 - Review and Manual Adjustment

Status: MVP complete.

- Review page shows generated assignments.
- Workload summary includes total, weekend, locked, and manual shifts.
- Prefer-off violations are shown.
- Unlocked assignments can be manually reassigned.
- Manual edits validate hard constraints before saving.
- Manual edits are marked `source = manual`.
- Manual edits can optionally create locked assignments.

### Phase 4 - Google Calendar Publishing

Status: partial.

- Event preview is implemented.
- Event payloads use one 6:00 PM-7:00 AM event per assignment.
- Calendar module supports idempotent upsert behavior when passed an authenticated Google Calendar service.
- The Streamlit UI does not yet perform OAuth or write events directly.
- Setup notes are documented in README and `.env.example`.

### Phase 5 - Packaging/Dev Experience

Status: partial.

- `scripts/init_db.py` initializes the active SQLite database.
- `scripts/run_app.sh` provides a simple local launcher.
- `.env.example` documents optional environment variables.

### Phase 6 - Hardening

Status: partial.

- Pytest coverage exists for core solver and validation scenarios.
- Test database isolation uses `RESIDENCY_SCHEDULER_DB`.
- Remaining hardening items are listed below.

## Test Coverage

Implemented tests cover:

- Normal feasible schedule.
- Vacation/hard unavailable is honored.
- Locked assignment is honored.
- Locked assignment conflicts with hard unavailable.
- Too many locked assignments on one date.
- Max shifts makes schedule infeasible.
- Preference-heavy but feasible schedule.
- Back-to-back avoidance when enough residents exist.

## Remaining Follow-Up Work

- Add database migrations for existing deployed local databases after schema changes.
- Add CSV import/export for residents, availability, locks, and assignments.
- Add seed/demo data.
- Add holiday labeling and holiday-specific distribution reporting.
- Add richer calendar-style monthly review layout.
- Wire OAuth in the Streamlit UI for direct Google Calendar publishing.
- Add optional delete/rebuild sync mode for Calendar publishing.
- Add structured logging.
- Add a configuration page for default time zone, shift times, and credential status.
- Add packaged executable support with PyInstaller or a similar tool.
