# --- Custom TokenInfo class (for demo, matches what verifier returns) ---
class TokenInfo:
    def __init__(self, sub, scope, claims):
        self.sub = sub
        self.scope = scope
        self.claims = claims
# --- Modern MCP Resource Server using FastAPI and MCP SDK ---
import os
import sys
import json
from fastapi import FastAPI, Request, Depends, HTTPException, status, Body
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import BaseModel

RESOURCE_METADATA_URL = "http://localhost:5000/.well-known/oauth-protected-resource"
AUTH_SERVER_METADATA_URL = "http://localhost:4000/.well-known/oauth-authorization-server"
CANONICAL_RESOURCE = "foo"  # Set to client_id to match OIDC token audience

# --- FastAPI app ---
app = FastAPI(title="MCP Resource Server", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Custom TokenVerifier using local JWT decode (for demo) ---
import jwt
PUBLIC_KEYS = ["dev-secret-key"]  # Replace with your real public keys or JWKS

token_verifier = None

class LocalJWTTokenVerifier(TokenVerifier):
    async def verify_token(self, token: str):
        try:
            payload = jwt.decode(token, PUBLIC_KEYS[0], algorithms=["HS256"], audience=CANONICAL_RESOURCE)
            print("Decoded JWT payload:", payload)  # Debug print
            # You can add more checks here (exp, scope, etc.)
            return TokenInfo(
                sub=payload.get("sub"),
                scope=payload.get("scope", ""),
                claims=payload,
            )
        except Exception as e:
            print("JWT decode error (TokenVerifier):", e)
            raise Exception("Invalid access token")

token_verifier = LocalJWTTokenVerifier()

mcp = FastMCP(
    name="terminal",
    app=app,
    token_verifier=token_verifier,
    auth=AuthSettings(
        resource=CANONICAL_RESOURCE,
        resource_metadata_url=RESOURCE_METADATA_URL,
        authorization_server_metadata_url=AUTH_SERVER_METADATA_URL,
        issuer_url="http://localhost:4000",
        resource_server_url="http://localhost:5000",
    ),
)


# --- Resource Metadata Endpoint (RFC9728) ---
@app.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata():
    return {
        "resource": CANONICAL_RESOURCE,
        "authorization_servers": [AUTH_SERVER_METADATA_URL],
        "resource_type": "mcp-server",
    }

# --- Example MCP API Endpoint (protected) ---
class QueryRequest(BaseModel):
    query: str

async def get_token_info(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid access token.", headers={
            "WWW-Authenticate": f'Bearer error="invalid_token", resource_metadata="{RESOURCE_METADATA_URL}"'
        })
    token = auth_header.split(" ", 1)[1]
    # Use the custom token verifier directly
    try:
        token_info = await token_verifier.verify_token(token)
        return token_info
    except Exception as e:
        print("Token verification error:", e)
        raise HTTPException(status_code=401, detail="Invalid access token.", headers={
            "WWW-Authenticate": f'Bearer error="invalid_token", resource_metadata="{RESOURCE_METADATA_URL}"'
        })

# --- MCP tool registration (for LLM/stdio use) ---
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
    if len(sys.argv) > 1 and sys.argv[1] == "http":
        import uvicorn
        print("MCP HTTP server running on port 5000...")
        uvicorn.run("server.terminal_server:app", host="0.0.0.0", port=5000, reload=True)
    else:
        print("MCP server started and waiting for requests (STDIO)...")
        mcp.run(transport="stdio")
