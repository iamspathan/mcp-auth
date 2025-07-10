from flask import Flask, request, jsonify, make_response
import jwt
import time

# --- HTTP MCP SERVER (OAuth2 Resource Server) ---
http_app = Flask("mcp_http")

# --- Config ---
RESOURCE_METADATA_URL = "http://localhost:5000/.well-known/oauth-protected-resource"
AUTH_SERVER_METADATA_URL = "http://localhost:4000/.well-known/oauth-authorization-server"
CANONICAL_RESOURCE = "foo"  # Set to client_id to match OIDC token audience
PUBLIC_KEYS = ["dev-secret-key"]  # Replace with your real public keys or JWKS

# --- Resource Metadata Endpoint (RFC9728) ---
@http_app.route("/.well-known/oauth-protected-resource")
def protected_resource_metadata():
    return jsonify({
        "resource": CANONICAL_RESOURCE,
        "authorization_servers": [AUTH_SERVER_METADATA_URL],
        "resource_type": "mcp-server",
        # Add more fields as needed
    })

# --- Example MCP API Endpoint ---
@http_app.route("/mcp", methods=["POST", "GET"])
def mcp_api():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return unauthorized_response()
    token = auth_header.split(" ", 1)[1]
    # Validate JWT (for demo, just decode with secret; in prod, use public key/JWKS)
    try:
        payload = jwt.decode(token, PUBLIC_KEYS[0], algorithms=["HS256"], audience=CANONICAL_RESOURCE)
        # Optionally: check exp, scope, etc.
    except Exception as e:
        print("JWT decode error:", e)
        return unauthorized_response()
    # --- Process MCP request ---
    if request.method == "POST":
        data = request.get_json() or {}
        query = data.get("query", "")
        return jsonify({"result": f"Echo: {query}", "user": payload.get("sub")})
    else:
        return jsonify({"message": "MCP server is running.", "user": payload.get("sub")})

# --- 401 Unauthorized with WWW-Authenticate header (RFC9728) ---
def unauthorized_response():
    resp = make_response(jsonify({"error": "unauthorized", "error_description": "Missing or invalid access token."}), 401)
    resp.headers["WWW-Authenticate"] = f'Bearer error="invalid_token", resource_metadata="{RESOURCE_METADATA_URL}"'
    return resp

# imports
import os
import subprocess
import requests
from mcp.server.fastmcp import FastMCP
from sqlalchemy import true

mcp = FastMCP("terminal")



# --- Example LLM + Bearer Token Tool ---
# This tool expects the client to provide a valid access token (JWT) and can call the MCP API directly.
@mcp.tool()
async def call_mcp_with_access_token(
    access_token: str,
    query: str,
    resource_url: str = "http://localhost:5000/mcp"
) -> dict:
    """
    Call the MCP API with a provided Bearer access token and query.
    Args:
        access_token: The JWT access token obtained from OIDC server.
        query: The query to send to the MCP API.
        resource_url: The MCP API endpoint (default: http://localhost:5000/mcp)
    Returns:
        The API response as a dict, or error message.
    """
    import requests
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        data = {"query": query}
        resp = requests.post(resource_url, json=data, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "http":
        print("MCP HTTP server running on port 5000...")
        http_app.run(port=5000, debug=True)
    else:
        print("MCP server started and waiting for requests (STDIO)...")
        mcp.run(transport="stdio")
