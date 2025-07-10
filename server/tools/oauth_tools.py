import os
import requests

def get_bearer_token(auth_server, client_id, client_secret, code, redirect_uri):
    token_url = f"{auth_server}/oauth/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    response = requests.post(token_url, data=data)
    response.raise_for_status()
    return response.json()["access_token"]

def call_resource_api(resource_url, bearer_token):
    headers = {"Authorization": f"Bearer {bearer_token}"}
    response = requests.get(resource_url, headers=headers)
    response.raise_for_status()
    return response.json()

# Usage example:
auth_server = os.getenv("AUTH_SERVER")
client_id = os.getenv("CLIENT1_CLIENT_ID")
client_secret = os.getenv("CLIENT1_CLIENT_SECRET")
redirect_uri = os.getenv("CLIENT1_CALLBACK")
resource_url = "http://localhost:3000/api/aps/status"  # Example

# Step 1: User logs in and gets 'code' (manually or via browser automation)
code = input("Paste the authorization code here: ")

# Step 2: Exchange code for token
token = get_bearer_token(auth_server, client_id, client_secret, code, redirect_uri)

# Step 3: Call resource API
result = call_resource_api(resource_url, token)
print(result)