"""EMAIL-MCP: OAuth2 authorization for Gmail API.

First run requires interactive browser flow.
Subsequent runs use refresh token automatically.

Usage:
    python -m src.organism.mcp_email.server --auth   # interactive setup
"""
import sys
from pathlib import Path

from src.organism.logging.error_handler import get_logger

_log = get_logger("mcp_email.auth")

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
CREDENTIALS_PATH = Path("config/gmail/credentials.json")
TOKEN_PATH = Path("config/gmail/token.json")


def get_gmail_service():
    """Build and return authorized Gmail API service.

    Returns:
        googleapiclient.discovery.Resource for Gmail v1.

    Raises:
        FileNotFoundError: if credentials.json is missing.
        SystemExit: if authorization fails.
    """
    # Late imports \u2014 these are optional dependencies
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
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

    creds = None

    # Load existing token
    if TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        except Exception as e:
            _log.warning(f"Invalid token.json, will re-authorize: {e}")
            TOKEN_PATH.unlink(missing_ok=True)
            creds = None

    # Refresh or run new flow
    if creds and creds.valid:
        pass
    elif creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json())
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
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json())
        _log.info("Gmail authorized successfully, token saved")

    return build("gmail", "v1", credentials=creds)
