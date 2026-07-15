from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import streamlit as st
from cryptography.fernet import Fernet, InvalidToken
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from streamlit_oauth import OAuth2Component, StreamlitOauthError

from residency_scheduler.cache import get_cached_resident_access_snapshot
from residency_scheduler.db import get_connection
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
AUTHORIZATION_CACHE_KEY = "google_authorization_cache"
EXPIRY_REFRESH_WINDOW = timedelta(minutes=5)
OAUTH_STATE_TTL = timedelta(hours=1)
GOOGLE_SIGN_IN_GUIDE_IMAGE_PATH = Path(__file__).resolve().parents[1] / "assets" / "google_sign_in_instructions.png"


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
	"""Require persistent OIDC identity and usable Google Calendar credentials."""
	config = load_google_oauth_config()
	profile = _current_identity_profile()
	if profile is None:
		_render_identity_login()

	if not is_user_allowed(profile, config):
		_render_access_denied(str(profile.get("email") or ""))

	session = st.session_state.get(AUTH_SESSION_KEY)
	if _session_matches_profile(session, profile) and _session_auth_is_valid(session):
		if render_sidebar:
			_render_signed_in_sidebar(session)
		return session

	if _session_matches_profile(session, profile) and _refresh_session_if_possible(session):
		if render_sidebar:
			_render_signed_in_sidebar(st.session_state[AUTH_SESSION_KEY])
		return st.session_state[AUTH_SESSION_KEY]

	if config is not None:
		stored_session = _restore_session_for_profile(profile, config)
		if stored_session is not None:
			st.session_state[AUTH_SESSION_KEY] = stored_session
			if render_sidebar:
				_render_signed_in_sidebar(stored_session)
			return stored_session

	if config is None:
		_render_auth_shell()
		st.error("Google Calendar OAuth is not configured. Add Google client credentials before using the app.")
		st.stop()

	st.session_state.pop(AUTH_SESSION_KEY, None)
	_render_auth_shell()
	st.caption("One-time Calendar authorization is required before entering the scheduler.")
	_render_calendar_authorization_button(config, profile)
	_render_google_calendar_user_guide()
	st.stop()


def sign_out() -> None:
	st.session_state.pop(AUTH_SESSION_KEY, None)
	st.session_state.pop(OAUTH_STATE_KEY, None)
	st.session_state.pop(AUTHORIZATION_CACHE_KEY, None)
	try:
		st.logout()
	except Exception:
		st.rerun()


def get_current_auth_session() -> dict[str, Any]:
	"""Return the session established by the app-level auth gate."""
	session = st.session_state.get(AUTH_SESSION_KEY)
	if not isinstance(session, dict):
		raise RuntimeError("Google authentication has not been established for this session.")
	return session


def current_user_is_allowed() -> bool:
	profile = _current_identity_profile()
	return profile is not None and is_user_allowed(profile)


def render_authenticated_sidebar(session: dict[str, Any]) -> None:
	_render_signed_in_sidebar(session)


def _current_identity_profile() -> dict[str, Any] | None:
	"""Read Streamlit's OIDC identity, with live-session fallback for testability."""
	try:
		user = st.user
		if bool(getattr(user, "is_logged_in", False)):
			profile = {
				"sub": _claim(user, "sub"),
				"email": _claim(user, "email"),
				"name": _claim(user, "name"),
				"picture": _claim(user, "picture"),
			}
			if profile["sub"] and profile["email"]:
				return profile
	except Exception:
		pass

	session = st.session_state.get(AUTH_SESSION_KEY)
	if isinstance(session, dict) and isinstance(session.get("profile"), dict):
		return dict(session["profile"])
	return None


def _claim(user: Any, key: str) -> str:
	try:
		value = user.get(key, "")
	except AttributeError:
		value = getattr(user, key, "")
	return str(value or "")


def _session_matches_profile(session: Any, profile: dict[str, Any]) -> bool:
	return isinstance(session, dict) and str(session.get("google_sub") or "") == str(profile.get("sub") or "")


def _render_auth_shell() -> None:
	_hide_unauthenticated_navigation()
	with st.sidebar:
		render_sidebar_logo(st)
	st.title("Residency Scheduler")


def _render_identity_login() -> None:
	_render_auth_shell()
	st.caption("Sign in with Google to continue.")
	if st.button("Sign in with Google", type="primary"):
		st.login()
	with st.expander("User Guide: Google Sign-In", expanded=True):
		st.markdown(
			"""
1. Click **Sign in with Google**.
2. Select the Google account associated with the residency call calendar.
3. After identity is confirmed, the app will restore existing Calendar access or guide you through a one-time Calendar authorization.

Only the administrator and Google accounts matching an email on the Residents page can enter the scheduler.
"""
		)
	st.stop()


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
	return _build_authorization_url(config, GOOGLE_REQUIRED_SCOPES, prompt="consent")


def build_calendar_authorization_url(config: GoogleOAuthConfig) -> str:
	return _build_authorization_url(
		config,
		GOOGLE_REQUIRED_SCOPES,
		prompt="consent",
	)


def _streamlit_oauth_redirect_uri(config: GoogleOAuthConfig) -> str:
	return f"{config.redirect_uri.rstrip('/')}/component/streamlit_oauth.authorize_button"


def _render_calendar_authorization_button(config: GoogleOAuthConfig, oidc_profile: dict[str, Any]) -> None:
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
			"Authorize Google Calendar",
			redirect_uri=_streamlit_oauth_redirect_uri(config),
			scope=" ".join(GOOGLE_REQUIRED_SCOPES),
			key="google_calendar_authorization",
			extras_params={
				"access_type": "offline",
				"include_granted_scopes": "true",
				"prompt": "consent",
			},
			use_container_width=False,
		)
	except StreamlitOauthError as exc:
		st.error(f"Google Calendar authorization could not be completed. Please try again. ({exc})")
		st.stop()
	if result and result.get("token"):
		_handle_streamlit_oauth_result(result, config, oidc_profile)


def _render_google_calendar_user_guide() -> None:
	with st.expander("User Guide: Google Calendar Authorization", expanded=True):
		st.markdown(
			"""
This app has not yet completed Google verification, so Google may show an extra warning when Calendar access is authorized.

1. Click **Authorize Google Calendar**.
2. Select the Google account associated with the residency call calendar.
3. Click **Advanced**, then [Go to huntingtonhealthresidencyscheduler.streamlit.app (unsafe)](https://accounts.google.com/#).
4. Allow all requested scopes and click **Continue**.
"""
		)
		if GOOGLE_SIGN_IN_GUIDE_IMAGE_PATH.exists():
			st.image(
				str(GOOGLE_SIGN_IN_GUIDE_IMAGE_PATH),
				caption="Google unverified app warning instructions",
				width="stretch",
			)


def _handle_streamlit_oauth_result(
	result: dict[str, Any],
	config: GoogleOAuthConfig,
	oidc_profile: dict[str, Any],
) -> None:
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
	if str(profile.get("sub") or "") != str(oidc_profile.get("sub") or ""):
		st.error("Calendar access was granted by a different Google account. Please use the account selected at sign-in.")
		st.stop()

	allowed = is_user_allowed(oidc_profile, config)
	persist_authenticated_user(oidc_profile, token_payload, config, allowed)
	if not allowed:
		st.error("This Google account is not allowed to access the scheduler.")
		st.stop()

	st.session_state[AUTH_SESSION_KEY] = _build_auth_session(oidc_profile, token_payload)
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
		payload["expiry"] = _format_google_credentials_expiry(expiry)
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


def _google_authorized_user_info(token_payload: dict[str, Any]) -> dict[str, Any]:
	payload = dict(token_payload)
	expires_at = _parse_timestamp(payload.get("expiry"))
	if expires_at is not None:
		payload["expiry"] = _format_google_credentials_expiry(expires_at)
	return payload


def _format_google_credentials_expiry(expires_at: datetime) -> str:
	return expires_at.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


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
	snapshot = get_cached_resident_access_snapshot()
	fingerprint = str(snapshot.get("fingerprint") or "")
	try:
		cached = st.session_state.get(AUTHORIZATION_CACHE_KEY)
	except Exception:
		cached = None
	if isinstance(cached, dict) and cached.get("email") == email and cached.get("fingerprint") == fingerprint:
		return bool(cached.get("allowed"))
	allowed = email in set(snapshot.get("emails") or ())
	try:
		st.session_state[AUTHORIZATION_CACHE_KEY] = {
			"email": email,
			"fingerprint": fingerprint,
			"allowed": allowed,
		}
	except Exception:
		pass
	return allowed


def _session_user_is_allowed(session: dict[str, Any]) -> bool:
	email = _normalise_email(session.get("email") or (session.get("profile") or {}).get("email"))
	return is_user_allowed({"email": email})


def _normalise_email(value: Any) -> str:
	return str(value or "").strip().casefold()


def _render_access_denied(email: str) -> None:
	st.session_state.pop(AUTH_SESSION_KEY, None)
	_render_auth_shell()
	st.error(
		f"{email or 'This Google account'} is not authorized to access the scheduler. "
		"Ask an administrator to add that email to the Residents page."
	)
	if st.button("Try another Google account"):
		sign_out()
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
	oidc_profile = _current_identity_profile()
	if oidc_profile is None or str(profile.get("sub") or "") != str(oidc_profile.get("sub") or ""):
		_clear_auth_query_params()
		st.error("Calendar access was granted by a different Google account. Please use the account selected at sign-in.")
		st.stop()
	allowed = is_user_allowed(oidc_profile, config)
	token_payload = _credentials_to_token_payload(credentials)
	persist_authenticated_user(oidc_profile, token_payload, config, allowed)
	if not allowed:
		_clear_auth_query_params()
		st.error("This Google account is not allowed to access the scheduler.")
		st.stop()

	st.session_state[AUTH_SESSION_KEY] = _build_auth_session(oidc_profile, token_payload)
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
		return False

	credentials = Credentials.from_authorized_user_info(
		_google_authorized_user_info(token_payload),
		list(_normalise_scopes(token_payload.get("scopes"))) or GOOGLE_REQUIRED_SCOPES,
	)
	try:
		credentials.refresh(Request())
	except RefreshError:
		return False

	refreshed_payload = _credentials_to_token_payload(credentials)
	profile = dict(session.get("profile") or {})
	allowed = is_user_allowed(profile, config)
	persist_authenticated_user(profile, refreshed_payload, config, allowed)
	if not allowed:
		return False
	st.session_state[AUTH_SESSION_KEY] = _build_auth_session(profile, refreshed_payload)
	return True


def _restore_session_for_profile(profile: dict[str, Any], config: GoogleOAuthConfig) -> dict[str, Any] | None:
	google_sub = str(profile.get("sub") or "")
	if not google_sub:
		return None
	try:
		with get_connection() as conn:
			row = conn.execute(
				"""
				SELECT encrypted_token_json
				FROM google_oauth_tokens
				WHERE google_sub = ?
				""",
				(google_sub,),
			).fetchone()
	except Exception:
		return None
	if row is None:
		return None
	try:
		token_payload = decrypt_token_payload(str(row["encrypted_token_json"]), _token_encryption_secret(config))
	except ValueError:
		return None
	if not set(GOOGLE_CALENDAR_SCOPES).issubset(_normalise_scopes(token_payload.get("scopes"))):
		return None
	session = _build_auth_session(profile, token_payload)
	if _session_auth_is_valid(session):
		return session

	session["token"] = token_payload
	session["profile"] = profile
	if _refresh_session_if_possible(session):
		return st.session_state.get(AUTH_SESSION_KEY)
	return None


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
