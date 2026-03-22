"""EMAIL-MCP: Gmail MCP server with OAuth2 \u2014 send, read, search, reply, labels.

Standalone HTTP server exposing Gmail operations via MCP protocol.
Pattern follows src/organism/mcp_1c/server.py.

Usage:
    python -m src.organism.mcp_email.server --port 8092
    python -m src.organism.mcp_email.server --auth  # first-time OAuth2 setup
"""
import argparse
import asyncio
import base64
import html.parser
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from aiohttp import web

from src.organism.logging.error_handler import get_logger

_log = get_logger("mcp_email.server")

# Max body length returned to agent (prevent context overflow)
_MAX_BODY_CHARS = 5000

# \u2500\u2500 Tool definitions \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "send_email",
        "description": (
            "Send an email. IMPORTANT: agent MUST use confirm_with_user "
            "before sending."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject",
                },
                "body": {
                    "type": "string",
                    "description": "Email body (plain text or HTML)",
                },
                "cc": {
                    "type": "string",
                    "description": "CC recipients, comma-separated",
                    "default": "",
                },
                "is_html": {
                    "type": "boolean",
                    "description": "true if body contains HTML",
                    "default": False,
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "read_inbox",
        "description": "Read recent emails from inbox",
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Number of emails to return",
                    "default": 10,
                },
                "unread_only": {
                    "type": "boolean",
                    "description": "Only unread emails",
                    "default": False,
                },
            },
        },
    },
    {
        "name": "read_email",
        "description": "Read full text of a specific email by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "Email ID (from read_inbox or search_emails)",
                },
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "search_emails",
        "description": (
            "Search emails using Gmail search syntax. "
            "Examples: 'from:user@mail.ru', 'subject:contract', 'after:2025/01/01'"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (Gmail search syntax)",
                },
                "max_results": {
                    "type": "integer",
                    "default": 10,
                    "description": "Max results",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "reply_to_email",
        "description": (
            "Reply to an email. Keeps thread and adds Re: to subject. "
            "IMPORTANT: agent MUST use confirm_with_user before replying."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "ID of the email to reply to (from read_inbox or search_emails)",
                },
                "body": {
                    "type": "string",
                    "description": "Reply text",
                },
                "reply_all": {
                    "type": "boolean",
                    "description": "Reply to all recipients",
                    "default": False,
                },
            },
            "required": ["message_id", "body"],
        },
    },
    {
        "name": "list_labels",
        "description": "List all email labels (folders)",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


# \u2500\u2500 HTML stripper \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


class _HTMLStripper(html.parser.HTMLParser):
    """Minimal HTML-to-text converter."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def _strip_html(text: str) -> str:
    stripper = _HTMLStripper()
    try:
        stripper.feed(text)
        return stripper.get_text()
    except Exception:
        return text


# \u2500\u2500 MCP Email Server \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


class MCPEmailServer:
    """MCP-protocol HTTP handler for Gmail operations."""

    def __init__(self) -> None:
        self._sender_email: str | None = None
        self._handlers: dict[str, Any] = {
            "send_email": self._h_send_email,
            "read_inbox": self._h_read_inbox,
            "read_email": self._h_read_email,
            "search_emails": self._h_search_emails,
            "reply_to_email": self._h_reply_to_email,
            "list_labels": self._h_list_labels,
        }

    def _get_service(self):
        """Get Gmail service \u2014 delegates to auth.py singleton."""
        from .auth import get_gmail_service
        return get_gmail_service()

    def _get_sender_email(self) -> str:
        """Get authenticated user's email address (cached)."""
        if self._sender_email is None:
            try:
                svc = self._get_service()
                profile = svc.users().getProfile(userId="me").execute()
                self._sender_email = profile.get("emailAddress", "")
            except Exception as e:
                _log.warning(f"Failed to get sender email: {e}")
                self._sender_email = ""
        return self._sender_email

    # \u2500\u2500 HTTP endpoints \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    async def handle_tools_list(self, request: web.Request) -> web.Response:
        return web.json_response({"tools": MCP_TOOLS})

    async def handle_tools_call(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {"isError": True, "content": [{"type": "text", "text": "Invalid JSON"}]},
                status=400,
            )

        tool_name = body.get("name", "")
        arguments = body.get("arguments", {})

        handler = self._handlers.get(tool_name)
        if not handler:
            return web.json_response(
                {
                    "isError": True,
                    "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                },
                status=404,
            )

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, handler, arguments)
            text = json.dumps(result, ensure_ascii=False, indent=2)
            return web.json_response({"content": [{"type": "text", "text": text}]})
        except FileNotFoundError as e:
            return web.json_response(
                {"isError": True, "content": [{"type": "text", "text": str(e)}]},
                status=503,
            )
        except Exception as e:
            _log.error(f"Tool call error: {tool_name} \u2014 {e}")
            return web.json_response(
                {"isError": True, "content": [{"type": "text", "text": f"Error: {e}"}]},
                status=500,
            )

    # \u2500\u2500 JSON-RPC 2.0 (Cursor / Claude Desktop) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    async def handle_jsonrpc(self, request: web.Request) -> web.Response:
        """JSON-RPC 2.0 endpoint for MCP protocol."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }, status=400)

        rpc_id = body.get("id")
        method = body.get("method", "")

        if rpc_id is None:
            return web.Response(status=200)

        if method == "initialize":
            return web.json_response({
                "jsonrpc": "2.0", "id": rpc_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "organism-email", "version": "1.1"},
                },
            })

        if method == "tools/list":
            return web.json_response({
                "jsonrpc": "2.0", "id": rpc_id,
                "result": {"tools": MCP_TOOLS},
            })

        if method == "tools/call":
            params = body.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            handler = self._handlers.get(tool_name)
            if not handler:
                return web.json_response({
                    "jsonrpc": "2.0", "id": rpc_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                        "isError": True,
                    },
                })
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, handler, arguments)
                text = json.dumps(result, ensure_ascii=False, indent=2)
                return web.json_response({
                    "jsonrpc": "2.0", "id": rpc_id,
                    "result": {"content": [{"type": "text", "text": text}]},
                })
            except Exception as e:
                _log.error(f"JSON-RPC tool error: {tool_name} \u2014 {e}")
                return web.json_response({
                    "jsonrpc": "2.0", "id": rpc_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    },
                })

        return web.json_response({
            "jsonrpc": "2.0", "id": rpc_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        })

    # \u2500\u2500 Tool handlers (synchronous \u2014 Gmail API is sync) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def _h_send_email(self, args: dict) -> dict:
        svc = self._get_service()
        to = args["to"]
        subject = args["subject"]
        body_text = args["body"]
        cc = args.get("cc", "")
        is_html = args.get("is_html", False)

        if is_html:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body_text, "html"))
        else:
            msg = MIMEText(body_text)

        msg["From"] = self._get_sender_email()
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        try:
            svc.users().messages().send(
                userId="me", body={"raw": raw},
            ).execute()
        except Exception as e:
            return {"error": f"Failed to send email: {e}"}

        return {
            "status": "sent",
            "from": self._get_sender_email(),
            "to": to,
            "subject": subject,
        }

    def _h_read_inbox(self, args: dict) -> list[dict] | dict:
        svc = self._get_service()
        max_results = args.get("max_results", 10)
        unread_only = args.get("unread_only", False)

        kwargs: dict[str, Any] = {
            "userId": "me",
            "labelIds": ["INBOX"],
            "maxResults": max_results,
        }
        if unread_only:
            kwargs["q"] = "is:unread"

        try:
            resp = svc.users().messages().list(**kwargs).execute()
        except Exception as e:
            return {"error": f"Gmail API error: {e}", "emails": []}

        messages = resp.get("messages") or []
        if not messages:
            return []

        return self._fetch_messages_metadata(svc, [m["id"] for m in messages])

    def _h_read_email(self, args: dict) -> dict:
        svc = self._get_service()
        message_id = args["message_id"]

        try:
            msg = svc.users().messages().get(
                userId="me", id=message_id, format="full",
            ).execute()
        except Exception as e:
            return {"error": f"Failed to read email {message_id}: {e}"}

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        body_text = self._extract_body(msg.get("payload", {}))

        if len(body_text) > _MAX_BODY_CHARS:
            body_text = body_text[:_MAX_BODY_CHARS] + "\n...(truncated)"

        return {
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "body": body_text,
        }

    def _h_search_emails(self, args: dict) -> list[dict] | dict:
        svc = self._get_service()
        query = args["query"]
        max_results = args.get("max_results", 10)

        try:
            resp = svc.users().messages().list(
                userId="me", q=query, maxResults=max_results,
            ).execute()
        except Exception as e:
            return {"error": f"Gmail API error: {e}", "emails": []}

        messages = resp.get("messages") or []
        if not messages:
            return []

        return self._fetch_messages_metadata(svc, [m["id"] for m in messages])

    def _h_reply_to_email(self, args: dict) -> dict:
        svc = self._get_service()
        message_id = args["message_id"]
        body_text = args["body"]
        reply_all = args.get("reply_all", False)

        # Fetch original message headers
        try:
            original = svc.users().messages().get(
                userId="me", id=message_id, format="metadata",
                metadataHeaders=["From", "To", "Cc", "Subject", "Message-ID"],
            ).execute()
        except Exception as e:
            return {"error": f"Failed to read original email {message_id}: {e}"}

        headers = {
            h["name"]: h["value"]
            for h in original.get("payload", {}).get("headers", [])
        }
        thread_id = original.get("threadId", "")

        to = headers.get("From", "")
        subject = headers.get("Subject", "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        msg = MIMEText(body_text)
        msg["From"] = self._get_sender_email()
        msg["To"] = to
        msg["Subject"] = subject

        # Threading headers
        original_msg_id = headers.get("Message-ID", "")
        if original_msg_id:
            msg["In-Reply-To"] = original_msg_id
            msg["References"] = original_msg_id

        if reply_all:
            cc_parts = []
            if headers.get("To"):
                cc_parts.append(headers["To"])
            if headers.get("Cc"):
                cc_parts.append(headers["Cc"])
            sender = self._get_sender_email()
            cc_all = ", ".join(cc_parts)
            cc_filtered = ", ".join(
                addr.strip() for addr in cc_all.split(",")
                if sender.lower() not in addr.lower()
            )
            if cc_filtered:
                msg["Cc"] = cc_filtered

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        try:
            svc.users().messages().send(
                userId="me",
                body={"raw": raw, "threadId": thread_id},
            ).execute()
        except Exception as e:
            return {"error": f"Failed to send reply: {e}"}

        return {
            "status": "replied",
            "from": self._get_sender_email(),
            "to": to,
            "subject": subject,
            "thread_id": thread_id,
        }

    def _h_list_labels(self, args: dict) -> list[dict] | dict:
        svc = self._get_service()
        try:
            resp = svc.users().labels().list(userId="me").execute()
        except Exception as e:
            return {"error": f"Gmail API error: {e}"}
        labels = resp.get("labels", [])
        return [
            {"id": lb["id"], "name": lb["name"], "type": lb.get("type", "")}
            for lb in labels
        ]

    # \u2500\u2500 Helpers \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def _fetch_messages_metadata(self, svc, message_ids: list[str]) -> list[dict]:
        """Fetch metadata for multiple messages using Gmail batch API."""
        results: list[dict | None] = [None] * len(message_ids)

        def _make_callback(index: int):
            def callback(request_id, response, exception):
                if exception:
                    _log.warning(f"Batch fetch error for message: {exception}")
                    return
                h = {
                    hdr["name"]: hdr["value"]
                    for hdr in response.get("payload", {}).get("headers", [])
                }
                label_ids = response.get("labelIds", [])
                results[index] = {
                    "id": response["id"],
                    "from": h.get("From", ""),
                    "subject": h.get("Subject", ""),
                    "date": h.get("Date", ""),
                    "snippet": response.get("snippet", ""),
                    "unread": "UNREAD" in label_ids,
                }
            return callback

        batch = svc.new_batch_http_request()
        for i, msg_id in enumerate(message_ids):
            batch.add(
                svc.users().messages().get(
                    userId="me", id=msg_id, format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ),
                callback=_make_callback(i),
            )
        batch.execute()

        return [r for r in results if r is not None]

    @staticmethod
    def _extract_body(payload: dict) -> str:
        """Extract text body from Gmail message payload (recursive)."""
        # Single-part message
        mime_type = payload.get("mimeType", "")
        body_data = payload.get("body", {}).get("data")

        if body_data and mime_type in ("text/plain", "text/html"):
            decoded = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
            if mime_type == "text/html":
                return _strip_html(decoded)
            return decoded

        # Multipart \u2014 prefer text/plain, fall back to text/html
        parts = payload.get("parts", [])
        plain = ""
        html_text = ""
        for part in parts:
            part_mime = part.get("mimeType", "")
            part_data = part.get("body", {}).get("data")
            if part_data:
                decoded = base64.urlsafe_b64decode(part_data).decode(
                    "utf-8", errors="replace",
                )
                if part_mime == "text/plain" and not plain:
                    plain = decoded
                elif part_mime == "text/html" and not html_text:
                    html_text = _strip_html(decoded)
            # Recurse into nested multipart
            if part.get("parts"):
                nested = MCPEmailServer._extract_body(part)
                if nested and not plain:
                    plain = nested

        return plain or html_text or ""


# \u2500\u2500 App factory \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


def create_app() -> web.Application:
    """Create the aiohttp application with MCP routes."""
    server = MCPEmailServer()
    app = web.Application()
    app.router.add_post("/tools/list", server.handle_tools_list)
    app.router.add_post("/tools/call", server.handle_tools_call)
    app.router.add_post("/jsonrpc", server.handle_jsonrpc)
    return app


# \u2500\u2500 CLI \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP server for Gmail")
    parser.add_argument("--port", type=int, default=8092)
    parser.add_argument(
        "--auth", action="store_true",
        help="Run OAuth2 flow interactively (first-time setup)",
    )
    cli_args = parser.parse_args()

    if cli_args.auth:
        from .auth import get_gmail_service
        svc = get_gmail_service()
        print("Gmail authorization successful!")
        profile = svc.users().getProfile(userId="me").execute()
        print(f"Authorized as: {profile.get('emailAddress', 'unknown')}")
    else:
        print(f"Starting MCP Email server on port {cli_args.port}")
        app = create_app()
        web.run_app(app, port=cli_args.port, print=lambda msg: _log.info(msg))
