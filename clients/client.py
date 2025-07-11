# --- Modern MCP Client using SDK (stdio + OAuth) ---
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
import requests
import argparse

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client
from mcp import StdioServerParameters
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
    def __init__(self, server_command: str = "python3 run_server_stdio.py"):
        self.server_command = server_command
        self.session: ClientSession | None = None

    async def connect(self):
        print(f"Connecting to MCP server using command: {self.server_command}")
        
        # Set up server parameters for stdio connection
        server_params = StdioServerParameters(
            command="python3",
            args=["run_server_stdio.py"],
            env=None,
        )

        # Use stdio client to connect to the local server
        async with stdio_client(server_params) as (read_stream, write_stream):
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
        except Exception as e:
            print(f"Failed to call tool '{tool_name}': {e}")


def ollama_chat(messages, model="llama3.1:latest"):
    url = "http://localhost:11434/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False
    }
    response = requests.post(url, json=payload)
    response.raise_for_status()
    return response.json()["message"]["content"]


def format_tools_for_llm(tools):
    """Format the list of tools and their schemas for the LLM prompt."""
    lines = []
    for tool in tools.tools:
        lines.append(f"Tool: {tool.name}")
        if hasattr(tool, 'description') and tool.description:
            lines.append(f"  Description: {tool.description}")
        if hasattr(tool, 'inputSchema'):
            lines.append(f"  Input schema: {json.dumps(getattr(tool, 'inputSchema', {}), indent=2)}")
    return '\n'.join(lines)

async def llm_agent_loop(session, model="llama3.1:latest"):
    print("\nLLM Agent Mode. Type your request, or 'quit' to exit.")
    conversation = []
    while True:
        user_input = input("You: ").strip()
        if user_input.lower() == "quit":
            break
        # 1. List available tools
        tools = await session.list_tools()
        tool_names = [tool.name for tool in tools.tools]
        tool_info = format_tools_for_llm(tools)
        # 2. Build LLM prompt
        prompt = f"""
You are an AI assistant that can call the following tools by replying with:
call <tool_name> <json_args>
Otherwise, reply directly to the user.

Available tools:
{tool_info}

User: {user_input}
"""
        messages = [
            {"role": "system", "content": "You are a helpful assistant that can call tools via MCP."},
            {"role": "user", "content": prompt}
        ]
        llm_reply = ollama_chat(messages, model=model)
        print(f"LLM: {llm_reply}")
        if llm_reply.strip().startswith("call "):
            try:
                # Parse: call <tool_name> <json_args>
                parts = llm_reply.strip().split(None, 2)
                tool_name = parts[1]
                arguments = {}
                if len(parts) > 2:
                    arguments = json.loads(parts[2])
                print(f"[Agent] Calling tool '{tool_name}' with arguments: {arguments}")
                result = await session.call_tool(tool_name, arguments)
                print(f"[Tool Result]: {result}")
                # Optionally, feed result back to LLM for multi-turn reasoning
                # conversation.append({"role": "assistant", "content": llm_reply})
                # conversation.append({"role": "tool", "content": json.dumps(result)})
            except Exception as e:
                print(f"[Agent] Failed to parse or call tool: {e}")
        else:
            # Just a direct reply
            continue

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true", help="Run in LLM agent mode (Ollama)")
    args = parser.parse_args()

    client = SimpleMCPClient()
    if args.llm:
        async def run_llm():
            print("Connecting to MCP server using command: python3 run_server_stdio.py")
            server_params = StdioServerParameters(
                command="python3",
                args=["run_server_stdio.py"],
                env=None,
            )
            async with stdio_client(server_params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    print("Session initialized!\n")
                    await session.initialize()
                    await llm_agent_loop(session)
        asyncio.run(run_llm())
    else:
        asyncio.run(client.connect())