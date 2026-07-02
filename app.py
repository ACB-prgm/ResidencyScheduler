from __future__ import annotations

import streamlit as st

from residency_scheduler.auth import require_google_auth

st.set_page_config(
	page_title="Residency Scheduler",
	page_icon="📅",
	layout="wide",
)

require_google_auth(render_sidebar=False)

navigation = st.navigation(
	[
		st.Page("pages/0_Home.py", title="Home"),
		st.Page("pages/1_Residents.py", title="Residents"),
		st.Page("pages/2_Availability_and_Preferences.py", title="Availability and Preferences"),
		st.Page("pages/3_Scheduling_Rules.py", title="Scheduling Rules"),
		st.Page("pages/4_Generate_Schedule.py", title="Generate Schedule"),
	]
)
navigation.run()
