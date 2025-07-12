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
from pydantic import BaseModel, AnyHttpUrl

RESOURCE_METADATA_URL = "http://localhost:5001/.well-known/oauth-protected-resource"
AUTH_SERVER_METADATA_URL = "http://localhost:4000/.well-known/oauth-authorization-server"
CANONICAL_RESOURCE = "http://localhost:5001"  # Match the OIDC server's audience

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
        issuer_url=AnyHttpUrl("http://localhost:4000"),
        resource_server_url=AnyHttpUrl("http://localhost:5001"),
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
from server.tools.oauth_tools import (
    generate_authorization_url, 
    get_bearer_token, 
    call_resource_api, 
    complete_oauth_flow
)

@mcp.tool()
async def echo_query(query: str) -> dict:
    """
    Echo back the provided query with a simple response.
    Args:
        query: The query to echo back.
    Returns:
        A simple response containing the query.
    """
    return {"message": f"Echo: {query}", "status": "success"}

@mcp.tool()
async def generate_oauth_url(
    auth_server: str = "http://localhost:4000",
    client_id: str = "foo",
    redirect_uri: str = "http://localhost:8765/callback",
    scope: str = "openid"
) -> dict:
    """
    Generate an OAuth authorization URL for the user to visit.
    Args:
        auth_server: The OAuth server URL (default: http://localhost:4000)
        client_id: The OAuth client ID (default: client1)
        redirect_uri: The redirect URI for the callback (default: http://localhost:8765/callback)
        scope: The OAuth scope (default: openid)
    Returns:
        The authorization URL and instructions.
    """
    auth_url = generate_authorization_url(auth_server, client_id, redirect_uri, scope)
    return {
        "authorization_url": auth_url,
        "instructions": "Visit this URL in your browser to authorize the application. After authorization, you'll be redirected to a callback URL.",
        "auth_server": auth_server,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope
    }

@mcp.tool()
async def exchange_code_for_token(
    auth_server: str = "http://localhost:4000",
    client_id: str = "foo",
    client_secret: str = "",
    code: str = "",
    redirect_uri: str = "http://localhost:8765/callback"
) -> dict:
    """
    Exchange an authorization code for an access token.
    Args:
        auth_server: The OAuth server URL (default: http://localhost:4000)
        client_id: The OAuth client ID (default: client1)
        client_secret: The OAuth client secret (default: client1-secret)
        code: The authorization code from the OAuth callback
        redirect_uri: The redirect URI used in the authorization (default: http://localhost:8765/callback)
    Returns:
        The access token and related information.
    """
    if not code:
        return {"error": "Authorization code is required"}
    
    try:
        token_response = get_bearer_token(auth_server, client_id, client_secret, code, redirect_uri)
        return {
            "success": True,
            "access_token": token_response.get("access_token"),
            "token_type": token_response.get("token_type"),
            "expires_in": token_response.get("expires_in"),
            "refresh_token": token_response.get("refresh_token"),
            "scope": token_response.get("scope")
        }
    except Exception as e:
        return {"error": f"Token exchange failed: {str(e)}"}

@mcp.tool()
async def call_protected_resource(
    access_token: str,
    resource_url: str = "http://localhost:4000/protected"
) -> dict:
    """
    Call the protected resource API (OIDC demo server) using a bearer token.
    Args:
        access_token: The OAuth access token
        resource_url: The URL of the protected resource to call (default: http://localhost:4000/protected)
    Returns:
        The response from the protected resource.
    """
    try:
        result = call_resource_api(resource_url, access_token)
        return {
            "success": True,
            "resource_url": resource_url,
            "response": result
        }
    except Exception as e:
        return {"error": f"Resource call failed: {str(e)}"}

@mcp.tool()
async def complete_oauth_flow_automated(
    auth_server: str = "http://localhost:4000",
    client_id: str = "foo",
    client_secret: str = "",
    redirect_uri: str = "http://localhost:8765/callback",
    scope: str = "email openid"
) -> dict:
    """
    Complete the entire OAuth flow automatically (opens browser, handles callback, exchanges token),
    then call the protected resource endpoint using the obtained access token.
    Args:
        auth_server: The OAuth server URL (default: http://localhost:4000)
        client_id: The OAuth client ID (default: client1)
        client_secret: The OAuth client secret (default: client1-secret)
        redirect_uri: The redirect URI for the callback (default: http://localhost:8765/callback)
        scope: The OAuth scope (default: openid)
    Returns:
        The complete OAuth flow result with access token and protected resource response.
    """
    flow_result = complete_oauth_flow(auth_server, client_id, client_secret, redirect_uri, scope)
    access_token = flow_result.get("access_token")
    protected_response = None
    if access_token:
        # Call the protected resource endpoint using the access token
        protected_response = call_resource_api("http://localhost:4000/protected", access_token)
    return {
        "oauth_flow_result": flow_result,
        "protected_resource_response": protected_response
    }


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "http":
        import uvicorn
        print("MCP HTTP server running on port 5001...")
        uvicorn.run("server.terminal_server:app", host="0.0.0.0", port=5001, reload=True)
    else:
        print("MCP server started and waiting for requests (STDIO)...")
        mcp.run(transport="stdio")
