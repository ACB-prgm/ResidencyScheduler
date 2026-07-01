from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
	sys.path.insert(0, str(ROOT))

from residency_scheduler.db import get_database_url, init_db


if __name__ == "__main__":
	init_db()
	database_url = get_database_url()
	target = "postgres database" if database_url.startswith(("postgresql://", "postgresql+")) else database_url
	print(f"Initialized {target}")
