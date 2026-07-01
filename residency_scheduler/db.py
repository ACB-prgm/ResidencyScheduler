from __future__ import annotations

import calendar
import os
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError

from residency_scheduler.colors import RESIDENT_COLOR_PALETTE

DB_DIR = Path("data")
DB_PATH = DB_DIR / "residency_scheduler.sqlite"
CACHE_DB_PATH = DB_DIR / "residency_scheduler_cache.sqlite"

_ENGINE: Engine | None = None
_ENGINE_URL: str | None = None


class ResultRow(dict):
	def __getitem__(self, key):
		if isinstance(key, int):
			return list(self.values())[key]
		return super().__getitem__(key)


class ResultCursor:
	def __init__(self, cursor):
		self._cursor = cursor

	@property
	def rowcount(self) -> int:
		return int(getattr(self._cursor, "rowcount", -1))

	@property
	def lastrowid(self) -> int | None:
		return getattr(self._cursor, "lastrowid", None)

	def fetchone(self):
		row = self._cursor.fetchone()
		if row is None:
			return None
		return self._coerce_row(row)

	def fetchall(self) -> list[ResultRow]:
		return [self._coerce_row(row) for row in self._cursor.fetchall()]

	def _coerce_row(self, row) -> ResultRow:
		if isinstance(row, dict):
			return ResultRow(row)
		columns = [column[0] for column in self._cursor.description or []]
		return ResultRow(zip(columns, row))


class AppConnection:
	def __init__(self):
		self._engine = get_engine()
		self._raw = self._engine.raw_connection()
		self.dialect = self._engine.dialect.name
		if self.dialect == "sqlite":
			self.execute("PRAGMA foreign_keys = ON")

	def __enter__(self) -> AppConnection:
		return self

	def __exit__(self, exc_type, exc, traceback) -> None:
		try:
			try:
				if exc_type is None:
					self._raw.commit()
				else:
					self._raw.rollback()
			except Exception:
				reset_engine()
				if exc_type is None:
					raise
		finally:
			try:
				self._raw.close()
			except Exception:
				reset_engine()

	def execute(self, query: str, params: tuple | list | None = None) -> ResultCursor:
		cursor = self._raw.cursor()
		cursor.execute(self._translate_query(query), tuple(params or ()))
		return ResultCursor(cursor)

	def executemany(self, query: str, rows: list[tuple] | tuple[tuple, ...]) -> ResultCursor:
		cursor = self._raw.cursor()
		cursor.executemany(self._translate_query(query), rows)
		return ResultCursor(cursor)

	def run_script(self, script: str) -> None:
		for statement in _split_sql_script(script):
			self.execute(statement)

	def _translate_query(self, query: str) -> str:
		if self.dialect in {"postgresql", "postgres"}:
			return query.replace("?", "%s")
		return query


def get_db_path() -> Path:
	"""Return the active local SQLite database path."""
	return Path(os.environ.get("RESIDENCY_SCHEDULER_DB", str(DB_PATH)))


def get_cache_db_path() -> Path:
	"""Return the local SQLite cache database path.

	This cache is never the durable source of truth when Neon/Postgres is
	configured; it only stores read-through data to reduce repeated remote reads.
	"""
	return Path(os.environ.get("RESIDENCY_SCHEDULER_CACHE_DB", str(CACHE_DB_PATH)))


def get_database_url() -> str:
	if os.environ.get("RESIDENCY_SCHEDULER_DB"):
		db_path = get_db_path()
		db_path.parent.mkdir(parents=True, exist_ok=True)
		return f"sqlite:///{db_path}"

	for key in ("RESIDENCY_SCHEDULER_DATABASE_URL", "DATABASE_URL", "NEON_DATABASE_URL"):
		value = os.environ.get(key)
		if value:
			return value

	secret_url = _streamlit_secret_database_url()
	if secret_url:
		return secret_url

	db_path = get_db_path()
	db_path.parent.mkdir(parents=True, exist_ok=True)
	return f"sqlite:///{db_path}"


def primary_database_is_remote() -> bool:
	url = _normalize_database_url(get_database_url())
	return url.startswith(("postgresql", "postgres"))


def get_engine() -> Engine:
	global _ENGINE, _ENGINE_URL
	url = _normalize_database_url(get_database_url())
	if _ENGINE is None or _ENGINE_URL != url:
		connect_args: dict[str, Any] = {}
		if url.startswith("sqlite"):
			connect_args["check_same_thread"] = False
		engine_args: dict[str, Any] = {"connect_args": connect_args, "future": True, "pool_pre_ping": True}
		if url.startswith(("postgresql", "postgres")):
			engine_args["pool_recycle"] = 300
		_ENGINE = create_engine(url, **engine_args)
		_ENGINE_URL = url
	return _ENGINE


def _normalize_database_url(url: str) -> str:
	if url.startswith("postgresql://"):
		return url.replace("postgresql://", "postgresql+psycopg://", 1)
	if url.startswith("postgres://"):
		return url.replace("postgres://", "postgresql+psycopg://", 1)
	return url


def reset_engine() -> None:
	global _ENGINE, _ENGINE_URL
	if _ENGINE is not None:
		_ENGINE.dispose()
	_ENGINE = None
	_ENGINE_URL = None


def get_connection() -> AppConnection:
	last_error: Exception | None = None
	for _ in range(2):
		try:
			return AppConnection()
		except (DBAPIError, OperationalError, OSError) as exc:
			last_error = exc
			reset_engine()
	if last_error is not None:
		raise last_error
	return AppConnection()


def init_db() -> None:
	last_error: Exception | None = None
	for _ in range(3):
		try:
			with get_connection() as conn:
				_prepare_schema(conn)
				conn.run_script(_schema_sql(conn.dialect))
				seed_calendar_months(conn)
			return
		except (DBAPIError, OperationalError, OSError) as exc:
			last_error = exc
			reset_engine()
	if last_error is not None:
		raise last_error


def seed_calendar_months(conn: AppConnection, start_year: int | None = None, years: int = 10) -> None:
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
	if conn.dialect == "sqlite":
		conn.executemany(
			"""
			INSERT OR IGNORE INTO calendar_months (year, month, month_key, start_date, display_name)
			VALUES (?, ?, ?, ?, ?)
			""",
			rows,
		)
	else:
		conn.executemany(
			"""
			INSERT INTO calendar_months (year, month, month_key, start_date, display_name)
			VALUES (?, ?, ?, ?, ?)
			ON CONFLICT(month_key) DO NOTHING
			""",
			rows,
		)


def _streamlit_secret_database_url() -> str | None:
	try:
		import streamlit as st

		connections = st.secrets.get("connections", {})
		neon = connections.get("neon", {}) if hasattr(connections, "get") else {}
		value = neon.get("url") if hasattr(neon, "get") else None
		return str(value).strip() if value else None
	except Exception:
		return None


def _schema_sql(dialect: str) -> str:
	id_type = "INTEGER PRIMARY KEY AUTOINCREMENT" if dialect == "sqlite" else "SERIAL PRIMARY KEY"
	return f"""
	CREATE TABLE IF NOT EXISTS calendar_months (
		id {id_type},
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

	CREATE TABLE IF NOT EXISTS app_users (
		google_sub TEXT PRIMARY KEY,
		email TEXT NOT NULL,
		name TEXT,
		picture_url TEXT,
		allowed INTEGER NOT NULL DEFAULT 0,
		created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
		updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
		last_login_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
	);

	CREATE TABLE IF NOT EXISTS google_oauth_tokens (
		id {id_type},
		google_sub TEXT NOT NULL UNIQUE,
		encrypted_token_json TEXT NOT NULL,
		scopes TEXT NOT NULL,
		created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
		updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
		FOREIGN KEY (google_sub) REFERENCES app_users(google_sub) ON DELETE CASCADE
	);

	CREATE TABLE IF NOT EXISTS google_auth_sessions (
		session_hash TEXT PRIMARY KEY,
		google_sub TEXT NOT NULL,
		expires_at TEXT NOT NULL,
		created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
		updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
		last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
		FOREIGN KEY (google_sub) REFERENCES app_users(google_sub) ON DELETE CASCADE
	);

	CREATE TABLE IF NOT EXISTS residents (
		id {id_type},
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
		id {id_type},
		year INTEGER NOT NULL,
		month INTEGER NOT NULL,
		required_count INTEGER NOT NULL DEFAULT 1,
		google_calendar_id TEXT,
		created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
		UNIQUE(year, month)
	);

	CREATE TABLE IF NOT EXISTS schedule_requests (
		id {id_type},
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
		id {id_type},
		period_id INTEGER NOT NULL,
		resident_id INTEGER NOT NULL,
		rule_type TEXT NOT NULL,
		weekday INTEGER NOT NULL,
		paired_weekday INTEGER,
		comparator TEXT NOT NULL,
		target_count INTEGER NOT NULL,
		priority TEXT NOT NULL,
		reason TEXT,
		created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
		FOREIGN KEY (period_id) REFERENCES schedule_periods(id) ON DELETE CASCADE,
		FOREIGN KEY (resident_id) REFERENCES residents(id) ON DELETE CASCADE
	);

	CREATE TABLE IF NOT EXISTS assignments (
		id {id_type},
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
		id {id_type},
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


def _prepare_schema(conn: AppConnection) -> None:
	if conn.dialect != "sqlite":
		return

	conn.execute("DROP TABLE IF EXISTS availability")
	conn.execute("DROP TABLE IF EXISTS locked_assignments")
	_prepare_resident_schema(conn)
	_prepare_schedule_rule_schema(conn)
	_prepare_schedule_period_schema(conn)


def _prepare_schedule_period_schema(conn: AppConnection) -> None:
	if not _table_exists(conn, "schedule_periods"):
		return

	columns = _table_columns(conn, "schedule_periods")
	indexes = conn.execute("PRAGMA index_list(schedule_periods)").fetchall()
	has_unique_period_index = any(int(row["unique"]) == 1 for row in indexes)
	if "draft_name" not in columns and "status" not in columns and has_unique_period_index:
		return

	keeper_ids = _schedule_period_keeper_ids(conn)
	if not keeper_ids:
		keeper_ids = set()
	conn.execute("PRAGMA foreign_keys = OFF")
	for child_table in ["schedule_requests", "schedule_rules", "assignments", "schedule_runs"]:
		if _table_exists(conn, child_table):
			placeholders = ",".join("?" for _ in keeper_ids) or "NULL"
			conn.execute(f"DELETE FROM {child_table} WHERE period_id NOT IN ({placeholders})", tuple(keeper_ids))

	conn.execute(
		"""
		CREATE TABLE schedule_periods_new (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			year INTEGER NOT NULL,
			month INTEGER NOT NULL,
			required_count INTEGER NOT NULL DEFAULT 1,
			google_calendar_id TEXT,
			created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
			UNIQUE(year, month)
		)
		"""
	)
	required_expr = "COALESCE(required_count, 1)" if "required_count" in columns else "1"
	calendar_expr = "google_calendar_id" if "google_calendar_id" in columns else "NULL"
	created_expr = "COALESCE(created_at, CURRENT_TIMESTAMP)" if "created_at" in columns else "CURRENT_TIMESTAMP"
	placeholders = ",".join("?" for _ in keeper_ids) or "NULL"
	conn.execute(
		f"""
		INSERT INTO schedule_periods_new (id, year, month, required_count, google_calendar_id, created_at)
		SELECT id, year, month, {required_expr}, {calendar_expr}, {created_expr}
		FROM schedule_periods
		WHERE id IN ({placeholders})
		""",
		tuple(keeper_ids),
	)
	conn.execute("DROP TABLE schedule_periods")
	conn.execute("ALTER TABLE schedule_periods_new RENAME TO schedule_periods")
	conn.execute("PRAGMA foreign_keys = ON")


def _schedule_period_keeper_ids(conn: AppConnection) -> set[int]:
	rows = conn.execute(
		"""
		SELECT sp.id, sp.year, sp.month,
			CASE WHEN EXISTS (SELECT 1 FROM assignments a WHERE a.period_id = sp.id) THEN 1 ELSE 0 END AS has_assignments
		FROM schedule_periods sp
		ORDER BY sp.year, sp.month, has_assignments DESC, sp.id DESC
		"""
	).fetchall()
	keepers: dict[tuple[int, int], int] = {}
	for row in rows:
		key = (int(row["year"]), int(row["month"]))
		if key not in keepers:
			keepers[key] = int(row["id"])
	return set(keepers.values())


def _prepare_schedule_rule_schema(conn: AppConnection) -> None:
	if not _table_exists(conn, "schedule_rules"):
		return
	if "paired_weekday" not in _table_columns(conn, "schedule_rules"):
		conn.execute("ALTER TABLE schedule_rules ADD COLUMN paired_weekday INTEGER")


def _prepare_resident_schema(conn: AppConnection) -> None:
	if not _table_exists(conn, "residents"):
		return
	if "color" not in _table_columns(conn, "residents"):
		conn.execute("ALTER TABLE residents ADD COLUMN color TEXT")
	_backfill_resident_colors(conn)


def _backfill_resident_colors(conn: AppConnection) -> None:
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


def _table_exists(conn: AppConnection, table_name: str) -> bool:
	return (
		conn.execute(
			"SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
			(table_name,),
		).fetchone()
		is not None
	)


def _table_columns(conn: AppConnection, table_name: str) -> set[str]:
	return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _split_sql_script(script: str) -> list[str]:
	return [statement.strip() for statement in script.split(";") if statement.strip()]
