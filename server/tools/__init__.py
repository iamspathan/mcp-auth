from .oauth_tools import (
    generate_authorization_url,
    get_bearer_token,
    call_resource_api,
    complete_oauth_flow
)

TOOLS = [
    generate_authorization_url,
    get_bearer_token,
    call_resource_api,
    complete_oauth_flow,
]