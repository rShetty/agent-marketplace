from __future__ import annotations

import pytest

from routers.mcp_oauth import _register_client


@pytest.mark.asyncio
async def test_pre_registered_client_identity_wins_over_dcr():
    class Server:
        oauth_client_id = "atlassian-client-id"
        oauth_client_secret = "atlassian-secret"
        oauth_encrypted = ""

    metadata = {"registration_endpoint": "https://provider.test/register"}
    redirect_uri = "https://hub.test/api/mcp/oauth/callback"

    client_id, client_secret = await _register_client(Server(), metadata, redirect_uri)

    assert (client_id, client_secret) == ("atlassian-client-id", "atlassian-secret")


@pytest.mark.asyncio
async def test_dynamic_registration_is_used_without_cimd():
    class Server:
        oauth_client_id = None
        oauth_client_secret = None
        oauth_encrypted = ""

    class Response:
        status_code = 201
        def json(self):
            return {"client_id": "dcr-client", "client_secret": "dcr-secret"}
        @property
        def text(self):
            return ""

    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, *args, **kwargs):
            return Response()

    metadata = {
        "registration_endpoint": "https://provider.test/register",
        "authorization_endpoint": "https://provider.test/authorize",
        "token_endpoint": "https://provider.test/token",
    }
    from routers import mcp_oauth
    original = mcp_oauth.httpx.AsyncClient
    mcp_oauth.httpx.AsyncClient = lambda **kwargs: Client()
    try:
        client_id, _client_secret = await _register_client(
            Server(), metadata, "https://hub.test/callback"
        )
    finally:
        mcp_oauth.httpx.AsyncClient = original

    assert client_id == "dcr-client"
