#!/usr/bin/env bash
set -euo pipefail

python scripts/init_db.py
streamlit run app.py
