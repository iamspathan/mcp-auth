# demo_oidc.py

from flask import Flask, request, render_template_string, redirect
from authlib.integrations.flask_oauth2 import AuthorizationServer
from authlib.oauth2.rfc6749 import grants
from authlib.oidc.core import UserInfo
from authlib.oidc.core.grants import OpenIDCode
import time

# --- In-memory “database” ---
CLIENTS = {
    "foo": {
        "client_id": "foo",
        # No client_secret for public client
        "redirect_uris": [
            "http://localhost:3000/callback",
            "http://localhost:8765/callback"
        ],
        "scope": "openid email",
        "token_endpoint_auth_method": "none",
    }
}
AUTH_CODES = {}
TOKENS = {}

# Dummy user
class User:
    def __init__(self, uid, email):
        self.user_id = uid
        self.email = email

demo_user = User("123", "alice@example.com")

# --- OAuth2 grant definition ---
class MyOpenIDCode(OpenIDCode):
    def generate_user_info(self, user, scope):
        # For demo, return minimal OIDC claims
        return {
            "sub": user.user_id,
            "email": user.email,
        }
    def get_jwt_config(self, grant):
        return {
            "key": app.config["SECRET_KEY"],
            "alg": "HS256",
            "iss": "http://localhost:4000",
            "aud": "http://localhost:5000",
        }

class AuthorizationCodeGrant(grants.AuthorizationCodeGrant):
    TOKEN_ENDPOINT_AUTH_METHODS = ["client_secret_basic", "none"]
    def generate_user_info(self, user, scope):
        # For demo, return minimal OIDC claims
        return {
            "sub": user.user_id,
            "email": user.email,
        }
    def get_jwt_config(self, grant):
        # Use your Flask secret key for demo purposes (not secure for production!)
        return {
            "key": app.config["SECRET_KEY"],
            "alg": "HS256",
            "iss": "http://localhost:4000",
            "aud": "http://localhost:5000",
        }
    def save_authorization_code(self, code, request):
        # store code → (client_id, redirect_uri, scope, user)
        AUTH_CODES[code] = {
            "client_id": request.client.client_id,
            "redirect_uri": request.redirect_uri,
            "scope": request.scope,
            "user": request.user,
        }

    def query_authorization_code(self, code, client):
        data = AUTH_CODES.get(code)
        if data and data["client_id"] == client.client_id:
            class AuthorizationCode:
                def __init__(self, data):
                    self.data = data
                    self.code = code
                    self.client_id = data["client_id"]
                    self.redirect_uri = data["redirect_uri"]
                    self.scope = data["scope"]
                    self.user = data["user"]
                def get_redirect_uri(self):
                    return self.redirect_uri
                def get_scope(self):
                    return self.scope
                def get_client_id(self):
                    return self.client_id
                def get_nonce(self):
                    return None
                def get_auth_time(self):
                    # For demo, return current time
                    return int(time.time())
                def get_acr(self):
                    # For demo, return None or a static ACR value
                    return None
                def get_amr(self):
                    # For demo, return None or a static AMR value
                    return None
            return AuthorizationCode(data)
    def delete_authorization_code(self, authorization_code):
        AUTH_CODES.pop(authorization_code.code, None)
    def authenticate_user(self, authorization_code):
        return authorization_code.user

# --- Helper callbacks ---
def query_client(client_id):
    info = CLIENTS.get(client_id)
    if not info:
        return None
    class Client:
        def check_token_endpoint_auth_method(self, method):
            return method == self.token_endpoint_auth_method
        def get_client_auth_method(self):
            return self.token_endpoint_auth_method
        def __init__(self, info):
            self.client_id = info["client_id"]
            self.client_secret = info.get("client_secret")
            self.redirect_uris = info["redirect_uris"]
            self.scope = info["scope"]
            self.token_endpoint_auth_method = info.get("token_endpoint_auth_method", "client_secret_basic")
        def check_redirect_uri(self, uri):
            return uri in self.redirect_uris
        def check_client_secret(self, secret):
            if self.token_endpoint_auth_method == "none":
                return True
            return secret == self.client_secret
        def check_response_type(self, response_type):
            # Only 'code' is supported in this demo
            return response_type == "code"
        def check_endpoint_auth_method(self, method, endpoint):
            # Support 'none' for public clients
            return method == self.token_endpoint_auth_method
        def check_grant_type(self, grant_type):
            # Only 'authorization_code' is supported in this demo
            return grant_type == "authorization_code"
        def get_allowed_scope(self, scope):
            # Only allow scopes that are registered for this client
            allowed = set(self.scope.split())
            requested = set(scope.split())
            return " ".join(allowed & requested)
        def get_client_id(self):
            return self.client_id
        def get_default_redirect_uri(self):
            # Return the first redirect URI as default
            return self.redirect_uris[0] if self.redirect_uris else None
        def check_scope(self, scope):
            # Check if all requested scopes are allowed
            allowed = set(self.scope.split())
            requested = set(scope.split())
            return requested.issubset(allowed)
        def check_requested_scope(self, scope):
            # Alias for check_scope
            return self.check_scope(scope)
        def get_audiences(self):
            # For demo, audience is client_id
            return [self.client_id]
    return Client(info)


def save_token(token, request):
    # For demo: use the id_token as the access_token (so access_token is a JWT)
    if "id_token" in token:
        token["access_token"] = token["id_token"]
    TOKENS[token["access_token"]] = token

# --- Flask app & server setup ---

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret-key"
auth_server = AuthorizationServer()
auth_server.init_app(app, query_client=query_client, save_token=save_token)
auth_server.register_grant(AuthorizationCodeGrant, [
    MyOpenIDCode(require_nonce=False)  # enable OpenID Connect
])

# --- Well-known metadata endpoint ---
from flask import jsonify, url_for

@app.route("/.well-known/oauth-authorization-server")
def well_known_oauth_metadata():
    issuer = "http://localhost:4000"
    return jsonify({
        "issuer": issuer,
        "authorization_endpoint": issuer + "/auth",
        "token_endpoint": issuer + "/token",
        "userinfo_endpoint": issuer + "/userinfo",
        "jwks_uri": issuer + "/jwks.json",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["HS256"],
        "scopes_supported": ["openid", "email"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "none"],
        "claims_supported": ["sub", "email"]
    })

# --- Routes ---
login_page = """
<form method="post">
  <p>Login as demo user (no creds)</p>
  <button type="submit">Continue</button>
</form>
"""

@app.route("/auth", methods=["GET", "POST"])
def authorize():
    if request.method == "GET":
        # build consent page
        grant = auth_server.get_consent_grant(end_user=demo_user)
        return render_template_string(
            login_page + "<p>Requesting scopes: {{grant.request.scope}}</p>",
            grant=grant
        )
    # user “logs in” and grants consent
    return auth_server.create_authorization_response(grant_user=demo_user)

@app.route("/token", methods=["POST"])
def issue_token():
    print("/token endpoint called")
    print("Request headers:", dict(request.headers))
    print("Request form:", dict(request.form))
    print("Request data:", request.data)
    resp = auth_server.create_token_response()
    # Patch: replace access_token with id_token in the response body
    if resp.status_code == 200 and resp.is_json:
        data = resp.get_json()
        if "id_token" in data:
            data["access_token"] = data["id_token"]
            import json as _json
            resp.set_data(_json.dumps(data))
    return resp

@app.route("/userinfo")
def userinfo():
    # simple OIDC userinfo endpoint
    token = request.headers.get("Authorization", "").split()[-1]
    data = TOKENS.get(token)
    if not data or "openid" not in data.get("scope", ""):
        return {"error": "invalid_token"}, 401
    user = data["user"]
    return UserInfo(sub=user.user_id, email=user.email)

if __name__ == "__main__":
    app.run(port=4000)
