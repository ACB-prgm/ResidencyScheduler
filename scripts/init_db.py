from __future__ import annotations

from residency_scheduler.db import DB_PATH, init_db


if __name__ == "__main__":
	init_db()
	print(f"Initialized database at {DB_PATH}")
