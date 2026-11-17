"""CORS: only the origins in CORS_ORIGINS may call the API from a browser. Never a wildcard."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config import Settings, get_settings
from backend.main import add_cors, app

FRONT_END = "http://localhost:5173"


def preflight(client: TestClient, origin: str, path: str = "/documents/upload", method: str = "POST"):
    return client.options(path, headers={"Origin": origin, "Access-Control-Request-Method": method, "Access-Control-Request-Headers": "content-type"})


def test_the_default_allowed_origin_is_the_vite_dev_server():
    assert Settings(_env_file=None).cors_origin_list == [FRONT_END]


def test_the_real_app_allows_the_configured_front_end_and_refuses_other_sites():
    client = TestClient(app)
    allowed = get_settings().cors_origin_list[0]
    good = preflight(client, allowed)
    assert good.status_code == 200 and good.headers["access-control-allow-origin"] == allowed
    assert "access-control-allow-credentials" not in good.headers                           # no cookies are involved
    bad = preflight(client, "https://evil.example")
    assert bad.status_code == 400 and "access-control-allow-origin" not in bad.headers
    plain = client.get("/health", headers={"Origin": "https://evil.example"})
    assert plain.status_code == 200 and "access-control-allow-origin" not in plain.headers   # the browser would block the answer


def test_a_simple_request_from_the_front_end_gets_the_allow_header():
    response = TestClient(app).get("/health", headers={"Origin": get_settings().cors_origin_list[0]})
    assert response.headers["access-control-allow-origin"] == get_settings().cors_origin_list[0]


def build(origins: str) -> TestClient:
    other = FastAPI()
    add_cors(other, Settings(_env_file=None, cors_origins=origins))

    @other.get("/ping")
    def ping():
        return {"ok": True}
    return TestClient(other)


def test_several_origins_can_be_listed_and_spaces_and_trailing_slashes_are_ignored():
    client = build(" http://localhost:5173/ , https://app.example.com ")
    for origin in ("http://localhost:5173", "https://app.example.com"):
        assert client.get("/ping", headers={"Origin": origin}).headers["access-control-allow-origin"] == origin
    assert "access-control-allow-origin" not in client.get("/ping", headers={"Origin": "https://other.example.com"}).headers


def test_a_wildcard_is_refused():
    with pytest.raises(ValueError, match="not allowed"):
        Settings(_env_file=None, cors_origins="*").cors_origin_list
    with pytest.raises(ValueError, match="not allowed"):
        Settings(_env_file=None, cors_origins="http://localhost:5173, *").cors_origin_list
