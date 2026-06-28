from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
	sys.path.insert(0, str(ROOT))

from residency_scheduler.db import DB_PATH, init_db


if __name__ == "__main__":
	init_db()
	print(f"Initialized database at {DB_PATH}")
