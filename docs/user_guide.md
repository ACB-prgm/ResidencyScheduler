# Residency Scheduler User Guide

## Year-Month

Use the Year-Month selector at the top of any page to choose the month you are editing. The selected month is global, so changing it on one page updates the active month everywhere.

Each year-month has one schedule. Availability, preferences, scheduling rules, generated assignments, manual edits, and calendar publishing settings all belong to the selected month.

## Residents

Residents are the people eligible for call scheduling.

- `PGY level` is stored from 1 through 5.
- Higher PGY levels are protected from surplus total shifts and surplus weekend shifts when possible.
- Colors are used in the schedule calendar and must be unique.
- Inactive residents are not used by the solver.

## Availability and Preferences

Availability and Preferences are date-based entries. An entry can cover one date or a date range.

- `vacation`, `unavailable`, `approved_absence`, and `medical_leave` default to hard.
- `assign` defaults to hard and forces the resident onto the selected date.
- `prefer_off` and `prefer_work` default to soft.
- Hard availability and preferences must be honored by the solver.
- Soft availability and preferences affect the solver objective but can be violated if needed.

Vacation ranges automatically add a soft `prefer_work` preference for the Thursday before vacation starts when that Thursday is inside the selected month.

## Scheduling Rules

Scheduling Rules are month-specific constraints. New scheduling rules default to hard priority. Change a rule to soft only when it is acceptable for the solver to miss the target.

### Weekday Count

Use Weekday Count when a resident must work an exact number of a selected weekday.

Example: `Resident A` must work exactly `2` Fridays.

### Weekday Pair Count

Use Weekday Pair Count when a resident must work adjacent weekday pairs.

Example: `Resident A` must work exactly `1` Friday+Saturday pair. The solver counts only adjacent Friday and Saturday dates in the selected month.

For a hard Friday+Saturday pair rule, the resident must work the complete pair and cannot receive extra unpaired Fridays or Saturdays from that rule.

### Away Rotation

Use Away Rotation when a resident is away for the month and should not receive ordinary call assignments.

- Hard Away Rotation blocks all ordinary assignments for that resident.
- Hard `assign`, hard Weekday Count, and hard Weekday Pair Count rules can explicitly allow required dates.
- Soft Away Rotation strongly discourages assignments but does not make them impossible.

Example: if a resident is away but must cover one Friday+Saturday pair, add both:

- Away Rotation, hard
- Weekday Pair Count, Friday + Saturday, exactly 1, hard

That resident will only work the required Friday+Saturday pair, and the rest of the month will be spread across the other eligible residents.

## Weekend Fairness

Weekend shifts are Friday, Saturday, and Sunday. The solver balances these weekend shifts across residents when possible and also looks at the prior three months to avoid repeatedly giving the same resident surplus weekend burden.

## Generate Schedule

Generate Schedule runs the solver for the selected month. The page shows:

- Solver controls and recent run status
- Calendar view
- Workload summary
- Preference violations
- Manual reassign and swap tools
- ICS export
- Google Calendar publishing

Manual reassignments and swaps validate hard unavailable conflicts before saving.

## Calendar Export and Publishing

ICS export downloads a single calendar file named for the selected year-month call schedule.

Google Calendar publishing writes the current month to a selected writable Google Calendar. Publishing deletes prior Residency Scheduler events for the selected month and calendar before inserting the current assignments. The app identifies its own events using private Google Calendar metadata, so it does not wipe unrelated calendar events.

The selected Google Calendar is remembered as your default for future months. Use Wipe Scheduler Events when you need to remove the app-generated events for the selected year-month without publishing a replacement schedule.
