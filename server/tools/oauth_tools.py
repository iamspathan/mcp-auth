import os
import requests
import webbrowser
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
from typing import Dict, Any, Optional

class CallbackHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler to capture OAuth callback."""
    callback_data = {}

    def do_GET(self):
        parsed = urlparse(self.path)
        query_params = parse_qs(parsed.query)
        if "code" in query_params:
            CallbackHandler.callback_data["authorization_code"] = query_params["code"][0]
            CallbackHandler.callback_data["state"] = query_params.get("state", [None])[0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Authorization Successful!</h1><p>You can close this window.</p>")
        elif "error" in query_params:
            CallbackHandler.callback_data["error"] = query_params["error"][0]
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Authorization Failed</h1>")
        else:
            self.send_response(404)
            self.end_headers()

class CallbackServer:
    def __init__(self, port=8765):
        self.port = port
        self.server = HTTPServer(("localhost", self.port), CallbackHandler)
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
        if self.thread:
            self.thread.join(timeout=1)

    def wait_for_callback(self, timeout=300):
        start = time.time()
        while "authorization_code" not in CallbackHandler.callback_data and time.time() - start < timeout:
            time.sleep(0.1)
        return CallbackHandler.callback_data.get("authorization_code")

    def get_state(self):
        return CallbackHandler.callback_data.get("state")

def get_bearer_token(auth_server: str, client_id: str, client_secret: str, code: str, redirect_uri: str) -> Dict[str, Any]:
    """Exchange authorization code for access token."""
    token_url = f"{auth_server}/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    response = requests.post(token_url, data=data)
    response.raise_for_status()
    return response.json()

def call_resource_api(resource_url: str, bearer_token: str) -> Dict[str, Any]:
    """Call a protected resource API with bearer token."""
    headers = {"Authorization": f"Bearer {bearer_token}"}
    response = requests.get(resource_url, headers=headers)
    response.raise_for_status()
    return response.json()

def generate_authorization_url(auth_server: str, client_id: str, redirect_uri: str, scope: str = "openid", state: Optional[str] = None) -> str:
    """Generate OAuth authorization URL."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
    }
    if state:
        params["state"] = state
    
    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    return f"{auth_server}/auth?{query_string}"

def complete_oauth_flow(auth_server: str, client_id: str, client_secret: str, redirect_uri: str, scope: str = "openid") -> Dict[str, Any]:
    """Complete the entire OAuth flow automatically."""
    # Generate authorization URL
    auth_url = generate_authorization_url(auth_server, client_id, redirect_uri, scope)
    
    # Start callback server
    callback_server = CallbackServer()
    callback_server.start()
    
    # Open browser for authorization
    webbrowser.open(auth_url)
    
    # Wait for callback
    code = callback_server.wait_for_callback()
    if not code:
        callback_server.stop()
        return {"error": "Authorization failed or timed out"}
    
    # Exchange code for token
    try:
        token_response = get_bearer_token(auth_server, client_id, client_secret, code, redirect_uri)
        callback_server.stop()
        return {
            "success": True,
            "access_token": token_response.get("access_token"),
            "token_type": token_response.get("token_type"),
            "expires_in": token_response.get("expires_in"),
            "refresh_token": token_response.get("refresh_token"),
            "scope": token_response.get("scope")
        }
    except Exception as e:
        callback_server.stop()
        return {"error": f"Token exchange failed: {str(e)}"}

# Legacy functions for backward compatibility
def get_bearer_token_legacy(auth_server, client_id, client_secret, code, redirect_uri):
    """Legacy function for backward compatibility."""
    result = get_bearer_token(auth_server, client_id, client_secret, code, redirect_uri)
    return result["access_token"]

def call_resource_api_legacy(resource_url, bearer_token):
    """Legacy function for backward compatibility."""
    return call_resource_api(resource_url, bearer_token)