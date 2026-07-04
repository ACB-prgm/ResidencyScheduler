from __future__ import annotations
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet

from residency_scheduler import auth
from residency_scheduler.db import get_connection, init_db


def test_google_config_loads_from_streamlit_style_secrets(monkeypatch):
	monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
	monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
	monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
	monkeypatch.setattr(
		auth,
		"_streamlit_google_config",
		lambda: {
			"client_id": "client-id",
			"client_secret": "client-secret",
			"redirect_uri": "https://example.streamlit.app",
		},
	)
	monkeypatch.setattr(auth, "_current_app_root_url", lambda: None)

	config = auth.load_google_oauth_config()

	assert config is not None
	assert config.client_id == "client-id"
	assert config.client_secret == "client-secret"
	assert config.redirect_uri == "https://example.streamlit.app"


def test_localhost_runtime_redirect_overrides_deployed_secret(monkeypatch):
	monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
	monkeypatch.setattr(
		auth,
		"_streamlit_google_config",
		lambda: {
			"client_id": "secret-client-id",
			"client_secret": "secret-client-secret",
			"redirect_uri": "https://HuntingtonHealthResidencyScheduler.streamlit.app",
		},
	)
	monkeypatch.setattr(auth, "_current_app_root_url", lambda: "http://localhost:8501")

	config = auth.load_google_oauth_config()

	assert config is not None
	assert config.redirect_uri == "http://localhost:8501"


def test_explicit_google_redirect_env_wins_over_localhost(monkeypatch):
	monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:9999/")
	monkeypatch.setattr(
		auth,
		"_streamlit_google_config",
		lambda: {
			"client_id": "secret-client-id",
			"client_secret": "secret-client-secret",
			"redirect_uri": "https://HuntingtonHealthResidencyScheduler.streamlit.app",
		},
	)
	monkeypatch.setattr(auth, "_current_app_root_url", lambda: "http://localhost:8501")

	config = auth.load_google_oauth_config()

	assert config is not None
	assert config.redirect_uri == "http://localhost:9999/"


def test_current_app_root_url_drops_page_path_and_query(monkeypatch):
	class Context:
		url = "http://localhost:8501/Generate_Schedule?month=2026-06"

	class Streamlit:
		context = Context()

	monkeypatch.setattr(auth, "st", Streamlit())

	assert auth._current_app_root_url() == "http://localhost:8501"


def test_access_control_permits_admin_without_residents(isolated_auth_db):
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
	)

	assert auth.is_user_allowed({"email": "AaronBastian31@gmail.com"}, config)


def test_access_control_permits_resident_email_case_insensitive(isolated_auth_db):
	_insert_resident_email("Ada@Example.com", active=1)
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
	)

	assert auth.is_user_allowed({"email": "ada@example.COM"}, config)


def test_access_control_permits_inactive_resident_email(isolated_auth_db):
	_insert_resident_email("inactive@example.com", active=0)
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
	)

	assert auth.is_user_allowed({"email": "inactive@example.com"}, config)


def test_access_control_rejects_non_resident_email(isolated_auth_db):
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
	)

	assert not auth.is_user_allowed({"email": "blocked@example.net"}, config)


def test_access_control_rejects_blank_email(isolated_auth_db):
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
	)

	assert not auth.is_user_allowed({"email": ""}, config)
	assert not auth.is_user_allowed({}, config)


def test_signed_oauth_state_validates_without_session_state():
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
	)
	now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
	state = auth._build_oauth_state(config, now=now)

	assert auth._oauth_state_matches(state, None, config, now=now + timedelta(minutes=1))


def test_signed_oauth_state_rejects_tampering():
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
	)
	now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
	state = auth._build_oauth_state(config, now=now)
	body, signature = state.split(".", maxsplit=1)

	assert not auth._oauth_state_matches(f"{body}x.{signature}", None, config, now=now)


def test_signed_oauth_state_rejects_expired_state():
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
	)
	now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
	state = auth._build_oauth_state(config, now=now)

	assert not auth._oauth_state_matches(state, None, config, now=now + auth.OAUTH_STATE_TTL + timedelta(seconds=1))


def test_oauth_flow_disables_pkce_code_verifier():
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
	)

	flow = auth._oauth_flow(config)

	assert flow.autogenerate_code_verifier is False
	assert flow.code_verifier is None


def test_authorization_url_does_not_include_pkce_code_challenge(monkeypatch):
	stub = AuthStreamlitStub()
	monkeypatch.setattr(auth, "st", stub)
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
	)

	auth_url = auth.build_authorization_url(config)
	query = parse_qs(urlparse(auth_url).query)
	requested_scopes = set(query["scope"][0].split())

	assert "code_challenge" not in auth_url
	assert auth.OAUTH_STATE_KEY in stub.session_state
	for scope in auth.GOOGLE_CALENDAR_SCOPES:
		assert scope in requested_scopes


def test_identity_only_session_is_not_valid_after_calendar_publish_phase():
	expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
	session = {
		"google_sub": "sub",
		"email": "user@example.org",
		"profile": {"sub": "sub"},
		"scopes": ["openid", "email", "profile"],
		"expires_at": expires_at.isoformat(),
	}

	assert not auth._session_auth_is_valid(session)


def test_session_with_calendar_scopes_is_valid():
	expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
	session = {
		"google_sub": "sub",
		"email": "user@example.org",
		"profile": {"sub": "sub"},
		"scopes": ["openid", "email", "profile", *auth.GOOGLE_CALENDAR_SCOPES],
		"expires_at": expires_at.isoformat(),
	}

	assert auth._session_auth_is_valid(session)


def test_relaxed_oauthlib_token_scope_sets_and_restores_env(monkeypatch):
	monkeypatch.delenv("OAUTHLIB_RELAX_TOKEN_SCOPE", raising=False)

	with auth._relaxed_oauthlib_token_scope():
		assert auth.os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] == "1"

	assert "OAUTHLIB_RELAX_TOKEN_SCOPE" not in auth.os.environ


def test_relaxed_oauthlib_token_scope_restores_existing_env(monkeypatch):
	monkeypatch.setenv("OAUTHLIB_RELAX_TOKEN_SCOPE", "existing")

	with auth._relaxed_oauthlib_token_scope():
		assert auth.os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] == "1"

	assert auth.os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] == "existing"


def test_streamlit_oauth_redirect_uri_uses_component_callback():
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="https://huntingtonhealthresidencyscheduler.streamlit.app/",
	)

	assert (
		auth._streamlit_oauth_redirect_uri(config)
		== "https://huntingtonhealthresidencyscheduler.streamlit.app/component/streamlit_oauth.authorize_button"
	)


def test_streamlit_oauth_button_uses_google_component_flow(monkeypatch):
	stub = AuthStreamlitStub()
	monkeypatch.setattr(auth, "st", stub)
	component = OAuth2ComponentStub(result=None)
	monkeypatch.setattr(auth, "OAuth2Component", component.factory)
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
	)

	auth._render_streamlit_oauth_button(config)

	assert component.init_kwargs["authorize_endpoint"] == auth.GOOGLE_AUTHORIZE_ENDPOINT
	assert component.init_kwargs["token_endpoint"] == auth.GOOGLE_TOKEN_ENDPOINT
	assert component.init_kwargs["token_endpoint_auth_method"] == "client_secret_post"
	assert component.authorize_kwargs["redirect_uri"] == "http://localhost:8501/component/streamlit_oauth.authorize_button"
	assert component.authorize_kwargs["scope"] == " ".join(auth.GOOGLE_REQUIRED_SCOPES)
	assert component.authorize_kwargs["extras_params"] == {
		"access_type": "offline",
		"include_granted_scopes": "true",
		"prompt": "consent select_account",
	}


def test_streamlit_oauth_token_converts_to_google_credentials_payload():
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
	)
	token = {
		"access_token": "access-token",
		"refresh_token": "refresh-token",
		"id_token": "identity-token",
		"scope": "openid email profile https://www.googleapis.com/auth/calendar.events",
		"expires_at": 1783000000,
	}

	payload = auth._streamlit_oauth_token_to_credentials_payload(token, config)

	assert payload["token"] == "access-token"
	assert payload["refresh_token"] == "refresh-token"
	assert payload["id_token"] == "identity-token"
	assert payload["token_uri"] == auth.GOOGLE_TOKEN_ENDPOINT
	assert payload["client_id"] == "client"
	assert payload["client_secret"] == "secret"
	assert "https://www.googleapis.com/auth/calendar.events" in payload["scopes"]
	assert payload["expiry"] == datetime.fromtimestamp(1783000000, tz=timezone.utc).isoformat()


def test_token_encryption_round_trips_without_plaintext():
	key = Fernet.generate_key().decode("utf-8")
	payload = {"token": "access-token", "refresh_token": "refresh-token", "scopes": ["openid"]}

	encrypted = auth.encrypt_token_payload(payload, key)

	assert "access-token" not in encrypted
	assert "refresh-token" not in encrypted
	assert auth.decrypt_token_payload(encrypted, key) == payload


def test_token_encryption_derives_key_from_plain_secret():
	payload = {"token": "access-token", "refresh_token": "refresh-token", "scopes": ["openid"]}

	encrypted = auth.encrypt_token_payload(payload, "plain-client-secret")

	assert "access-token" not in encrypted
	assert auth.decrypt_token_payload(encrypted, "plain-client-secret") == payload


def test_persist_authenticated_user_stores_encrypted_token(isolated_auth_db):
	key = Fernet.generate_key().decode("utf-8")
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
		token_encryption_key=key,
	)
	profile = {"sub": "google-sub-1", "email": "user@example.org", "name": "Test User", "picture": "https://example.org/me.png"}
	token_payload = {"token": "access-token", "refresh_token": "refresh-token", "scopes": ["openid", "email"]}

	auth.persist_authenticated_user(profile, token_payload, config, allowed=True)

	with get_connection() as conn:
		user = conn.execute("SELECT * FROM app_users WHERE google_sub = ?", ("google-sub-1",)).fetchone()
		token = conn.execute("SELECT * FROM google_oauth_tokens WHERE google_sub = ?", ("google-sub-1",)).fetchone()

	assert user["email"] == "user@example.org"
	assert int(user["allowed"]) == 1
	assert "access-token" not in token["encrypted_token_json"]
	assert auth.decrypt_token_payload(token["encrypted_token_json"], key) == token_payload


def test_persist_authenticated_user_uses_client_secret_when_no_token_key(isolated_auth_db):
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="plain-client-secret",
		redirect_uri="http://localhost:8501",
	)
	profile = {"sub": "google-sub-1", "email": "user@example.org", "name": "Test User", "picture": ""}
	token_payload = {"token": "access-token", "refresh_token": "refresh-token", "scopes": ["openid", "email"]}

	auth.persist_authenticated_user(profile, token_payload, config, allowed=True)

	with get_connection() as conn:
		token = conn.execute("SELECT * FROM google_oauth_tokens WHERE google_sub = ?", ("google-sub-1",)).fetchone()

	assert token is not None
	assert "access-token" not in token["encrypted_token_json"]
	assert auth.decrypt_token_payload(token["encrypted_token_json"], "plain-client-secret") == token_payload


def test_create_remembered_session_stores_hashed_cookie_only(isolated_auth_db, monkeypatch):
	key = Fernet.generate_key().decode("utf-8")
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
		token_encryption_key=key,
	)
	stub = AuthStreamlitStub()
	monkeypatch.setattr(auth, "st", stub)
	monkeypatch.setattr(auth.secrets, "token_urlsafe", lambda _size: "remember-cookie-value")
	profile = {"sub": "google-sub-1", "email": "user@example.org", "name": "Test User", "picture": ""}
	token_payload = {
		"token": "access-token",
		"refresh_token": "refresh-token",
		"expiry": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
		"scopes": ["openid", "email", "profile", *auth.GOOGLE_CALENDAR_SCOPES],
	}
	auth.persist_authenticated_user(profile, token_payload, config, allowed=True)

	auth._create_remembered_session("google-sub-1", config)

	with get_connection() as conn:
		row = conn.execute("SELECT * FROM google_auth_sessions").fetchone()

	assert row is not None
	assert row["session_hash"] != "remember-cookie-value"
	assert row["google_sub"] == "google-sub-1"
	assert stub.session_state[auth.PENDING_REMEMBER_COOKIE_KEY]["value"] == "remember-cookie-value"


def test_create_remembered_session_works_without_explicit_token_key(isolated_auth_db, monkeypatch):
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="plain-client-secret",
		redirect_uri="http://localhost:8501",
	)
	stub = AuthStreamlitStub()
	monkeypatch.setattr(auth, "st", stub)
	monkeypatch.setattr(auth.secrets, "token_urlsafe", lambda _size: "remember-cookie-value")
	profile = {"sub": "google-sub-1", "email": "user@example.org", "name": "Test User", "picture": ""}
	token_payload = {
		"token": "access-token",
		"refresh_token": "refresh-token",
		"expiry": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
		"scopes": ["openid", "email", "profile", *auth.GOOGLE_CALENDAR_SCOPES],
	}
	auth.persist_authenticated_user(profile, token_payload, config, allowed=True)

	auth._create_remembered_session("google-sub-1", config)

	with get_connection() as conn:
		row = conn.execute("SELECT * FROM google_auth_sessions").fetchone()

	assert row is not None
	assert stub.session_state[auth.PENDING_REMEMBER_COOKIE_KEY]["value"] == "remember-cookie-value"


def test_restore_session_from_cookie_uses_encrypted_stored_token(isolated_auth_db, monkeypatch):
	_insert_resident_email("user@example.org")
	key = Fernet.generate_key().decode("utf-8")
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
		token_encryption_key=key,
	)
	stub = AuthStreamlitStub()
	monkeypatch.setattr(auth, "st", stub)
	monkeypatch.setattr(auth.secrets, "token_urlsafe", lambda _size: "remember-cookie-value")
	profile = {"sub": "google-sub-1", "email": "user@example.org", "name": "Test User", "picture": ""}
	token_payload = {
		"token": "access-token",
		"refresh_token": "refresh-token",
		"expiry": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
		"scopes": ["openid", "email", "profile", *auth.GOOGLE_CALENDAR_SCOPES],
	}
	auth.persist_authenticated_user(profile, token_payload, config, allowed=True)
	auth._create_remembered_session("google-sub-1", config)
	monkeypatch.setattr(auth, "_remember_cookie_value", lambda: "remember-cookie-value")

	restored = auth._restore_session_from_cookie(config)

	assert restored is not None
	assert restored["google_sub"] == "google-sub-1"
	assert restored["email"] == "user@example.org"
	assert restored["token"]["token"] == "access-token"


def test_restore_session_from_cookie_attempts_refresh_for_expired_token(isolated_auth_db, monkeypatch):
	_insert_resident_email("user@example.org")
	key = Fernet.generate_key().decode("utf-8")
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
		token_encryption_key=key,
	)
	stub = AuthStreamlitStub()
	monkeypatch.setattr(auth, "st", stub)
	monkeypatch.setattr(auth.secrets, "token_urlsafe", lambda _size: "remember-cookie-value")
	monkeypatch.setattr(auth, "load_google_oauth_config", lambda: config)
	profile = {"sub": "google-sub-1", "email": "user@example.org", "name": "Test User", "picture": ""}
	token_payload = {
		"token": "expired-access-token",
		"refresh_token": "refresh-token",
		"expiry": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
		"scopes": ["openid", "email", "profile", *auth.GOOGLE_CALENDAR_SCOPES],
	}
	auth.persist_authenticated_user(profile, token_payload, config, allowed=True)
	auth._create_remembered_session("google-sub-1", config)
	monkeypatch.setattr(auth, "_remember_cookie_value", lambda: "remember-cookie-value")

	def fake_refresh(session):
		stub.session_state[auth.AUTH_SESSION_KEY] = {
			"google_sub": session["profile"]["sub"],
			"email": session["profile"]["email"],
			"profile": session["profile"],
			"token": {"token": "refreshed-access-token", "scopes": ["openid", "email", "profile", *auth.GOOGLE_CALENDAR_SCOPES]},
			"scopes": ["openid", "email", "profile", *auth.GOOGLE_CALENDAR_SCOPES],
			"expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
		}
		return True

	monkeypatch.setattr(auth, "_refresh_session_if_possible", fake_refresh)

	restored = auth._restore_session_from_cookie(config)

	assert restored is not None
	assert restored["token"]["token"] == "refreshed-access-token"


def test_restore_session_from_cookie_rechecks_current_resident_access(isolated_auth_db, monkeypatch):
	key = Fernet.generate_key().decode("utf-8")
	config = auth.GoogleOAuthConfig(
		client_id="client",
		client_secret="secret",
		redirect_uri="http://localhost:8501",
		token_encryption_key=key,
	)
	stub = AuthStreamlitStub()
	monkeypatch.setattr(auth, "st", stub)
	monkeypatch.setattr(auth.secrets, "token_urlsafe", lambda _size: "remember-cookie-value")
	profile = {"sub": "google-sub-1", "email": "removed@example.org", "name": "Removed User", "picture": ""}
	token_payload = {
		"token": "access-token",
		"refresh_token": "refresh-token",
		"expiry": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
		"scopes": ["openid", "email", "profile", *auth.GOOGLE_CALENDAR_SCOPES],
	}
	auth.persist_authenticated_user(profile, token_payload, config, allowed=True)
	auth._create_remembered_session("google-sub-1", config)
	monkeypatch.setattr(auth, "_remember_cookie_value", lambda: "remember-cookie-value")

	restored = auth._restore_session_from_cookie(config)

	assert restored is None
	with get_connection() as conn:
		assert conn.execute("SELECT COUNT(*) AS count FROM google_auth_sessions").fetchone()["count"] == 0


def test_session_auth_valid_uses_local_session_payload_without_storage():
	expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
	session = {
		"google_sub": "sub",
		"email": "user@example.org",
		"profile": {"sub": "sub"},
		"scopes": ["openid", "email", "profile", *auth.GOOGLE_CALENDAR_SCOPES],
		"expires_at": expires_at.isoformat(),
	}

	assert auth._session_auth_is_valid(session)


def test_session_auth_invalid_when_expiring_soon():
	expires_at = datetime.now(timezone.utc) + timedelta(minutes=2)
	session = {
		"google_sub": "sub",
		"email": "user@example.org",
		"profile": {"sub": "sub"},
		"scopes": ["openid", "email", "profile", *auth.GOOGLE_CALENDAR_SCOPES],
		"expires_at": expires_at.isoformat(),
	}

	assert not auth._session_auth_is_valid(session)


def test_unauthenticated_gate_stops_before_scheduler_content(monkeypatch):
	stub = AuthStreamlitStub()
	monkeypatch.setattr(auth, "st", stub)
	monkeypatch.setattr(auth, "load_google_oauth_config", lambda: None)

	try:
		auth.require_google_auth()
	except StopException:
		pass
	else:
		raise AssertionError("require_google_auth should stop unauthenticated execution")

	assert stub.stopped
	assert stub.images
	assert "Google OAuth is not configured" in stub.errors[0]


class StopException(Exception):
	pass


class OAuth2ComponentStub:
	def __init__(self, result):
		self.result = result
		self.init_args = None
		self.init_kwargs = None
		self.authorize_kwargs = None

	def factory(self, *args, **kwargs):
		self.init_args = args
		self.init_kwargs = kwargs
		return self

	def authorize_button(self, name, redirect_uri, scope, **kwargs):
		self.authorize_kwargs = {"name": name, "redirect_uri": redirect_uri, "scope": scope, **kwargs}
		return self.result


def _insert_resident_email(email: str, active: int = 1) -> None:
	with get_connection() as conn:
		conn.execute(
			"""
			INSERT INTO residents (name, email, max_shifts, min_shifts, weight, active)
			VALUES (?, ?, ?, ?, ?, ?)
			""",
			(f"Resident {email}", email, 10, None, 1, active),
		)


class AuthStreamlitStub:
	def __init__(self):
		self.session_state = {}
		self.query_params = {}
		self.errors: list[str] = []
		self.markdowns: list[str] = []
		self.markdown_unsafe_flags: list[bool] = []
		self.htmls: list[str] = []
		self.images: list[str] = []
		self.link_buttons: list[dict] = []
		self.stopped = False
		self.sidebar = self

	def __enter__(self):
		return self

	def __exit__(self, exc_type, exc, traceback):
		return False

	def title(self, _value):
		return None

	def caption(self, _value):
		return None

	def error(self, value):
		self.errors.append(value)

	def markdown(self, value, unsafe_allow_html=False):
		self.markdowns.append(value)
		self.markdown_unsafe_flags.append(unsafe_allow_html)

	def html(self, value):
		self.htmls.append(value)

	def image(self, value, **_kwargs):
		self.images.append(value)

	def link_button(self, label, url, **kwargs):
		self.link_buttons.append({"label": label, "url": url, "type": kwargs.get("type")})
		return None

	def button(self, _label, **_kwargs):
		return False

	def stop(self):
		self.stopped = True
		raise StopException


@pytest.fixture
def isolated_auth_db(tmp_path, monkeypatch):
	monkeypatch.setenv("RESIDENCY_SCHEDULER_DB", str(tmp_path / "auth.sqlite"))
	init_db()
