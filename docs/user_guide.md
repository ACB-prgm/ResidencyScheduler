# Residency Scheduler User Guide

## Year-Month

Use the Year-Month selector at the top of any page to choose the month you are editing. The selected month is global, so changing it on one page updates the active month everywhere.

Each year-month has one schedule. Scheduling rules, generated assignments, manual edits, and calendar publishing settings belong to the selected month. Availability and preferences are global date ranges that appear on any selected month they overlap.

## Residents

Residents are the people eligible for call scheduling.

- `PGY level` is stored from 1 through 5.
- Higher PGY levels are protected from surplus total shifts and surplus workload points when possible.
- Colors are used in the schedule calendar and must be unique.
- Inactive residents are not used by the solver.

## Availability and Preferences

Availability and Preferences can be dated entries or recurring weekly preferences. Dated entries can cover one date or a date range, and ranges can cross month boundaries. Cross-month entries appear on every selected month they overlap.

- `vacation`, `unavailable`, `approved_absence`, and `medical_leave` default to hard.
- `assign` defaults to hard and forces the resident onto the selected date.
- `prefer_off` and `prefer_work` default to soft.
- Recurring `prefer_off` and `prefer_work` entries apply weekly on one weekday, either indefinitely or through an end date, and are always soft.
- Hard availability and preferences must be honored by the solver.
- Soft availability and preferences affect the solver objective but can be violated if needed.
- A dated preference overrides a recurring preference for the same resident and date. Duplicate preferences are evaluated once.
- `Description` is optional context shown with the saved entry.

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

## Workload Fairness

The solver balances raw total shifts first. It then uses configurable workload points to distribute higher-value call days: Monday through Thursday are 1 point, Friday and Sunday are 1.5 points, and Saturday is 2 points. The prior three months are included so the same resident is less likely to receive surplus total shifts or surplus workload points repeatedly.

## Generate Schedule

Generate Schedule runs the solver for the selected month. The solver max time is an upper limit; the solver may finish sooner. Running the solver again replaces the current local assignments, including manual edits. The objective score is a weighted penalty score: lower is better when comparing repeated runs with the same month and unchanged inputs, but scores should not be compared across different months or inputs.

The page shows:

- Solver controls and recent run status
- A read-only, color-coded calendar using the call shift's start date
- Workload summary
	- Month shows only the selected month, L3M shows the selected month plus the prior two months, and YTD shows January through the selected month. Only saved assignments contribute.
	- Weekday means Monday through Thursday. Friday, Saturday, and Sunday are displayed separately.
	- Workload Points use Monday-Thursday = 1, Friday = 1.5, Saturday = 2, and Sunday = 1.5.
- Soft prefer-off violations
- Manual reassign and swap tools for unlocked assignments
- ICS export
- Google Calendar publishing

Manual reassignments and swaps validate hard unavailable conflicts before saving. The optional hard assign setting creates dated hard assign requests that remain in effect on future solver runs.

### Wiping a Local Schedule

**Wipe current schedule** permanently deletes only the selected month's local assignments, including manual edits. It does not delete residents, availability/preferences, recurring preferences, scheduling rules, hard assign requests, or solver run history. It also does not delete Google Calendar events. If the schedule was published, use **Wipe Scheduler Events** in the Google Calendar section before wiping the local schedule.

## Calendar Export and Publishing

ICS export downloads a single calendar file named for the selected year-month call schedule.

Google Calendar publishing writes the current month to a selected writable Google Calendar. Publishing deletes prior Residency Scheduler events for the selected month and calendar before inserting the current assignments. The app identifies its own events using private Google Calendar metadata, so it does not wipe unrelated calendar events.

The selected Google Calendar is remembered as your default for future months. Google events are all-day events on the shift start date. Residents with saved email addresses are added as attendees and receive Google invitation/update emails. Use **Refresh Google Calendar status** to recheck the selected calendar. Use **Wipe Scheduler Events** when you need to remove app-generated events for the selected year-month without changing the local schedule or publishing a replacement.

ICS export is a one-time download, not a live sync. Its events use the 6:00 PM start and 7:00 AM next-day end times.

Developer details retains the latest solver result and warnings for troubleshooting, including after a local schedule wipe.
