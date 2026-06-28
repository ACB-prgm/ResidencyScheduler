from __future__ import annotations

import os
import sqlite3
from pathlib import Path

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
		conn.executescript(
			"""
			CREATE TABLE IF NOT EXISTS residents (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				name TEXT NOT NULL,
				email TEXT,
				max_shifts INTEGER,
				min_shifts INTEGER,
				weight REAL NOT NULL DEFAULT 1.0,
				active INTEGER NOT NULL DEFAULT 1,
				created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
				updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
			);

			CREATE TABLE IF NOT EXISTS schedule_periods (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				year INTEGER NOT NULL,
				month INTEGER NOT NULL,
				required_count INTEGER NOT NULL DEFAULT 1,
				status TEXT NOT NULL DEFAULT 'draft',
				google_calendar_id TEXT,
				created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
				UNIQUE(year, month)
			);

			CREATE TABLE IF NOT EXISTS availability (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				period_id INTEGER NOT NULL,
				resident_id INTEGER NOT NULL,
				work_date TEXT NOT NULL,
				availability_type TEXT NOT NULL,
				priority TEXT NOT NULL,
				reason TEXT,
				created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
				FOREIGN KEY (period_id) REFERENCES schedule_periods(id) ON DELETE CASCADE,
				FOREIGN KEY (resident_id) REFERENCES residents(id) ON DELETE CASCADE
			);

			CREATE TABLE IF NOT EXISTS locked_assignments (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				period_id INTEGER NOT NULL,
				work_date TEXT NOT NULL,
				resident_id INTEGER NOT NULL,
				reason TEXT,
				created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
				FOREIGN KEY (period_id) REFERENCES schedule_periods(id) ON DELETE CASCADE,
				FOREIGN KEY (resident_id) REFERENCES residents(id) ON DELETE CASCADE,
				UNIQUE(period_id, work_date, resident_id)
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

			CREATE INDEX IF NOT EXISTS idx_availability_period_date ON availability(period_id, work_date);
			CREATE INDEX IF NOT EXISTS idx_locked_period_date ON locked_assignments(period_id, work_date);
			CREATE INDEX IF NOT EXISTS idx_assignments_period_date ON assignments(period_id, work_date);
			"""
		)
