"""OpenRouter OAuth PKCE flow for local-first desktop applications."""

import base64
import hashlib
import json
import secrets
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


AUTH_URL = "https://openrouter.ai/auth"
TOKEN_URL = "https://openrouter.ai/api/v1/auth/keys"


class OAuthError(RuntimeError):
    pass


def create_pkce_pair():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorization_url(callback_url, challenge):
    query = urllib.parse.urlencode({
        "callback_url": callback_url,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{AUTH_URL}?{query}"


def exchange_code(code, verifier, timeout=30):
    payload = json.dumps({
        "code": code,
        "code_verifier": verifier,
        "code_challenge_method": "S256",
    }).encode("utf-8")
    request = urllib.request.Request(
        TOKEN_URL, data=payload, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "DeYaz/2"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise OAuthError(f"OpenRouter authorization exchange failed: {exc}") from exc
    key = str(result.get("key") or "").strip()
    if not key:
        raise OAuthError("OpenRouter did not return an API key.")
    return key


def authorize(timeout=180, browser_open=webbrowser.open):
    """Complete OAuth in the system browser and return the provisioned key."""
    verifier, challenge = create_pkce_pair()
    state = secrets.token_urlsafe(24)
    result = {"code": "", "error": ""}
    received = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if parsed.path != "/callback" or params.get("state", [""])[0] != state:
                self.send_error(400, "Invalid OAuth callback")
                return
            result["code"] = params.get("code", [""])[0]
            result["error"] = params.get("error", [""])[0]
            body = ("<!doctype html><meta charset='utf-8'><title>DeYaz</title>"
                    "<style>body{font:16px Segoe UI;background:#171310;color:#fff;"
                    "display:grid;place-items:center;height:100vh;margin:0}main{max-width:460px;"
                    "padding:36px;border:1px solid #514338;border-radius:24px;background:#241e1a}"
                    "b{color:#ff7548}</style><main><b>İcazə alındı</b>"
                    "<p>DeYaz bağlantını tamamlayır. Bu pəncərəni bağlayıb tətbiqə qayıda bilərsiniz.</p></main>")
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            received.set()

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
    server.timeout = 1
    port = server.server_address[1]
    callback = f"http://localhost:{port}/callback?state={urllib.parse.quote(state)}"
    auth_url = build_authorization_url(callback, challenge)
    try:
        if not browser_open(auth_url):
            raise OAuthError("The browser could not be opened.")
        remaining = timeout
        while remaining > 0 and not received.is_set():
            server.handle_request()
            remaining -= 1
        if not received.is_set():
            raise OAuthError("OpenRouter sign-in timed out. Please try again.")
        if result["error"]:
            raise OAuthError(f"OpenRouter authorization was declined: {result['error']}")
        if not result["code"]:
            raise OAuthError("The authorization callback did not include a code.")
        return exchange_code(result["code"], verifier)
    finally:
        server.server_close()
