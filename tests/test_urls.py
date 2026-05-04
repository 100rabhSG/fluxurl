"""Tests for the URL shortener API endpoints."""

from unittest.mock import patch

from httpx import AsyncClient

from app.services.shortener import ALPHABET, DEFAULT_LENGTH

# ─── POST /shorten ────────────────────────────────────────────────────────────


async def test_shorten_happy_path(client: AsyncClient):
    """POST with a valid URL returns 201 and the expected response shape."""
    resp = await client.post("/shorten", json={"url": "https://example.com/hello"})

    assert resp.status_code == 201
    data = resp.json()
    assert data["long_url"] == "https://example.com/hello"
    assert len(data["short_code"]) == DEFAULT_LENGTH
    assert all(c in ALPHABET for c in data["short_code"])
    assert data["short_url"].endswith(f"/{data['short_code']}")


async def test_shorten_invalid_url(client: AsyncClient):
    """POST with a non-URL string returns 422."""
    resp = await client.post("/shorten", json={"url": "not-a-url"})
    assert resp.status_code == 422


async def test_shorten_missing_url(client: AsyncClient):
    """POST with empty body returns 422."""
    resp = await client.post("/shorten", json={})
    assert resp.status_code == 422


async def test_shorten_collision_retries(client: AsyncClient):
    """If code generation collides, it retries and eventually succeeds."""
    # First call: generate a code normally so there's a row in the DB.
    resp1 = await client.post("/shorten", json={"url": "https://example.com/first"})
    assert resp1.status_code == 201
    existing_code = resp1.json()["short_code"]

    # Patch generate_short_code to return the same code once (collision), then a new one.
    with patch("app.api.urls.generate_short_code") as mock_gen:
        mock_gen.side_effect = [existing_code, "UniqueX"]
        resp2 = await client.post("/shorten", json={"url": "https://example.com/second"})

    assert resp2.status_code == 201
    assert resp2.json()["short_code"] == "UniqueX"


async def test_shorten_all_collisions_500(client: AsyncClient):
    """If every retry collides, returns 500."""
    # Create a row first.
    resp = await client.post("/shorten", json={"url": "https://example.com/setup"})
    existing_code = resp.json()["short_code"]

    # Patch to always return the same colliding code.
    with patch("app.api.urls.generate_short_code", return_value=existing_code):
        resp = await client.post("/shorten", json={"url": "https://example.com/boom"})

    assert resp.status_code == 500
    assert "unique short code" in resp.json()["detail"]


# ─── GET /{short_code} ────────────────────────────────────────────────────────


async def test_redirect_happy_path(client: AsyncClient):
    """GET with a valid, existing short code returns 302 with Location header."""
    # Create a short URL first.
    create_resp = await client.post("/shorten", json={"url": "https://example.com/target"})
    code = create_resp.json()["short_code"]

    resp = await client.get(f"/{code}", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "https://example.com/target"


async def test_redirect_not_found(client: AsyncClient):
    """GET with a valid-shaped but non-existent code returns 404."""
    resp = await client.get("/aB3dE7x", follow_redirects=False)
    assert resp.status_code == 404


async def test_redirect_wrong_length(client: AsyncClient):
    """GET with a code that's too short fails fast with 404."""
    resp = await client.get("/abc", follow_redirects=False)
    assert resp.status_code == 404


async def test_redirect_invalid_chars(client: AsyncClient):
    """GET with invalid characters (not base62) returns 404."""
    resp = await client.get("/abc!@#$", follow_redirects=False)
    assert resp.status_code == 404


# ─── Static routes not hijacked ───────────────────────────────────────────────


async def test_root_not_hijacked(client: AsyncClient):
    """GET / still returns the root response, not a 404 from the catch-all."""
    resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.json() == {"hello": "world"}
