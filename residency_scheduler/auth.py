from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import hmac
import html
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import streamlit as st
import streamlit.components.v1 as components
from cryptography.fernet import Fernet, InvalidToken
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from streamlit_oauth import OAuth2Component, StreamlitOauthError

from residency_scheduler.db import get_connection, init_db
from residency_scheduler.ui import render_sidebar_logo

GOOGLE_LOGIN_SCOPES = ["openid", "email", "profile"]
GOOGLE_CALENDAR_SCOPES = [
	"https://www.googleapis.com/auth/calendar.calendarlist.readonly",
	"https://www.googleapis.com/auth/calendar.events",
]
GOOGLE_REQUIRED_SCOPES = GOOGLE_LOGIN_SCOPES + GOOGLE_CALENDAR_SCOPES
GOOGLE_AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
ADMIN_EMAIL = "aaronbastian31@gmail.com"
AUTH_SESSION_KEY = "google_auth_session"
OAUTH_STATE_KEY = "google_oauth_state"
AUTH_COOKIE_NAME = "rs_google_auth"
PENDING_REMEMBER_COOKIE_KEY = "pending_google_remember_cookie"
EXPIRY_REFRESH_WINDOW = timedelta(minutes=5)
OAUTH_STATE_TTL = timedelta(hours=1)
REMEMBER_SESSION_TTL = timedelta(days=30)


@dataclass(frozen=True)
class GoogleOAuthConfig:
	client_id: str
	client_secret: str
	redirect_uri: str
	token_encryption_key: str | None = None

	def client_config(self) -> dict[str, Any]:
		return {
			"web": {
				"client_id": self.client_id,
				"client_secret": self.client_secret,
				"auth_uri": "https://accounts.google.com/o/oauth2/auth",
				"token_uri": "https://oauth2.googleapis.com/token",
				"redirect_uris": [self.redirect_uri],
			}
		}


def require_google_auth(render_sidebar: bool = True) -> dict[str, Any]:
	"""Require a valid Google sign-in before rendering app content."""
	config = load_google_oauth_config()
	session = st.session_state.get(AUTH_SESSION_KEY)
	if _session_auth_is_valid(session):
		if not _session_user_is_allowed(session):
			_render_access_denied(str(session.get("email") or ""), config)
		if render_sidebar:
			_render_signed_in_sidebar(session)
		return session

	if session and _refresh_session_if_possible(session):
		if not _session_user_is_allowed(st.session_state[AUTH_SESSION_KEY]):
			_render_access_denied(str(st.session_state[AUTH_SESSION_KEY].get("email") or ""), config)
		if render_sidebar:
			_render_signed_in_sidebar(st.session_state[AUTH_SESSION_KEY])
		return st.session_state[AUTH_SESSION_KEY]

	if config is not None:
		remembered_session = _restore_session_from_cookie(config)
		if remembered_session is not None:
			st.session_state[AUTH_SESSION_KEY] = remembered_session
			if render_sidebar:
				_render_signed_in_sidebar(remembered_session)
			return remembered_session

	_hide_unauthenticated_navigation()
	with st.sidebar:
		render_sidebar_logo(st)
	st.title("Residency Scheduler")
	st.caption("Sign in with Google to continue.")

	if config is None:
		st.error("Google OAuth is not configured. Add Google client credentials before using the app.")
		st.stop()

	# Keep accepting the older root callback while local sessions migrate to the component callback.
	code = _query_param("code")
	state = _query_param("state")
	if code:
		_handle_oauth_callback(code, state, config)
		st.stop()

	_render_streamlit_oauth_button(config)
	st.stop()


def sign_out() -> None:
	config = load_google_oauth_config()
	if config is not None:
		_delete_remembered_session(config)
	_clear_remember_cookie()
	st.session_state.pop(AUTH_SESSION_KEY, None)
	st.session_state.pop(OAUTH_STATE_KEY, None)


def load_google_oauth_config() -> GoogleOAuthConfig | None:
	secret_config = _streamlit_google_config()

	client_id = _first_nonempty(
		secret_config.get("client_id"),
		os.environ.get("GOOGLE_CLIENT_ID"),
	)
	client_secret = _first_nonempty(
		secret_config.get("client_secret"),
		os.environ.get("GOOGLE_CLIENT_SECRET"),
	)
	redirect_uri = _first_nonempty(
		os.environ.get("GOOGLE_REDIRECT_URI"),
		_current_local_redirect_uri(),
		secret_config.get("redirect_uri"),
	)
	if not client_id or not client_secret or not redirect_uri:
		return None

	return GoogleOAuthConfig(
		client_id=client_id,
		client_secret=client_secret,
		redirect_uri=redirect_uri,
		token_encryption_key=_first_nonempty(
			secret_config.get("token_encryption_key"),
			os.environ.get("GOOGLE_TOKEN_ENCRYPTION_KEY"),
		),
	)


def build_authorization_url(config: GoogleOAuthConfig) -> str:
	return _build_authorization_url(config, GOOGLE_REQUIRED_SCOPES, prompt="consent select_account")


def build_calendar_authorization_url(config: GoogleOAuthConfig) -> str:
	return _build_authorization_url(
		config,
		GOOGLE_REQUIRED_SCOPES,
		prompt="consent select_account",
	)


def _streamlit_oauth_redirect_uri(config: GoogleOAuthConfig) -> str:
	return f"{config.redirect_uri.rstrip('/')}/component/streamlit_oauth.authorize_button"


def _render_streamlit_oauth_button(config: GoogleOAuthConfig) -> None:
	component = OAuth2Component(
		config.client_id,
		config.client_secret,
		authorize_endpoint=GOOGLE_AUTHORIZE_ENDPOINT,
		token_endpoint=GOOGLE_TOKEN_ENDPOINT,
		refresh_token_endpoint=GOOGLE_TOKEN_ENDPOINT,
		revoke_token_endpoint=GOOGLE_REVOKE_ENDPOINT,
		token_endpoint_auth_method="client_secret_post",
	)
	try:
		result = component.authorize_button(
			"Sign in with Google",
			redirect_uri=_streamlit_oauth_redirect_uri(config),
			scope=" ".join(GOOGLE_REQUIRED_SCOPES),
			key="google_sign_in",
			extras_params={
				"access_type": "offline",
				"include_granted_scopes": "true",
				"prompt": "consent select_account",
			},
			use_container_width=False,
		)
	except StreamlitOauthError as exc:
		st.error(f"Google sign-in could not be completed. Please try again. ({exc})")
		st.stop()
	if result and result.get("token"):
		_handle_streamlit_oauth_result(result, config)


def _handle_streamlit_oauth_result(result: dict[str, Any], config: GoogleOAuthConfig) -> None:
	token_payload = _streamlit_oauth_token_to_credentials_payload(dict(result.get("token") or {}), config)
	id_token_value = token_payload.get("id_token")
	if not id_token_value:
		st.error("Google did not return an identity token. Please try again.")
		st.stop()
	try:
		profile = id_token.verify_oauth2_token(str(id_token_value), Request(), config.client_id)
	except Exception as exc:
		st.error(f"Google identity could not be verified. Please try again. ({exc})")
		st.stop()

	allowed = is_user_allowed(profile, config)
	persist_authenticated_user(profile, token_payload, config, allowed)
	if not allowed:
		st.error("This Google account is not allowed to access the scheduler.")
		st.stop()

	st.session_state[AUTH_SESSION_KEY] = _build_auth_session(profile, token_payload)
	_create_remembered_session(str(profile["sub"]), config)
	st.rerun()


def _streamlit_oauth_token_to_credentials_payload(token: dict[str, Any], config: GoogleOAuthConfig) -> dict[str, Any]:
	scope_value = token.get("scope") or token.get("scopes") or GOOGLE_REQUIRED_SCOPES
	scopes = sorted(_normalise_scopes(scope_value)) or list(GOOGLE_REQUIRED_SCOPES)
	payload = {
		"token": token.get("access_token") or token.get("token"),
		"refresh_token": token.get("refresh_token"),
		"token_uri": GOOGLE_TOKEN_ENDPOINT,
		"client_id": config.client_id,
		"client_secret": config.client_secret,
		"scopes": scopes,
	}
	if token.get("id_token"):
		payload["id_token"] = token.get("id_token")
	expiry = _streamlit_oauth_token_expiry(token)
	if expiry is not None:
		payload["expiry"] = expiry.isoformat()
	return {key: value for key, value in payload.items() if value not in (None, "")}


def _streamlit_oauth_token_expiry(token: dict[str, Any]) -> datetime | None:
	expiry = _parse_timestamp(token.get("expiry") or token.get("expires_at"))
	if expiry is not None:
		return expiry
	expires_at = token.get("expires_at")
	if isinstance(expires_at, (int, float)):
		return datetime.fromtimestamp(float(expires_at), tz=timezone.utc)
	expires_in = token.get("expires_in")
	try:
		return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
	except (TypeError, ValueError):
		return None


def has_calendar_scopes(auth_session: dict[str, Any]) -> bool:
	session_scopes = _normalise_scopes(auth_session.get("scopes") or (auth_session.get("token") or {}).get("scopes"))
	return set(GOOGLE_CALENDAR_SCOPES).issubset(session_scopes)


def _build_authorization_url(config: GoogleOAuthConfig, scopes: list[str], prompt: str) -> str:
	flow = _oauth_flow(config, scopes=scopes)
	state = _build_oauth_state(config, scopes=scopes)
	auth_url, state = flow.authorization_url(
		access_type="offline",
		include_granted_scopes="true",
		prompt=prompt,
		state=state,
	)
	st.session_state[OAUTH_STATE_KEY] = state
	return auth_url


def is_user_allowed(profile: dict[str, Any], config: GoogleOAuthConfig | None = None) -> bool:
	email = _normalise_email(profile.get("email"))
	if not email:
		return False
	if email == ADMIN_EMAIL:
		return True
	return email in _resident_access_emails()


def _session_user_is_allowed(session: dict[str, Any]) -> bool:
	email = _normalise_email(session.get("email") or (session.get("profile") or {}).get("email"))
	return is_user_allowed({"email": email})


def _resident_access_emails() -> set[str]:
	try:
		init_db()
		with get_connection() as conn:
			rows = conn.execute(
				"""
				SELECT email
				FROM residents
				WHERE email IS NOT NULL
				  AND TRIM(email) <> ''
				"""
			).fetchall()
	except Exception:
		return set()
	return {_normalise_email(row["email"]) for row in rows if _normalise_email(row["email"])}


def _normalise_email(value: Any) -> str:
	return str(value or "").strip().casefold()


def _render_access_denied(email: str, config: GoogleOAuthConfig | None = None) -> None:
	if config is not None:
		_delete_remembered_session(config)
	_clear_remember_cookie()
	st.session_state.pop(AUTH_SESSION_KEY, None)
	_hide_unauthenticated_navigation()
	with st.sidebar:
		render_sidebar_logo(st)
	st.title("Residency Scheduler")
	st.error(
		f"{email or 'This Google account'} is not authorized to access the scheduler. "
		"Ask an administrator to add that email to the Residents page."
	)
	if st.button("Try another Google account"):
		st.rerun()
	st.stop()


def encrypt_token_payload(token_payload: dict[str, Any], token_encryption_key: str) -> str:
	cipher = Fernet(_fernet_key_from_secret(token_encryption_key))
	plaintext = json.dumps(token_payload, sort_keys=True).encode("utf-8")
	return cipher.encrypt(plaintext).decode("utf-8")


def decrypt_token_payload(encrypted_token_json: str, token_encryption_key: str) -> dict[str, Any]:
	cipher = Fernet(_fernet_key_from_secret(token_encryption_key))
	try:
		plaintext = cipher.decrypt(encrypted_token_json.encode("utf-8"))
	except InvalidToken as exc:
		raise ValueError("Stored Google token could not be decrypted.") from exc
	return json.loads(plaintext.decode("utf-8"))


def _fernet_key_from_secret(secret_value: str) -> bytes:
	value = str(secret_value).strip()
	try:
		Fernet(value.encode("utf-8"))
	except (ValueError, TypeError):
		return base64.urlsafe_b64encode(hashlib.sha256(value.encode("utf-8")).digest())
	return value.encode("utf-8")


def _token_encryption_secret(config: GoogleOAuthConfig) -> str:
	return config.token_encryption_key or config.client_secret


def persist_authenticated_user(
	profile: dict[str, Any],
	token_payload: dict[str, Any],
	config: GoogleOAuthConfig,
	allowed: bool,
) -> None:
	init_db()
	google_sub = str(profile["sub"])
	with get_connection() as conn:
		conn.execute(
			"""
			INSERT INTO app_users (google_sub, email, name, picture_url, allowed, created_at, updated_at, last_login_at)
			VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
			ON CONFLICT(google_sub) DO UPDATE SET
				email = excluded.email,
				name = excluded.name,
				picture_url = excluded.picture_url,
				allowed = excluded.allowed,
				updated_at = CURRENT_TIMESTAMP,
				last_login_at = CURRENT_TIMESTAMP
			""",
			(
				google_sub,
				str(profile.get("email") or ""),
				str(profile.get("name") or ""),
				str(profile.get("picture") or ""),
				1 if allowed else 0,
			),
		)
		encrypted = encrypt_token_payload(token_payload, _token_encryption_secret(config))
		conn.execute(
			"""
			INSERT INTO google_oauth_tokens (google_sub, encrypted_token_json, scopes, created_at, updated_at)
			VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
			ON CONFLICT(google_sub) DO UPDATE SET
				encrypted_token_json = excluded.encrypted_token_json,
				scopes = excluded.scopes,
				updated_at = CURRENT_TIMESTAMP
			""",
			(google_sub, encrypted, json.dumps(token_payload.get("scopes", []))),
		)


def _handle_oauth_callback(code: str, state: str | None, config: GoogleOAuthConfig) -> None:
	expected_state = st.session_state.get(OAUTH_STATE_KEY)
	if not _oauth_state_matches(state, expected_state, config):
		_clear_auth_query_params()
		st.error("Google sign-in expired or could not be verified. Please try again.")
		st.stop()

	flow = _oauth_flow(config, state=state, scopes=_scopes_from_oauth_state(state) or GOOGLE_REQUIRED_SCOPES)
	try:
		with _relaxed_oauthlib_token_scope():
			flow.fetch_token(code=code)
	except Exception as exc:
		_clear_auth_query_params()
		st.error(f"Google sign-in could not be completed. Please try again. ({exc})")
		st.stop()
	credentials = flow.credentials
	if not credentials.id_token:
		st.error("Google did not return an identity token. Please try again.")
		st.stop()

	profile = id_token.verify_oauth2_token(credentials.id_token, Request(), config.client_id)
	allowed = is_user_allowed(profile, config)
	token_payload = _credentials_to_token_payload(credentials)
	persist_authenticated_user(profile, token_payload, config, allowed)
	if not allowed:
		_clear_auth_query_params()
		st.error("This Google account is not allowed to access the scheduler.")
		st.stop()

	st.session_state[AUTH_SESSION_KEY] = _build_auth_session(profile, token_payload)
	_create_remembered_session(str(profile["sub"]), config)
	st.session_state.pop(OAUTH_STATE_KEY, None)
	_clear_auth_query_params()
	st.rerun()


def _oauth_flow(config: GoogleOAuthConfig, state: str | None = None, scopes: list[str] | None = None) -> Flow:
	return Flow.from_client_config(
		config.client_config(),
		scopes=scopes or GOOGLE_REQUIRED_SCOPES,
		state=state,
		redirect_uri=config.redirect_uri,
		autogenerate_code_verifier=False,
	)


@contextmanager
def _relaxed_oauthlib_token_scope():
	previous = os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE")
	os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
	try:
		yield
	finally:
		if previous is None:
			os.environ.pop("OAUTHLIB_RELAX_TOKEN_SCOPE", None)
		else:
			os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = previous


def _build_oauth_state(config: GoogleOAuthConfig, scopes: list[str] | None = None, now: datetime | None = None) -> str:
	now = now or datetime.now(timezone.utc)
	payload = {
		"exp": int((now + OAUTH_STATE_TTL).timestamp()),
		"iat": int(now.timestamp()),
		"nonce": secrets.token_urlsafe(24),
		"scopes": list(scopes or GOOGLE_REQUIRED_SCOPES),
	}
	body = _base64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
	return f"{body}.{_sign_oauth_state_body(body, config)}"


def _oauth_state_matches(
	state: str | None,
	expected_state: str | None,
	config: GoogleOAuthConfig,
	now: datetime | None = None,
) -> bool:
	if not state:
		return False
	if expected_state and secrets.compare_digest(str(state), str(expected_state)):
		return True
	return _signed_oauth_state_is_valid(str(state), config, now=now)


def _signed_oauth_state_is_valid(state: str, config: GoogleOAuthConfig, now: datetime | None = None) -> bool:
	payload = _oauth_state_payload(state, config)
	if payload is None:
		return False
	try:
		expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc)
	except (KeyError, TypeError, ValueError):
		return False
	now = now or datetime.now(timezone.utc)
	return expires_at >= now


def _scopes_from_oauth_state(state: str | None) -> list[str] | None:
	config = load_google_oauth_config()
	if not state or config is None:
		return None
	payload = _oauth_state_payload(str(state), config)
	if payload is None:
		return None
	scopes = payload.get("scopes")
	if not isinstance(scopes, list):
		return None
	return [str(scope) for scope in scopes if str(scope).strip()]


def _oauth_state_payload(state: str, config: GoogleOAuthConfig) -> dict[str, Any] | None:
	try:
		body, signature = state.split(".", maxsplit=1)
	except ValueError:
		return None
	expected_signature = _sign_oauth_state_body(body, config)
	if not secrets.compare_digest(signature, expected_signature):
		return None
	try:
		return json.loads(_base64url_decode(body).decode("utf-8"))
	except (KeyError, TypeError, ValueError, json.JSONDecodeError):
		return None


def _sign_oauth_state_body(body: str, config: GoogleOAuthConfig) -> str:
	secret = config.token_encryption_key or config.client_secret
	digest = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
	return _base64url_encode(digest)


def _base64url_encode(value: bytes) -> str:
	return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
	padding = "=" * (-len(value) % 4)
	return base64.urlsafe_b64decode(f"{value}{padding}")


def _refresh_session_if_possible(session: dict[str, Any]) -> bool:
	config = load_google_oauth_config()
	token_payload = dict(session.get("token") or {})
	if config is None or not token_payload.get("refresh_token"):
		sign_out()
		return False

	credentials = Credentials.from_authorized_user_info(token_payload, list(_normalise_scopes(token_payload.get("scopes"))) or GOOGLE_REQUIRED_SCOPES)
	try:
		credentials.refresh(Request())
	except Exception:
		sign_out()
		return False

	refreshed_payload = _credentials_to_token_payload(credentials)
	profile = dict(session.get("profile") or {})
	allowed = is_user_allowed(profile, config)
	persist_authenticated_user(profile, refreshed_payload, config, allowed)
	if not allowed:
		sign_out()
		return False
	st.session_state[AUTH_SESSION_KEY] = _build_auth_session(profile, refreshed_payload)
	return True


def _restore_session_from_cookie(config: GoogleOAuthConfig) -> dict[str, Any] | None:
	cookie_value = _remember_cookie_value()
	if not cookie_value:
		return None
	session_hash = _remember_session_hash(cookie_value, config)
	try:
		init_db()
		with get_connection() as conn:
			row = conn.execute(
				"""
				SELECT s.google_sub, s.expires_at, u.email, u.name, u.picture_url, u.allowed, t.encrypted_token_json
				FROM google_auth_sessions s
				JOIN app_users u ON u.google_sub = s.google_sub
				JOIN google_oauth_tokens t ON t.google_sub = s.google_sub
				WHERE s.session_hash = ?
				""",
				(session_hash,),
			).fetchone()
	except Exception:
		return None
	if row is None:
		return None
	expires_at = _parse_timestamp(row["expires_at"])
	if expires_at is None or expires_at <= datetime.now(timezone.utc):
		_delete_remembered_session(config)
		return None
	try:
		token_payload = decrypt_token_payload(str(row["encrypted_token_json"]), _token_encryption_secret(config))
	except ValueError:
		_delete_remembered_session(config)
		return None
	if not set(GOOGLE_CALENDAR_SCOPES).issubset(_normalise_scopes(token_payload.get("scopes"))):
		return None

	profile = {
		"sub": str(row["google_sub"]),
		"email": str(row["email"] or ""),
		"name": str(row["name"] or ""),
		"picture": str(row["picture_url"] or ""),
	}
	if not is_user_allowed(profile, config):
		_delete_remembered_session(config)
		return None
	session = _build_auth_session(profile, token_payload)
	if _session_auth_is_valid(session):
		_mark_remembered_session_used(session_hash)
		return session

	session["token"] = token_payload
	session["profile"] = profile
	if _refresh_session_if_possible(session):
		_mark_remembered_session_used(session_hash)
		return st.session_state.get(AUTH_SESSION_KEY)

	_delete_remembered_session(config)
	return None


def _create_remembered_session(google_sub: str, config: GoogleOAuthConfig) -> None:
	cookie_value = secrets.token_urlsafe(32)
	session_hash = _remember_session_hash(cookie_value, config)
	expires_at = datetime.now(timezone.utc) + REMEMBER_SESSION_TTL
	init_db()
	with get_connection() as conn:
		conn.execute(
			"""
			INSERT INTO google_auth_sessions (session_hash, google_sub, expires_at, created_at, updated_at, last_used_at)
			VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
			ON CONFLICT(session_hash) DO UPDATE SET
				google_sub = excluded.google_sub,
				expires_at = excluded.expires_at,
				updated_at = CURRENT_TIMESTAMP,
				last_used_at = CURRENT_TIMESTAMP
			""",
			(session_hash, google_sub, expires_at.isoformat()),
		)
	st.session_state[PENDING_REMEMBER_COOKIE_KEY] = {
		"value": cookie_value,
		"expires_at": expires_at.isoformat(),
	}


def _delete_remembered_session(config: GoogleOAuthConfig) -> None:
	cookie_value = _remember_cookie_value()
	if not cookie_value:
		return
	session_hash = _remember_session_hash(cookie_value, config)
	try:
		with get_connection() as conn:
			conn.execute("DELETE FROM google_auth_sessions WHERE session_hash = ?", (session_hash,))
	except Exception:
		return


def _mark_remembered_session_used(session_hash: str) -> None:
	try:
		with get_connection() as conn:
			conn.execute(
				"""
				UPDATE google_auth_sessions
				SET last_used_at = CURRENT_TIMESTAMP
				WHERE session_hash = ?
				""",
				(session_hash,),
			)
	except Exception:
		return


def _remember_session_hash(cookie_value: str, config: GoogleOAuthConfig) -> str:
	secret = config.token_encryption_key or config.client_secret
	return hmac.new(secret.encode("utf-8"), cookie_value.encode("utf-8"), hashlib.sha256).hexdigest()


def _remember_cookie_value() -> str | None:
	try:
		value = st.context.cookies.get(AUTH_COOKIE_NAME)
	except Exception:
		return None
	if not value:
		return None
	return str(value)


def _set_remember_cookie(cookie_value: str, expires_at: datetime) -> None:
	max_age = max(0, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
	components.html(
		f"""
		<script>
		document.cookie = "{AUTH_COOKIE_NAME}={html.escape(cookie_value, quote=True)}; Max-Age={max_age}; Path=/; SameSite=Lax";
		</script>
		""",
		height=0,
		width=0,
	)


def _emit_pending_remember_cookie() -> None:
	pending = st.session_state.pop(PENDING_REMEMBER_COOKIE_KEY, None)
	if not pending:
		return
	expires_at = _parse_timestamp(pending.get("expires_at"))
	if expires_at is None:
		return
	_set_remember_cookie(str(pending.get("value") or ""), expires_at)


def _clear_remember_cookie() -> None:
	components.html(
		f"""
		<script>
		document.cookie = "{AUTH_COOKIE_NAME}=; Max-Age=0; Path=/; SameSite=Lax";
		</script>
		""",
		height=0,
		width=0,
	)


def _session_auth_is_valid(session: Any, now: datetime | None = None) -> bool:
	if not isinstance(session, dict):
		return False
	if not session.get("google_sub") or not session.get("email") or not session.get("profile"):
		return False
	if not set(GOOGLE_CALENDAR_SCOPES).issubset(_normalise_scopes(session.get("scopes") or (session.get("token") or {}).get("scopes"))):
		return False
	expires_at = _parse_timestamp(session.get("expires_at"))
	if expires_at is None:
		return False
	now = now or datetime.now(timezone.utc)
	return expires_at > now + EXPIRY_REFRESH_WINDOW


def _build_auth_session(profile: dict[str, Any], token_payload: dict[str, Any]) -> dict[str, Any]:
	expires_at = _token_expiry(token_payload)
	return {
		"google_sub": str(profile["sub"]),
		"email": str(profile.get("email") or ""),
		"name": str(profile.get("name") or ""),
		"picture": str(profile.get("picture") or ""),
		"profile": dict(profile),
		"token": dict(token_payload),
		"scopes": list(token_payload.get("scopes") or []),
		"expires_at": expires_at.isoformat() if expires_at else "",
	}


def _credentials_to_token_payload(credentials: Credentials) -> dict[str, Any]:
	payload = json.loads(credentials.to_json())
	if "scopes" not in payload or not payload["scopes"]:
		payload["scopes"] = list(credentials.scopes or GOOGLE_REQUIRED_SCOPES)
	else:
		payload["scopes"] = sorted(_normalise_scopes(payload["scopes"]))
	return payload


def _token_expiry(token_payload: dict[str, Any]) -> datetime | None:
	return _parse_timestamp(token_payload.get("expiry"))


def _parse_timestamp(value: Any) -> datetime | None:
	if not value:
		return None
	try:
		parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
	except ValueError:
		return None
	if parsed.tzinfo is None:
		return parsed.replace(tzinfo=timezone.utc)
	return parsed.astimezone(timezone.utc)


def _render_signed_in_sidebar(session: dict[str, Any]) -> None:
	_emit_pending_remember_cookie()
	with st.sidebar:
		render_sidebar_logo(st)
		st.caption(f"Signed in as {session.get('email', '')}")
		if st.button("Sign out", key="google_sign_out"):
			sign_out()
			st.rerun()


def _hide_unauthenticated_navigation() -> None:
	st.html(
		"""
		<style>
			section[data-testid="stSidebar"] {
				display: block;
			}
			section[data-testid="stSidebar"] nav,
			section[data-testid="stSidebar"] [data-testid="stSidebarNav"] {
				display: none;
			}
		</style>
		"""
	)


def _streamlit_google_config() -> dict[str, Any]:
	try:
		google = st.secrets.get("google", {})
	except Exception:
		return {}
	return dict(google) if hasattr(google, "items") else {}


def _current_local_redirect_uri() -> str | None:
	app_root = _current_app_root_url()
	if not app_root:
		return None
	parsed = urlparse(app_root)
	hostname = (parsed.hostname or "").lower()
	if hostname in {"localhost", "127.0.0.1", "::1"}:
		return app_root
	return None


def _current_app_root_url() -> str | None:
	try:
		current_url = str(st.context.url or "")
	except Exception:
		return None
	if not current_url:
		return None
	parsed = urlparse(current_url)
	if not parsed.scheme or not parsed.netloc:
		return None
	return f"{parsed.scheme}://{parsed.netloc}"


def _normalise_scopes(value: Any) -> set[str]:
	if not value:
		return set()
	if isinstance(value, str):
		items = value.split()
	else:
		items = [str(item) for item in value]
	aliases = {
		"https://www.googleapis.com/auth/userinfo.email": "email",
		"https://www.googleapis.com/auth/userinfo.profile": "profile",
	}
	normalised = {aliases.get(item.strip(), item.strip()) for item in items if item.strip()}
	if "email" in normalised:
		normalised.add("https://www.googleapis.com/auth/userinfo.email")
	if "profile" in normalised:
		normalised.add("https://www.googleapis.com/auth/userinfo.profile")
	return normalised


def _first_nonempty(*values: Any) -> str | None:
	for value in values:
		if value is not None and str(value).strip():
			return str(value).strip()
	return None


def _query_param(key: str) -> str | None:
	value = st.query_params.get(key)
	if isinstance(value, list):
		return value[0] if value else None
	return value


def _clear_auth_query_params() -> None:
	for key in ("code", "state", "scope", "authuser", "prompt"):
		if key in st.query_params:
			del st.query_params[key]
