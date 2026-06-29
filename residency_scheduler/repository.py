from __future__ import annotations

import calendar
import json
from datetime import date, timedelta
from typing import Iterable

import pandas as pd

from residency_scheduler.colors import RESIDENT_COLOR_PALETTE
from residency_scheduler.db import get_connection, seed_calendar_months


HARD_UNAVAILABLE_TYPES = {"vacation", "unavailable", "approved_absence", "medical_leave"}
REQUEST_TYPES = HARD_UNAVAILABLE_TYPES | {"prefer_off", "prefer_work", "assign"}
HARD_DEFAULT_REQUEST_TYPES = HARD_UNAVAILABLE_TYPES | {"assign"}
SOFT_DEFAULT_REQUEST_TYPES = {"prefer_off", "prefer_work"}
PRIORITIES = {"hard", "soft"}
WEEKDAYS = {
	"Monday": 0,
	"Tuesday": 1,
	"Wednesday": 2,
	"Thursday": 3,
	"Friday": 4,
	"Saturday": 5,
	"Sunday": 6,
}
WEEKDAY_NAMES = {value: key for key, value in WEEKDAYS.items()}


def read_sql(query: str, params: tuple = ()) -> pd.DataFrame:
	with get_connection() as conn:
		return pd.read_sql_query(query, conn, params=params)


def get_app_state(key: str) -> str | None:
	with get_connection() as conn:
		row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
		return None if row is None else str(row["value"])


def set_app_state(key: str, value: str | None) -> None:
	with get_connection() as conn:
		if value is None:
			conn.execute("DELETE FROM app_state WHERE key = ?", (key,))
			return
		conn.execute(
			"""
			INSERT INTO app_state (key, value, updated_at)
			VALUES (?, ?, CURRENT_TIMESTAMP)
			ON CONFLICT(key) DO UPDATE SET
				value = excluded.value,
				updated_at = CURRENT_TIMESTAMP
			""",
			(key, value),
		)


def seed_months(start_year: int | None = None, years: int = 10) -> None:
	with get_connection() as conn:
		seed_calendar_months(conn, start_year=start_year, years=years)


def get_calendar_months() -> pd.DataFrame:
	return read_sql(
		"""
		SELECT id, year, month, month_key, start_date, display_name
		FROM calendar_months
		ORDER BY year, month
		"""
	)


def get_schedule_periods(year: int | None = None, month: int | None = None) -> pd.DataFrame:
	where = ""
	params: tuple = ()
	if year is not None and month is not None:
		where = "WHERE year = ? AND month = ?"
		params = (year, month)
	return read_sql(
		f"""
		SELECT id, year, month, draft_name, required_count, status, google_calendar_id, created_at
		FROM schedule_periods
		{where}
		ORDER BY year DESC, month DESC, id DESC
		""",
		params,
	)


def create_schedule_period(
	year: int,
	month: int,
	draft_name: str,
	required_count: int,
	google_calendar_id: str | None,
) -> int:
	if month < 1 or month > 12:
		raise ValueError("Month must be between 1 and 12.")
	if year < 2024 or year > 2100:
		raise ValueError("Year must be between 2024 and 2100.")
	if required_count < 1:
		raise ValueError("Residents per night must be at least 1.")
	draft_name = draft_name.strip() or "Draft 1"

	with get_connection() as conn:
		cursor = conn.execute(
			"""
			INSERT INTO schedule_periods (year, month, draft_name, required_count, google_calendar_id)
			VALUES (?, ?, ?, ?, ?)
			RETURNING id
			""",
			(year, month, draft_name, required_count, google_calendar_id),
		)
		return int(cursor.fetchone()[0])


def rename_schedule_period(period_id: int, draft_name: str) -> None:
	draft_name = draft_name.strip()
	if not draft_name:
		raise ValueError("Draft name is required.")
	with get_connection() as conn:
		cursor = conn.execute(
			"""
			UPDATE schedule_periods
			SET draft_name = ?
			WHERE id = ?
			""",
			(draft_name, int(period_id)),
		)
		if cursor.rowcount == 0:
			raise ValueError(f"Schedule period #{period_id} was not found.")


def delete_schedule_period(period_id: int) -> None:
	with get_connection() as conn:
		cursor = conn.execute("DELETE FROM schedule_periods WHERE id = ?", (int(period_id),))
		if cursor.rowcount == 0:
			raise ValueError(f"Schedule period #{period_id} was not found.")


def get_period(period_id: int) -> dict:
	with get_connection() as conn:
		row = conn.execute("SELECT * FROM schedule_periods WHERE id = ?", (period_id,)).fetchone()
		if row is None:
			raise ValueError(f"Schedule period #{period_id} was not found.")
		return dict(row)


def period_dates(period_id: int) -> list[str]:
	period = get_period(period_id)
	last_day = calendar.monthrange(int(period["year"]), int(period["month"]))[1]
	return [date(int(period["year"]), int(period["month"]), day).isoformat() for day in range(1, last_day + 1)]


def get_residents(active_only: bool = False) -> pd.DataFrame:
	where = "WHERE active = 1" if active_only else ""
	return read_sql(
		f"""
		SELECT id, name, email, max_shifts, min_shifts, weight, color, active
		FROM residents
		{where}
		ORDER BY name
		"""
	)


def resident_label(row) -> str:
	return f"{row.name} · resident #{int(row.id)}"


def get_resident_options(active_only: bool = True) -> dict[str, int]:
	residents = get_residents(active_only=active_only)
	return {resident_label(row): int(row.id) for row in residents.itertuples()}


def save_residents(df: pd.DataFrame) -> None:
	required_columns = ["id", "name", "email", "max_shifts", "min_shifts", "weight", "color", "active"]
	clean = df.copy()

	for column in required_columns:
		if column not in clean.columns:
			clean[column] = None

	clean = clean[required_columns]
	clean = clean[clean["name"].notna() & (clean["name"].astype(str).str.strip() != "")]
	clean["name"] = clean["name"].astype(str).str.strip()
	clean["email"] = clean["email"].fillna("").astype(str).str.strip()
	clean["id"] = pd.to_numeric(clean["id"], errors="coerce").astype("Int64")
	clean["max_shifts"] = pd.to_numeric(clean["max_shifts"], errors="coerce").astype("Int64")
	clean["min_shifts"] = pd.to_numeric(clean["min_shifts"], errors="coerce").astype("Int64")
	clean["weight"] = pd.to_numeric(clean["weight"], errors="coerce").fillna(1.0).round().clip(lower=1, upper=5).astype(int)
	clean["color"] = clean["color"].fillna("").astype(str).str.strip().str.upper()
	clean["active"] = clean["active"].fillna(1).astype(int)
	_validate_resident_colors(clean)

	with get_connection() as conn:
		existing_ids = {int(row[0]) for row in conn.execute("SELECT id FROM residents").fetchall()}
		used_colors = {
			str(row["color"]).upper()
			for row in conn.execute("SELECT color FROM residents WHERE color IS NOT NULL AND color != ''").fetchall()
		}
		seen_ids: set[int] = set()
		for row in clean.where(pd.notnull(clean), None).itertuples(index=False):
			resident_id_value = None if pd.isna(row.id) else int(row.id)
			max_shifts = None if pd.isna(row.max_shifts) else int(row.max_shifts)
			min_shifts = None if pd.isna(row.min_shifts) else int(row.min_shifts)
			color = str(row.color or "").strip().upper()
			if resident_id_value is None:
				if color and color in used_colors:
					raise ValueError(f"Resident colors must be unique: {color}.")
				cursor = conn.execute(
					"""
					INSERT INTO residents (name, email, max_shifts, min_shifts, weight, color, active)
					VALUES (?, ?, ?, ?, ?, ?, ?)
					""",
					(row.name, row.email, max_shifts, min_shifts, int(row.weight), color or None, int(row.active)),
				)
				new_resident_id = int(cursor.lastrowid)
				if not color:
					color = _next_resident_color(used_colors, new_resident_id)
					conn.execute("UPDATE residents SET color = ? WHERE id = ?", (color, new_resident_id))
				used_colors.add(color)
				continue

			resident_id = resident_id_value
			seen_ids.add(resident_id)
			if resident_id not in existing_ids:
				raise ValueError(f"Resident ID {resident_id} does not exist.")
			old_color_row = conn.execute("SELECT color FROM residents WHERE id = ?", (resident_id,)).fetchone()
			old_color = str(old_color_row["color"] or "").upper() if old_color_row else ""
			if old_color:
				used_colors.discard(old_color)
			if color and color in used_colors:
				raise ValueError(f"Resident colors must be unique: {color}.")
			if not color:
				color = _next_resident_color(used_colors, resident_id)
			used_colors.add(color)
			conn.execute(
				"""
				UPDATE residents
				SET name = ?, email = ?, max_shifts = ?, min_shifts = ?, weight = ?, color = ?,
					active = ?, updated_at = CURRENT_TIMESTAMP
				WHERE id = ?
				""",
				(row.name, row.email, max_shifts, min_shifts, int(row.weight), color, int(row.active), resident_id),
			)

		for resident_id in existing_ids - seen_ids:
			conn.execute(
				"UPDATE residents SET active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
				(resident_id,),
			)


def default_priority_for_request_type(request_type: str) -> str:
	request_type = request_type.lower()
	if request_type in SOFT_DEFAULT_REQUEST_TYPES:
		return "soft"
	return "hard"


def get_schedule_requests(period_id: int) -> pd.DataFrame:
	return read_sql(
		"""
		SELECT sr.id, sr.period_id, sr.resident_id, r.name AS resident_name,
			sr.start_date, sr.end_date, sr.request_type, sr.priority, sr.reason
		FROM schedule_requests sr
		JOIN residents r ON r.id = sr.resident_id
		WHERE sr.period_id = ?
		ORDER BY sr.start_date, sr.end_date, r.name, sr.request_type
		""",
		(period_id,),
	)


def get_schedule_requests_for_editor(period_id: int) -> pd.DataFrame:
	requests = get_schedule_requests(period_id)
	columns = ["resident", "start_date", "end_date", "request_type", "priority", "reason"]
	if requests.empty:
		return pd.DataFrame(columns=columns)

	requests["resident"] = requests["resident_name"] + " · resident #" + requests["resident_id"].astype(str)
	requests["start_date"] = pd.to_datetime(requests["start_date"]).dt.date
	requests["end_date"] = pd.to_datetime(requests["end_date"]).dt.date
	return requests[columns]


def replace_schedule_requests(period_id: int, df: pd.DataFrame) -> None:
	columns = ["resident", "resident_id", "start_date", "end_date", "request_type", "priority", "reason"]
	clean = _ensure_columns(df, columns)
	if clean.empty:
		with get_connection() as conn:
			conn.execute("DELETE FROM schedule_requests WHERE period_id = ?", (period_id,))
		return

	resident_options = get_resident_options(active_only=True)
	rows = []
	valid_dates = set(period_dates(period_id))
	for row in clean.itertuples(index=False):
		resident_id = _resolve_resident_id(row, resident_options)
		start = _coerce_date(row.start_date, "Start date")
		end = _coerce_date(row.end_date, "End date")
		if end < start:
			raise ValueError("End date cannot be before start date.")
		request_type = str(row.request_type).strip().lower()
		if request_type not in REQUEST_TYPES:
			raise ValueError(f"Invalid request type '{row.request_type}'.")
		priority_value = default_priority_for_request_type(request_type) if pd.isna(row.priority) or str(row.priority).strip() == "" else row.priority
		priority = str(priority_value).strip().lower()
		if priority not in PRIORITIES:
			raise ValueError(f"Invalid priority '{row.priority}'.")
		for work_date in _date_range(start, end):
			if work_date.isoformat() not in valid_dates:
				raise ValueError(f"Request date {work_date.isoformat()} is outside the selected draft month.")
		rows.append((period_id, resident_id, start.isoformat(), end.isoformat(), request_type, priority, row.reason or ""))

	with get_connection() as conn:
		conn.execute("DELETE FROM schedule_requests WHERE period_id = ?", (period_id,))
		conn.executemany(
			"""
			INSERT INTO schedule_requests (period_id, resident_id, start_date, end_date, request_type, priority, reason)
			VALUES (?, ?, ?, ?, ?, ?, ?)
			""",
			rows,
		)


def get_expanded_schedule_requests(period_id: int) -> pd.DataFrame:
	requests = get_schedule_requests(period_id)
	period = get_period(period_id)
	rows: list[dict] = []
	for request in requests.itertuples():
		start = date.fromisoformat(str(request.start_date))
		end = date.fromisoformat(str(request.end_date))
		for work_date in _date_range(start, end):
			rows.append(
				{
					"resident_id": int(request.resident_id),
					"resident_name": request.resident_name,
					"work_date": work_date.isoformat(),
					"request_type": request.request_type,
					"priority": request.priority,
					"reason": request.reason,
					"source": "user",
				}
			)
		if str(request.request_type).lower() == "vacation":
			prior_thursday = _previous_thursday(start)
			if prior_thursday.year == int(period["year"]) and prior_thursday.month == int(period["month"]):
				rows.append(
					{
						"resident_id": int(request.resident_id),
						"resident_name": request.resident_name,
						"work_date": prior_thursday.isoformat(),
						"request_type": "prefer_work",
						"priority": "soft",
						"reason": "Auto preference: Thursday before vacation starts",
						"source": "derived",
					}
				)
	return pd.DataFrame(
		rows,
		columns=["resident_id", "resident_name", "work_date", "request_type", "priority", "reason", "source"],
	)


def get_schedule_rules(period_id: int) -> pd.DataFrame:
	return read_sql(
		"""
		SELECT sr.id, sr.period_id, sr.resident_id, r.name AS resident_name,
			sr.rule_type, sr.weekday, sr.paired_weekday, sr.comparator, sr.target_count, sr.priority, sr.reason
		FROM schedule_rules sr
		JOIN residents r ON r.id = sr.resident_id
		WHERE sr.period_id = ?
		ORDER BY r.name, sr.weekday, sr.paired_weekday, sr.rule_type
		""",
		(period_id,),
	)


def get_schedule_rules_for_editor(period_id: int) -> pd.DataFrame:
	rules = get_schedule_rules(period_id)
	columns = ["resident", "rule_type", "weekday", "paired_weekday", "comparator", "target_count", "priority", "reason"]
	if rules.empty:
		return pd.DataFrame(columns=columns)

	rules["resident"] = rules["resident_name"] + " · resident #" + rules["resident_id"].astype(str)
	rules["weekday"] = rules["weekday"].map(WEEKDAY_NAMES)
	rules["paired_weekday"] = rules["paired_weekday"].map(WEEKDAY_NAMES)
	return rules[columns]


def replace_schedule_rules(period_id: int, df: pd.DataFrame) -> None:
	columns = [
		"resident",
		"resident_id",
		"rule_type",
		"weekday",
		"paired_weekday",
		"comparator",
		"target_count",
		"priority",
		"reason",
	]
	clean = _ensure_columns(df, columns)
	if clean.empty:
		with get_connection() as conn:
			conn.execute("DELETE FROM schedule_rules WHERE period_id = ?", (period_id,))
		return

	resident_options = get_resident_options(active_only=True)
	rows = []
	for row in clean.itertuples(index=False):
		resident_id = _resolve_resident_id(row, resident_options)
		rule_type = str(row.rule_type or "weekday_count").strip().lower()
		if rule_type not in {"weekday_count", "weekday_pair_count"}:
			raise ValueError("Only weekday_count and weekday_pair_count rules are supported.")
		weekday = _coerce_weekday(row.weekday)
		paired_weekday = None
		if rule_type == "weekday_pair_count":
			paired_weekday = _coerce_weekday(row.paired_weekday)
		comparator = str(row.comparator or "exactly").strip().lower()
		if comparator != "exactly":
			raise ValueError("Only exactly rules are supported.")
		target_count = int(pd.to_numeric(row.target_count, errors="raise"))
		if target_count < 0:
			raise ValueError("Rule target count cannot be negative.")
		priority = str(row.priority or "hard").strip().lower()
		if priority not in PRIORITIES:
			raise ValueError(f"Invalid priority '{row.priority}'.")
		rows.append((period_id, resident_id, rule_type, weekday, paired_weekday, comparator, target_count, priority, row.reason or ""))

	with get_connection() as conn:
		conn.execute("DELETE FROM schedule_rules WHERE period_id = ?", (period_id,))
		conn.executemany(
			"""
			INSERT INTO schedule_rules (
				period_id, resident_id, rule_type, weekday, paired_weekday, comparator, target_count, priority, reason
			)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
			""",
			rows,
		)


def get_assignments(period_id: int) -> pd.DataFrame:
	return read_sql(
		"""
		SELECT a.id, a.period_id, a.work_date, a.resident_id, r.name AS resident_name,
			r.color AS resident_color,
			CASE
				WHEN CAST(ROUND(r.weight) AS INTEGER) < 1 THEN 1
				WHEN CAST(ROUND(r.weight) AS INTEGER) > 5 THEN 5
				ELSE CAST(ROUND(r.weight) AS INTEGER)
			END AS resident_pgy,
			a.source, a.is_locked, a.google_event_id
		FROM assignments a
		JOIN residents r ON r.id = a.resident_id
		WHERE a.period_id = ?
		ORDER BY a.work_date, r.name
		""",
		(period_id,),
	)


def get_prior_assignment_history(period_id: int, months: int = 3) -> pd.DataFrame:
	period = get_period(period_id)
	columns = ["period_id", "year", "month", "resident_id", "work_date", "is_weekend"]
	if months < 1:
		return pd.DataFrame(columns=columns)

	selected_period_ids = []
	cursor_month = date(int(period["year"]), int(period["month"]), 1)
	with get_connection() as conn:
		for _ in range(months):
			cursor_month = _previous_month(cursor_month)
			row = conn.execute(
				"""
				SELECT sp.id
				FROM schedule_periods sp
				WHERE sp.year = ?
					AND sp.month = ?
					AND EXISTS (
						SELECT 1
						FROM assignments a
						WHERE a.period_id = sp.id
					)
				ORDER BY sp.id DESC
				LIMIT 1
				""",
				(cursor_month.year, cursor_month.month),
			).fetchone()
			if row is not None:
				selected_period_ids.append(int(row["id"]))

	if not selected_period_ids:
		return pd.DataFrame(columns=columns)

	placeholders = ",".join("?" for _ in selected_period_ids)
	history = read_sql(
		f"""
		SELECT sp.id AS period_id, sp.year, sp.month, a.resident_id, a.work_date,
			CASE
				WHEN CAST(strftime('%w', a.work_date) AS INTEGER) IN (0, 6) THEN 1
				ELSE 0
			END AS is_weekend
		FROM schedule_periods sp
		JOIN assignments a ON a.period_id = sp.id
		WHERE sp.id IN ({placeholders})
		ORDER BY sp.year, sp.month, a.work_date, a.resident_id
		""",
		tuple(selected_period_ids),
	)
	return history[columns]


def _validate_resident_colors(df: pd.DataFrame) -> None:
	provided = [str(color).strip().upper() for color in df["color"].tolist() if str(color).strip()]
	invalid = sorted(set(provided) - set(RESIDENT_COLOR_PALETTE))
	if invalid:
		raise ValueError(f"Resident color must be one of the configured palette colors: {', '.join(invalid)}.")
	duplicates = sorted({color for color in provided if provided.count(color) > 1})
	if duplicates:
		raise ValueError(f"Resident colors must be unique: {', '.join(duplicates)}.")


def _next_resident_color(used_colors: set[str], resident_id: int) -> str:
	if len(used_colors) >= len(RESIDENT_COLOR_PALETTE):
		raise ValueError("No unused resident colors remain in the configured palette.")
	start = (resident_id - 1) % len(RESIDENT_COLOR_PALETTE)
	for offset in range(len(RESIDENT_COLOR_PALETTE)):
		color = RESIDENT_COLOR_PALETTE[(start + offset) % len(RESIDENT_COLOR_PALETTE)]
		if color not in used_colors:
			return color
	raise ValueError("No unused resident colors remain in the configured palette.")


def save_assignments(period_id: int, assignments: Iterable[dict]) -> None:
	rows = [
		(
			period_id,
			item["work_date"],
			item["resident_id"],
			item.get("source", "solver"),
			int(item.get("is_locked", 0)),
		)
		for item in assignments
	]

	with get_connection() as conn:
		conn.execute("DELETE FROM assignments WHERE period_id = ?", (period_id,))
		conn.executemany(
			"""
			INSERT INTO assignments (period_id, work_date, resident_id, source, is_locked)
			VALUES (?, ?, ?, ?, ?)
			""",
			rows,
		)


def update_assignment_resident(
	assignment_id: int,
	resident_id: int,
	make_locked: bool = False,
	lock_reason: str | None = None,
) -> None:
	with get_connection() as conn:
		current = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
		if current is None:
			raise ValueError(f"Assignment #{assignment_id} was not found.")
		if int(current["is_locked"]) == 1:
			raise ValueError("Hard assigned shifts cannot be reassigned from the review page.")

	_validate_manual_assignment(
		int(current["period_id"]),
		str(current["work_date"]),
		int(resident_id),
		exclude_assignment_id=assignment_id,
	)

	with get_connection() as conn:
		conn.execute(
			"""
			UPDATE assignments
			SET resident_id = ?, source = 'manual', is_locked = ?, updated_at = CURRENT_TIMESTAMP
			WHERE id = ?
			""",
			(int(resident_id), int(make_locked), assignment_id),
		)
		if make_locked:
			conn.execute(
				"""
				INSERT INTO schedule_requests (period_id, resident_id, start_date, end_date, request_type, priority, reason)
				VALUES (?, ?, ?, ?, 'assign', 'hard', ?)
				""",
				(
					int(current["period_id"]),
					int(resident_id),
					str(current["work_date"]),
					str(current["work_date"]),
					lock_reason or "Manual review edit",
				),
			)


def swap_assignment_residents(
	first_assignment_id: int,
	second_assignment_id: int,
	make_locked: bool = False,
	lock_reason: str | None = None,
) -> None:
	if int(first_assignment_id) == int(second_assignment_id):
		raise ValueError("Select two different assignments to swap.")

	with get_connection() as conn:
		first = conn.execute("SELECT * FROM assignments WHERE id = ?", (int(first_assignment_id),)).fetchone()
		second = conn.execute("SELECT * FROM assignments WHERE id = ?", (int(second_assignment_id),)).fetchone()

	if first is None or second is None:
		raise ValueError("Both assignments must exist before they can be swapped.")
	if int(first["period_id"]) != int(second["period_id"]):
		raise ValueError("Assignments must belong to the same draft.")
	if int(first["is_locked"]) == 1 or int(second["is_locked"]) == 1:
		raise ValueError("Hard assigned shifts cannot be swapped.")
	if int(first["resident_id"]) == int(second["resident_id"]):
		raise ValueError("Select assignments with different residents to swap.")

	_validate_manual_assignment(
		int(first["period_id"]),
		str(second["work_date"]),
		int(first["resident_id"]),
		exclude_assignment_ids=[int(first["id"]), int(second["id"])],
	)
	_validate_manual_assignment(
		int(first["period_id"]),
		str(first["work_date"]),
		int(second["resident_id"]),
		exclude_assignment_ids=[int(first["id"]), int(second["id"])],
	)

	with get_connection() as conn:
		conn.execute(
			"""
			UPDATE assignments
			SET resident_id = ?, source = 'manual', is_locked = ?, updated_at = CURRENT_TIMESTAMP
			WHERE id = ?
			""",
			(int(second["resident_id"]), int(make_locked), int(first["id"])),
		)
		conn.execute(
			"""
			UPDATE assignments
			SET resident_id = ?, source = 'manual', is_locked = ?, updated_at = CURRENT_TIMESTAMP
			WHERE id = ?
			""",
			(int(first["resident_id"]), int(make_locked), int(second["id"])),
		)
		if make_locked:
			reason = lock_reason or "Manual review swap"
			conn.executemany(
				"""
				INSERT INTO schedule_requests (period_id, resident_id, start_date, end_date, request_type, priority, reason)
				VALUES (?, ?, ?, ?, 'assign', 'hard', ?)
				""",
				[
					(int(first["period_id"]), int(second["resident_id"]), str(first["work_date"]), str(first["work_date"]), reason),
					(int(second["period_id"]), int(first["resident_id"]), str(second["work_date"]), str(second["work_date"]), reason),
				],
			)


def update_assignment_google_event_id(assignment_id: int, google_event_id: str) -> None:
	with get_connection() as conn:
		conn.execute(
			"""
			UPDATE assignments
			SET google_event_id = ?, updated_at = CURRENT_TIMESTAMP
			WHERE id = ?
			""",
			(google_event_id, assignment_id),
		)


def get_schedule_runs(period_id: int) -> pd.DataFrame:
	return read_sql(
		"""
		SELECT id, period_id, run_at, solver_status, objective_score, warnings_json
		FROM schedule_runs
		WHERE period_id = ?
		ORDER BY run_at DESC, id DESC
		""",
		(period_id,),
	)


def record_solver_run(period_id: int, solver_status: str, objective_score: float | None, warnings: list[str]) -> None:
	with get_connection() as conn:
		conn.execute(
			"""
			INSERT INTO schedule_runs (period_id, solver_status, objective_score, warnings_json)
			VALUES (?, ?, ?, ?)
			""",
			(period_id, solver_status, objective_score, json.dumps(warnings)),
		)


def get_workload_summary(period_id: int) -> pd.DataFrame:
	assignments = get_assignments(period_id)
	if assignments.empty:
		return pd.DataFrame(columns=["resident_name", "total_shifts", "weekend_shifts", "hard_assigned_shifts", "manual_shifts"])

	work_dates = pd.to_datetime(assignments["work_date"])
	assignments = assignments.assign(is_weekend=work_dates.dt.weekday >= 5)
	return (
		assignments.groupby("resident_name")
		.agg(
			total_shifts=("id", "count"),
			weekend_shifts=("is_weekend", "sum"),
			hard_assigned_shifts=("is_locked", "sum"),
			manual_shifts=("source", lambda values: int((values == "manual").sum())),
		)
		.reset_index()
		.sort_values(["total_shifts", "weekend_shifts", "resident_name"], ascending=[False, False, True])
	)


def get_preference_violations(period_id: int) -> pd.DataFrame:
	assignments = get_assignments(period_id)
	requests = get_expanded_schedule_requests(period_id)
	if assignments.empty or requests.empty:
		return pd.DataFrame(columns=["work_date", "resident_name", "request_type", "priority", "reason"])

	soft = requests[requests["priority"].str.lower() == "soft"].copy()
	if soft.empty:
		return pd.DataFrame(columns=["work_date", "resident_name", "request_type", "priority", "reason"])

	violations = assignments.merge(
		soft,
		on=["resident_id", "work_date"],
		suffixes=("_assignment", "_request"),
	)
	violations = violations[violations["request_type"].str.lower() == "prefer_off"]
	return violations[["work_date", "resident_name_assignment", "request_type", "priority", "reason"]].rename(
		columns={"resident_name_assignment": "resident_name"}
	)


def get_assignment_calendar(period_id: int) -> pd.DataFrame:
	period = get_period(period_id)
	assignments = get_assignments(period_id)
	by_date: dict[str, list[str]] = {}
	for row in assignments.itertuples():
		marker = " *" if int(row.is_locked) else ""
		by_date.setdefault(str(row.work_date), []).append(f"{row.resident_name}{marker}")

	weeks = calendar.monthcalendar(int(period["year"]), int(period["month"]))
	rows = []
	for week in weeks:
		display_week = {}
		for index, day in enumerate(week):
			weekday = calendar.day_name[index]
			if day == 0:
				display_week[weekday] = ""
				continue
			work_date = date(int(period["year"]), int(period["month"]), day).isoformat()
			names = "\n".join(by_date.get(work_date, []))
			display_week[weekday] = f"{day}\n{names}" if names else str(day)
		rows.append(display_week)
	return pd.DataFrame(rows)


def _ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
	clean = df.copy()
	for column in columns:
		if column not in clean.columns:
			clean[column] = None
	clean = clean[columns]
	clean = clean.dropna(how="all")
	if "resident" in clean.columns:
		mask = clean["resident"].notna() | clean["resident_id"].notna()
		if "start_date" in clean.columns:
			mask = mask | clean["start_date"].notna()
		if "rule_type" in clean.columns:
			mask = mask | clean["rule_type"].notna()
		clean = clean[mask]
	return clean


def _resolve_resident_id(row, resident_options: dict[str, int]) -> int:
	if getattr(row, "resident", None) in resident_options:
		return resident_options[row.resident]
	if pd.notna(getattr(row, "resident_id", None)):
		resident_id = int(row.resident_id)
		if resident_id in set(resident_options.values()):
			return resident_id
	raise ValueError("Select a valid active resident.")


def _coerce_date(value, label: str) -> date:
	if pd.isna(value):
		raise ValueError(f"{label} is required.")
	if isinstance(value, date):
		return value
	return pd.to_datetime(value).date()


def _coerce_weekday(value) -> int:
	if pd.isna(value):
		raise ValueError("Weekday is required.")
	if isinstance(value, str):
		value = value.strip()
		if value in WEEKDAYS:
			return WEEKDAYS[value]
		if value.isdigit():
			number = int(value)
			if 0 <= number <= 6:
				return number
	if isinstance(value, int) and 0 <= value <= 6:
		return value
	raise ValueError(f"Invalid weekday '{value}'.")


def _date_range(start: date, end: date) -> list[date]:
	return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _previous_thursday(start: date) -> date:
	days_back = (start.weekday() - WEEKDAYS["Thursday"]) % 7
	if days_back == 0:
		days_back = 7
	return start - timedelta(days=days_back)


def _previous_month(month_start: date) -> date:
	if month_start.month == 1:
		return date(month_start.year - 1, 12, 1)
	return date(month_start.year, month_start.month - 1, 1)


def _validate_manual_assignment(
	period_id: int,
	work_date: str,
	resident_id: int,
	exclude_assignment_id: int | None = None,
	exclude_assignment_ids: list[int] | None = None,
) -> None:
	excluded_ids = set(exclude_assignment_ids or [])
	if exclude_assignment_id is not None:
		excluded_ids.add(int(exclude_assignment_id))

	valid_dates = set(period_dates(period_id))
	if work_date not in valid_dates:
		raise ValueError("Assignment date is outside the selected draft month.")
	if resident_id not in set(get_residents(active_only=True)["id"].astype(int).tolist()):
		raise ValueError("Selected resident is not active.")

	requests = get_expanded_schedule_requests(period_id)
	if not requests.empty:
		hard_unavailable = requests[
			(requests["resident_id"].astype(int) == int(resident_id))
			& (requests["work_date"].astype(str) == str(work_date))
			& (requests["priority"].str.lower() == "hard")
			& (requests["request_type"].str.lower().isin(HARD_UNAVAILABLE_TYPES))
		]
		if not hard_unavailable.empty:
			raise ValueError("Selected resident is hard unavailable on that date.")

	with get_connection() as conn:
		params: list[object] = [period_id, work_date, resident_id]
		exclude = ""
		if excluded_ids:
			placeholders = ",".join("?" for _ in excluded_ids)
			exclude = f"AND id NOT IN ({placeholders})"
			params.extend(sorted(excluded_ids))
		duplicate = conn.execute(
			f"""
			SELECT id FROM assignments
			WHERE period_id = ? AND work_date = ? AND resident_id = ?
			{exclude}
			""",
			tuple(params),
		).fetchone()
		if duplicate is not None:
			raise ValueError("Selected resident is already assigned on that date.")

		resident = conn.execute("SELECT max_shifts FROM residents WHERE id = ?", (resident_id,)).fetchone()
		if resident is not None and resident["max_shifts"] is not None:
			count_params: list[object] = [period_id, resident_id]
			exclude_count = ""
			if excluded_ids:
				placeholders = ",".join("?" for _ in excluded_ids)
				exclude_count = f"AND id NOT IN ({placeholders})"
				count_params.extend(sorted(excluded_ids))
			current_total = conn.execute(
				f"""
				SELECT COUNT(*) FROM assignments
				WHERE period_id = ? AND resident_id = ?
				{exclude_count}
				""",
				tuple(count_params),
			).fetchone()[0]
			if int(current_total) + 1 > int(resident["max_shifts"]):
				raise ValueError("Selected resident would exceed their configured max monthly shifts.")
