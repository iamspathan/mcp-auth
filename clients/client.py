import asyncio
from typing import Annotated
import os
import sys
import json
import httpx
import webbrowser
import urllib.parse
import secrets
import hashlib
import base64

# --- Utility: PKCE ---
def generate_pkce_pair():
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode('utf-8')
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b'=').decode('utf-8')
    return code_verifier, code_challenge

# --- Utility: Parse WWW-Authenticate header for resource metadata URL ---
def parse_www_authenticate(header):
    # Example: Bearer error="invalid_token", resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"
    for part in header.split(','):
        if 'resource_metadata=' in part:
            url = part.split('resource_metadata=')[1].strip().strip('"')
            return url
    return None

# --- Utility: Dynamic Client Registration ---
async def dynamic_client_registration(auth_server_metadata, resource):
    reg_endpoint = auth_server_metadata.get('registration_endpoint')
    if not reg_endpoint:
        return None
    async with httpx.AsyncClient() as client:
        resp = await client.post(reg_endpoint, json={
            "client_name": "MCP Python Client",
            "redirect_uris": ["http://localhost:8765/callback"],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "openid email",
        })
        resp.raise_for_status()
        return resp.json()

# --- Utility: Start local HTTP server for OAuth callback ---

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
callback_event = threading.Event()
class OAuthCallbackHandler(BaseHTTPRequestHandler):
    code = None
    state = None
    def do_GET(self):
        print("DEBUG: self.path =", self.path)
        parsed_url = urllib.parse.urlparse(self.path)
        print("DEBUG: parsed_url.query =", parsed_url.query)
        params = urllib.parse.parse_qs(parsed_url.query)
        print("DEBUG: params =", params)
        if parsed_url.path == "/callback" and 'code' in params and 'state' in params:
            OAuthCallbackHandler.code = params.get('code', [None])[0]
            OAuthCallbackHandler.state = params.get('state', [None])[0]
            callback_event.set()
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"<h1>Authorization complete. You may close this window.</h1>")

def start_callback_server():
    server = HTTPServer(('localhost', 8765), OAuthCallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server

# --- HTTP OAUTH2/OIDC FLOW ---
async def run_http_agent(mcp_url):
    print(f"Connecting to MCP server at {mcp_url}")
    async with httpx.AsyncClient(follow_redirects=True) as client:
        # 1. Make unauthenticated request to MCP server
        resp = await client.get(mcp_url)
        if resp.status_code == 401:
            www_auth = resp.headers.get('WWW-Authenticate', '')
            resource_metadata_url = parse_www_authenticate(www_auth)
            if not resource_metadata_url:
                print("Could not find resource_metadata in WWW-Authenticate header.")
                return
            # 2. Fetch resource metadata
            meta = await client.get(resource_metadata_url)
            meta.raise_for_status()
            meta_json = meta.json()
            auth_servers = meta_json.get('authorization_servers', [])
            if not auth_servers:
                print("No authorization_servers found in resource metadata.")
                return
            auth_server_url = auth_servers[0]
            # 3. Fetch authorization server metadata
            as_meta = await client.get(auth_server_url)
            as_meta.raise_for_status()
            as_json = as_meta.json()
            # 4. Dynamic client registration (if supported)
            client_creds = await dynamic_client_registration(as_json, mcp_url)
            if client_creds:
                client_id = client_creds['client_id']
            else:
                print("Dynamic client registration not supported. Please provide client_id:")
                client_id = input("Client ID: ").strip()
            # 5. PKCE
            code_verifier, code_challenge = generate_pkce_pair()
            state = secrets.token_urlsafe(16)
            params = {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": "http://localhost:8765/callback",
                "scope": "openid email",
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "resource": mcp_url,
            }
            auth_url = as_json['authorization_endpoint'] + '?' + urllib.parse.urlencode(params)
            print(f"Opening browser for authorization: {auth_url}")
            start_callback_server()
            webbrowser.open(auth_url)
            # Wait for callback
            while not callback_event.is_set():
                await asyncio.sleep(0.1)
            if OAuthCallbackHandler.state != state:
                print("State mismatch! Aborting.")
                return
            # 7. Exchange code for token
            print("About to call token endpoint...")
            token_data = {
                "grant_type": "authorization_code",
                "code": OAuthCallbackHandler.code,
                "redirect_uri": "http://localhost:8765/callback",
                "client_id": client_id,
                "code_verifier": code_verifier,
                "resource": mcp_url,
            }
            token_resp = await client.post(as_json['token_endpoint'], data=token_data, headers={"Content-Type": "application/x-www-form-urlencoded"})
            print("Token endpoint called, got response:", token_resp.status_code)
            if token_resp.status_code != 200:
                print("Token endpoint error body:", token_resp.text)
            token_resp.raise_for_status()
            token_json = token_resp.json()
            print("Token response:", json.dumps(token_json, indent=2))
            access_token = token_json['access_token']
            print("Access token obtained.")

            # --- LLM AGENT WITH ACCESS TOKEN, USING STDIO MCP SERVER ---
            print("\nOIDC login complete. Now starting LLM agent with MCP tools (via stdio)...")
            import subprocess
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from langchain_mcp_adapters.tools import load_mcp_tools
            from langgraph.prebuilt import create_react_agent
            # You can switch between OllamaLLM (local) and ChatOpenAI (OpenAI API) here:
            USE_OPENAI = False  # Set to True to use OpenAI, False for Ollama
            if USE_OPENAI:
                from langchain_openai import ChatOpenAI
                llm = ChatOpenAI(
                    model="gpt-3.5-turbo",
                    temperature=0,
                )
            else:
                try:
                    from langchain_ollama import ChatOllama
                    llm = ChatOllama(model="llama3.1", base_url="http://localhost:11434", temperature=0)
                except ImportError:
                    print("Warning: ChatOllama not found in langchain_ollama. Tool-calling will not work with OllamaLLM. Use OpenAI or upgrade langchain_ollama.")
                    from langchain_ollama import OllamaLLM
                    llm = OllamaLLM(model="llama3.1", base_url="http://localhost:11434", temperature=0)
            server_params = StdioServerParameters(
                command="python",
                args=[os.path.abspath(os.path.join(os.path.dirname(__file__), "../server/terminal_server.py"))],
            )
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await load_mcp_tools(session)
                    # Use tools as-is; instruct LLM/user to always include access_token in tool calls
                    agent = create_react_agent(llm, tools)
                    print("MCP LLM Agent Started! Type 'quit' to exit.")
                    while True:
                        query = input("\nQuery: ").strip()
                        if query.lower() == "quit":
                            break
                        # Always include access_token in tool call input if the tool expects it
                        # (LLM should be prompted to do this, or you can parse and inject here if needed)
                        response = await agent.ainvoke({"messages": query, "access_token": access_token})
                        try:
                            formatted = json.dumps(response, indent=2)
                        except Exception:
                            formatted = str(response)
                        print("\nResponse:")
                        print(formatted)
                    print("MCP LLM Agent Started! Type 'quit' to exit.")
                    while True:
                        query = input("\nQuery: ").strip()
                        if query.lower() == "quit":
                            break
                        response = await agent.ainvoke({"messages": query})
                        try:
                            formatted = json.dumps(response, indent=2)
                        except Exception:
                            formatted = str(response)
                        print("\nResponse:")
                        print(formatted)
        else:
            print(f"Unexpected response from MCP server: {resp.status_code}")
            print(resp.text)
    return

if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "http":
        print("Usage: python client.py http <mcp_url>")
        sys.exit(1)
    mcp_url = sys.argv[2]
    asyncio.run(run_http_agent(mcp_url))