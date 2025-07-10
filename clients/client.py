# --- Modern MCP Client using SDK (streamable HTTP + OAuth) ---
import asyncio
import httpx
import os
import sys
import json
import webbrowser
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken


class InMemoryTokenStorage(TokenStorage):
    """Simple in-memory token storage implementation."""
    def __init__(self):
        self.tokens = None
        self.client_info = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info


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
        import time
        start = time.time()
        while "authorization_code" not in CallbackHandler.callback_data and time.time() - start < timeout:
            time.sleep(0.1)
        return CallbackHandler.callback_data.get("authorization_code")

    def get_state(self):
        return CallbackHandler.callback_data.get("state")


class SimpleMCPClient:
    def __init__(self, server_url: str):
        self.server_url = server_url
        self.session: ClientSession | None = None

    async def connect(self):
        print(f"Connecting to MCP server at {self.server_url}")
        callback_server = CallbackServer(port=8765)
        callback_server.start()

        async def callback_handler() -> tuple[str, str | None]:
            print("Waiting for OAuth callback...")
            try:
                auth_code = callback_server.wait_for_callback(timeout=300)
                return auth_code, callback_server.get_state()
            finally:
                callback_server.stop()

        client_metadata_dict = {
            "client_id": "foo",
            "redirect_uris": ["http://localhost:8765/callback"],
        }

        # Pre-populate storage with static client info to prevent registration
        storage = InMemoryTokenStorage()
        from mcp.shared.auth import OAuthClientInformationFull
        storage.client_info = OAuthClientInformationFull(
            client_id="foo",
            client_secret=None,
            redirect_uris=["http://localhost:8765/callback"],
            scope="openid email",
            token_endpoint_auth_method="none",
        )

        async def _default_redirect_handler(authorization_url: str) -> None:
            print(f"Opening browser for authorization: {authorization_url}")
            webbrowser.open(authorization_url)

        oauth_auth = OAuthClientProvider(
            server_url="http://localhost:4000",
            client_metadata=OAuthClientMetadata.model_validate(client_metadata_dict),
            storage=storage,
            redirect_handler=_default_redirect_handler,
            callback_handler=callback_handler,
            # issuer_url="http://localhost:4000",
            # authorization_server_metadata_url="http://localhost:4000/.well-known/oauth-authorization-server",
        )

        # Use streamable HTTP transport
        async with streamablehttp_client(
            url=self.server_url,
            auth=oauth_auth,
            timeout=timedelta(seconds=60),
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                self.session = session
                print("Session initialized!\n")
                await session.initialize()
                await self.interactive_loop()

    async def interactive_loop(self):
        print("\nMCP Client Interactive Mode\nType 'list' to list tools, 'call <tool_name> {json_args}' to call a tool, or 'quit' to exit.")
        while True:
            try:
                command = input("mcp> ").strip()
                if not command:
                    continue
                if command == "quit":
                    break
                elif command == "list":
                    await self.list_tools()
                elif command.startswith("call "):
                    parts = command.split(maxsplit=2)
                    tool_name = parts[1] if len(parts) > 1 else ""
                    arguments = {}
                    if len(parts) > 2:
                        try:
                            arguments = json.loads(parts[2])
                        except Exception:
                            print("Invalid JSON arguments. Usage: call <tool_name> {json_args}")
                            continue
                    await self.call_tool(tool_name, arguments)
                else:
                    print("Unknown command. Try 'list', 'call <tool_name>', or 'quit'")
            except KeyboardInterrupt:
                print("\nExiting.")
                break

    async def list_tools(self):
        if not self.session:
            print("Not connected to server.")
            return
        try:
            result = await self.session.list_tools()
            if hasattr(result, "tools") and result.tools:
                print("\nAvailable tools:")
                for i, tool in enumerate(result.tools, 1):
                    print(f"{i}. {tool.name}")
                    if tool.description:
                        print(f"   Description: {tool.description}")
                    print(f"   Input schema: {json.dumps(getattr(tool, 'inputSchema', {}), indent=2)}")
            else:
                print("No tools available.")
        except Exception as e:
            print(f"Failed to list tools: {e}")

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None):
        if not self.session:
            print("Not connected to server.")
            return
        try:
            if (not arguments or "query" not in arguments):
                arguments = {"query": "hello from client"}
            print(f"Calling tool '{tool_name}' with arguments: {arguments}")
            result = await self.session.call_tool(tool_name, arguments or {})
            print(f"\nTool '{tool_name}' result:")
            if hasattr(result, "content"):
                for content in result.content:
                    if content.type == "text":
                        print(content.text)
                    else:
                        print(content)
            else:
                print(result)
        except httpx.HTTPStatusError as e:
            print("→ Request JSON:", e.request.content.decode())
            print("→ Response status:", e.response.status_code)
            print("→ Response body:", e.response.text)
            raise
        except Exception as e:
            print(f"Failed to call tool '{tool_name}': {e}")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "http":
        print("Usage: python client.py http <mcp_url>")
        sys.exit(1)
    mcp_url = sys.argv[2]
    client = SimpleMCPClient(mcp_url)
    asyncio.run(client.connect())