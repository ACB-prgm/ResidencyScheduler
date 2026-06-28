from __future__ import annotations

import calendar
import os
import sqlite3
from datetime import date
from pathlib import Path

from residency_scheduler.colors import RESIDENT_COLOR_PALETTE

DB_DIR = Path("data")
DB_PATH = DB_DIR / "residency_scheduler.sqlite"


def get_db_path() -> Path:
	"""Return the active SQLite database path.

	Tests can set RESIDENCY_SCHEDULER_DB to isolate themselves from a user's
	local app database.
	"""
	return Path(os.environ.get("RESIDENCY_SCHEDULER_DB", str(DB_PATH)))


def get_connection() -> sqlite3.Connection:
	db_path = get_db_path()
	db_path.parent.mkdir(parents=True, exist_ok=True)
	conn = sqlite3.connect(db_path)
	conn.row_factory = sqlite3.Row
	conn.execute("PRAGMA foreign_keys = ON;")
	return conn


def init_db() -> None:
	with get_connection() as conn:
		_prepare_schema(conn)
		conn.executescript(
			"""
			CREATE TABLE IF NOT EXISTS calendar_months (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				year INTEGER NOT NULL,
				month INTEGER NOT NULL,
				month_key TEXT NOT NULL UNIQUE,
				start_date TEXT NOT NULL,
				display_name TEXT NOT NULL,
				UNIQUE(year, month)
			);

			CREATE TABLE IF NOT EXISTS app_state (
				key TEXT PRIMARY KEY,
				value TEXT NOT NULL,
				updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
			);

			CREATE TABLE IF NOT EXISTS residents (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				name TEXT NOT NULL,
				email TEXT,
				max_shifts INTEGER,
				min_shifts INTEGER,
				weight REAL NOT NULL DEFAULT 1.0,
				color TEXT,
				active INTEGER NOT NULL DEFAULT 1,
				created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
				updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
			);

			CREATE TABLE IF NOT EXISTS schedule_periods (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				year INTEGER NOT NULL,
				month INTEGER NOT NULL,
				draft_name TEXT NOT NULL DEFAULT 'Draft 1',
				required_count INTEGER NOT NULL DEFAULT 1,
				status TEXT NOT NULL DEFAULT 'draft',
				google_calendar_id TEXT,
				created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
			);

			CREATE TABLE IF NOT EXISTS schedule_requests (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				period_id INTEGER NOT NULL,
				resident_id INTEGER NOT NULL,
				start_date TEXT NOT NULL,
				end_date TEXT NOT NULL,
				request_type TEXT NOT NULL,
				priority TEXT NOT NULL,
				reason TEXT,
				created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
				updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
				FOREIGN KEY (period_id) REFERENCES schedule_periods(id) ON DELETE CASCADE,
				FOREIGN KEY (resident_id) REFERENCES residents(id) ON DELETE CASCADE
			);

			CREATE TABLE IF NOT EXISTS schedule_rules (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				period_id INTEGER NOT NULL,
				resident_id INTEGER NOT NULL,
				rule_type TEXT NOT NULL,
				weekday INTEGER NOT NULL,
				comparator TEXT NOT NULL,
				target_count INTEGER NOT NULL,
				priority TEXT NOT NULL,
				reason TEXT,
				created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
				FOREIGN KEY (period_id) REFERENCES schedule_periods(id) ON DELETE CASCADE,
				FOREIGN KEY (resident_id) REFERENCES residents(id) ON DELETE CASCADE
			);

			CREATE TABLE IF NOT EXISTS assignments (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				period_id INTEGER NOT NULL,
				work_date TEXT NOT NULL,
				resident_id INTEGER NOT NULL,
				source TEXT NOT NULL DEFAULT 'solver',
				is_locked INTEGER NOT NULL DEFAULT 0,
				google_event_id TEXT,
				created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
				updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
				FOREIGN KEY (period_id) REFERENCES schedule_periods(id) ON DELETE CASCADE,
				FOREIGN KEY (resident_id) REFERENCES residents(id) ON DELETE CASCADE,
				UNIQUE(period_id, work_date, resident_id)
			);

			CREATE TABLE IF NOT EXISTS schedule_runs (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				period_id INTEGER NOT NULL,
				run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
				solver_status TEXT NOT NULL,
				objective_score REAL,
				warnings_json TEXT,
				FOREIGN KEY (period_id) REFERENCES schedule_periods(id) ON DELETE CASCADE
			);

			CREATE INDEX IF NOT EXISTS idx_schedule_requests_period_dates ON schedule_requests(period_id, start_date, end_date);
			CREATE INDEX IF NOT EXISTS idx_schedule_rules_period ON schedule_rules(period_id);
			CREATE INDEX IF NOT EXISTS idx_assignments_period_date ON assignments(period_id, work_date);
			"""
		)
		seed_calendar_months(conn)


def seed_calendar_months(conn: sqlite3.Connection, start_year: int | None = None, years: int = 10) -> None:
	start = start_year or date.today().year
	rows = []
	for year in range(start, start + years):
		for month in range(1, 13):
			start_date = date(year, month, 1)
			rows.append(
				(
					year,
					month,
					f"{year}-{month:02d}",
					start_date.isoformat(),
					f"{calendar.month_name[month]} {year}",
				)
			)
	conn.executemany(
		"""
		INSERT OR IGNORE INTO calendar_months (year, month, month_key, start_date, display_name)
		VALUES (?, ?, ?, ?, ?)
		""",
		rows,
	)


def _prepare_schema(conn: sqlite3.Connection) -> None:
	conn.execute("DROP TABLE IF EXISTS availability")
	conn.execute("DROP TABLE IF EXISTS locked_assignments")
	_prepare_resident_schema(conn)

	if not _table_exists(conn, "schedule_periods"):
		return

	columns = {row["name"] for row in conn.execute("PRAGMA table_info(schedule_periods)").fetchall()}
	indexes = conn.execute("PRAGMA index_list(schedule_periods)").fetchall()
	has_unique_period_index = any(int(row["unique"]) == 1 for row in indexes)
	if "draft_name" in columns and not has_unique_period_index:
		_repair_schedule_period_foreign_keys(conn)
		return

	conn.execute("PRAGMA foreign_keys = OFF")
	conn.execute("ALTER TABLE schedule_periods RENAME TO schedule_periods_old")
	conn.execute(
		"""
		CREATE TABLE schedule_periods (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			year INTEGER NOT NULL,
			month INTEGER NOT NULL,
			draft_name TEXT NOT NULL DEFAULT 'Draft 1',
			required_count INTEGER NOT NULL DEFAULT 1,
			status TEXT NOT NULL DEFAULT 'draft',
			google_calendar_id TEXT,
			created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
		)
		"""
	)
	old_columns = {row["name"] for row in conn.execute("PRAGMA table_info(schedule_periods_old)").fetchall()}
	if {"id", "year", "month"}.issubset(old_columns):
		draft_expr = "COALESCE(NULLIF(draft_name, ''), 'Draft 1')" if "draft_name" in old_columns else "'Draft 1'"
		required_expr = "COALESCE(required_count, 1)" if "required_count" in old_columns else "1"
		status_expr = "COALESCE(status, 'draft')" if "status" in old_columns else "'draft'"
		calendar_expr = "google_calendar_id" if "google_calendar_id" in old_columns else "NULL"
		created_expr = "COALESCE(created_at, CURRENT_TIMESTAMP)" if "created_at" in old_columns else "CURRENT_TIMESTAMP"
		conn.execute(
			f"""
			INSERT INTO schedule_periods (id, year, month, draft_name, required_count, status, google_calendar_id, created_at)
			SELECT id, year, month, {draft_expr}, {required_expr}, {status_expr}, {calendar_expr}, {created_expr}
			FROM schedule_periods_old
			"""
		)
	conn.execute("DROP TABLE schedule_periods_old")
	conn.execute("PRAGMA foreign_keys = ON")
	_repair_schedule_period_foreign_keys(conn)


def _repair_schedule_period_foreign_keys(conn: sqlite3.Connection) -> None:
	if _table_references(conn, "assignments", "schedule_periods_old"):
		conn.execute("PRAGMA foreign_keys = OFF")
		conn.execute("DROP INDEX IF EXISTS idx_assignments_period_date")
		conn.execute("ALTER TABLE assignments RENAME TO assignments_old")
		conn.execute(
			"""
			CREATE TABLE assignments (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				period_id INTEGER NOT NULL,
				work_date TEXT NOT NULL,
				resident_id INTEGER NOT NULL,
				source TEXT NOT NULL DEFAULT 'solver',
				is_locked INTEGER NOT NULL DEFAULT 0,
				google_event_id TEXT,
				created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
				updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
				FOREIGN KEY (period_id) REFERENCES schedule_periods(id) ON DELETE CASCADE,
				FOREIGN KEY (resident_id) REFERENCES residents(id) ON DELETE CASCADE,
				UNIQUE(period_id, work_date, resident_id)
			)
			"""
		)
		conn.execute(
			"""
			INSERT INTO assignments (
				id, period_id, work_date, resident_id, source, is_locked, google_event_id, created_at, updated_at
			)
			SELECT id, period_id, work_date, resident_id, source, is_locked, google_event_id, created_at, updated_at
			FROM assignments_old
			"""
		)
		conn.execute("DROP TABLE assignments_old")
		conn.execute("PRAGMA foreign_keys = ON")

	if _table_references(conn, "schedule_runs", "schedule_periods_old"):
		conn.execute("PRAGMA foreign_keys = OFF")
		conn.execute("ALTER TABLE schedule_runs RENAME TO schedule_runs_old")
		conn.execute(
			"""
			CREATE TABLE schedule_runs (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				period_id INTEGER NOT NULL,
				run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
				solver_status TEXT NOT NULL,
				objective_score REAL,
				warnings_json TEXT,
				FOREIGN KEY (period_id) REFERENCES schedule_periods(id) ON DELETE CASCADE
			)
			"""
		)
		conn.execute(
			"""
			INSERT INTO schedule_runs (id, period_id, run_at, solver_status, objective_score, warnings_json)
			SELECT id, period_id, run_at, solver_status, objective_score, warnings_json
			FROM schedule_runs_old
			"""
		)
		conn.execute("DROP TABLE schedule_runs_old")
		conn.execute("PRAGMA foreign_keys = ON")


def _table_references(conn: sqlite3.Connection, table_name: str, referenced_table: str) -> bool:
	row = conn.execute(
		"SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
		(table_name,),
	).fetchone()
	return bool(row and referenced_table in str(row["sql"]))


def _prepare_resident_schema(conn: sqlite3.Connection) -> None:
	if not _table_exists(conn, "residents"):
		return
	columns = {row["name"] for row in conn.execute("PRAGMA table_info(residents)").fetchall()}
	if "color" not in columns:
		conn.execute("ALTER TABLE residents ADD COLUMN color TEXT")
	_backfill_resident_colors(conn)


def _backfill_resident_colors(conn: sqlite3.Connection) -> None:
	used_colors = {
		str(row["color"]).upper()
		for row in conn.execute("SELECT color FROM residents WHERE color IS NOT NULL AND color != ''").fetchall()
	}
	rows = conn.execute(
		"""
		SELECT id
		FROM residents
		WHERE color IS NULL OR color = ''
		ORDER BY id
		"""
	).fetchall()
	for row in rows:
		resident_id = int(row["id"])
		color = _next_resident_color(used_colors, resident_id)
		conn.execute("UPDATE residents SET color = ? WHERE id = ?", (color, resident_id))
		used_colors.add(color)


def _next_resident_color(used_colors: set[str], resident_id: int) -> str:
	if len(used_colors) >= len(RESIDENT_COLOR_PALETTE):
		raise ValueError("No unused resident colors remain in the configured palette.")
	start = (resident_id - 1) % len(RESIDENT_COLOR_PALETTE)
	for offset in range(len(RESIDENT_COLOR_PALETTE)):
		color = RESIDENT_COLOR_PALETTE[(start + offset) % len(RESIDENT_COLOR_PALETTE)]
		if color not in used_colors:
			return color
	raise ValueError("No unused resident colors remain in the configured palette.")


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
	return (
		conn.execute(
			"SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
			(table_name,),
		).fetchone()
		is not None
	)
