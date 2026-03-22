"""EMAIL-MCP: OAuth2 authorization for Gmail API.

First run requires interactive browser flow.
Subsequent runs use refresh token automatically.
Thread-safe: module-level lock protects token refresh from race conditions.

Usage:
    python -m src.organism.mcp_email.server --auth   # interactive setup
"""
import base64
import json
import sys
import threading
from pathlib import Path

from src.organism.logging.error_handler import get_logger

_log = get_logger("mcp_email.auth")

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
CREDENTIALS_PATH = Path("config/gmail/credentials.json")
TOKEN_PATH = Path("config/gmail/token.json")

_lock = threading.Lock()
_cached_service = None


def _save_token(creds) -> None:
    """Save token with base64 obfuscation."""
    data = creds.to_json().encode()
    encoded = base64.b64encode(data).decode()
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(encoded)


def _load_token(scopes):
    """Load token from file. Handles both obfuscated and plain-text formats."""
    from google.oauth2.credentials import Credentials

    if not TOKEN_PATH.exists():
        return None

    raw = TOKEN_PATH.read_text().strip()
    if not raw:
        return None

    # Try base64 obfuscated format first
    try:
        decoded = base64.b64decode(raw).decode()
        info = json.loads(decoded)
        creds = Credentials.from_authorized_user_info(info, scopes)
        return creds
    except Exception:
        pass

    # Fallback: plain-text JSON (backward compatibility)
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), scopes)
        # Re-save in obfuscated format
        _save_token(creds)
        _log.info("Token migrated to obfuscated format")
        return creds
    except Exception as e:
        _log.warning(f"Invalid token.json, will re-authorize: {e}")
        TOKEN_PATH.unlink(missing_ok=True)
        return None


def get_gmail_service():
    """Build and return authorized Gmail API service.

    Thread-safe: uses module-level lock + cached service singleton.

    Returns:
        googleapiclient.discovery.Resource for Gmail v1.

    Raises:
        FileNotFoundError: if credentials.json is missing.
        SystemExit: if authorization fails.
    """
    global _cached_service

    # Late imports \u2014 these are optional dependencies
    try:
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        _log.error(f"Missing Google API dependency: {e}")
        print(
            "Install required packages:\n"
            "  pip install google-api-python-client "
            "google-auth-httplib2 google-auth-oauthlib"
        )
        sys.exit(1)

    with _lock:
        # Check cached service
        if _cached_service is not None:
            creds = _cached_service._http.credentials
            if creds and creds.valid:
                return _cached_service
            # Expired \u2014 try refresh
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    _save_token(creds)
                    _log.info("Gmail token refreshed successfully")
                    return _cached_service
                except Exception as e:
                    _log.warning(f"Token refresh failed, re-authorizing: {e}")
                    _cached_service = None
                    TOKEN_PATH.unlink(missing_ok=True)

        if not CREDENTIALS_PATH.exists():
            msg = (
                f"Gmail credentials not found at {CREDENTIALS_PATH}\n"
                "Setup instructions:\n"
                "  1. Go to https://console.cloud.google.com/apis/credentials\n"
                "  2. Create OAuth 2.0 Client ID (Desktop app)\n"
                "  3. Download JSON and save as config/gmail/credentials.json\n"
                "  4. Enable Gmail API in your project\n"
                "  5. Run: python -m src.organism.mcp_email.server --auth"
            )
            _log.error(msg)
            raise FileNotFoundError(msg)

        creds = _load_token(SCOPES)

        # Refresh or run new flow
        if creds and creds.valid:
            pass
        elif creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save_token(creds)
                _log.info("Gmail token refreshed successfully")
            except Exception as e:
                _log.warning(f"Token refresh failed, re-authorizing: {e}")
                TOKEN_PATH.unlink(missing_ok=True)
                creds = None
        else:
            creds = None

        if creds is None:
            _log.info("Starting OAuth2 flow \u2014 browser will open for authorization")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_PATH), SCOPES,
            )
            creds = flow.run_local_server(port=0)
            _save_token(creds)
            _log.info("Gmail authorized successfully, token saved")

        service = build("gmail", "v1", credentials=creds)
        _cached_service = service
        return service
