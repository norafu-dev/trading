"""AC-1、AC-2：受保护是默认值。"""

import httpx
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute, iter_route_contexts

from harvester.api.deps import PUBLIC_API_PATHS, require_session
from harvester.api.main import create_app
from tests.api.conftest import UNUSED_LINK_BASE, api_settings, crypto_settings


def unguarded_api_routes(app: FastAPI) -> list[str]:
    """不在白名单里又没有 require_session 的 /api 路由，包括 WebSocket 和 mount。"""
    found: list[str] = []
    for context in iter_route_contexts(app.routes):
        path = context.path or ""
        if not path.startswith("/api") or path in PUBLIC_API_PATHS:
            continue
        route = context.original_route
        dependant = context.dependant if isinstance(route, APIRoute | APIWebSocketRoute) else None
        guarded = dependant is not None and any(
            d.call is require_session for d in dependant.dependencies
        )
        if not guarded:
            found.append(path)
    return found


def all_api_routes(app: FastAPI) -> list[tuple[str, str]]:
    return [
        (method, context.path or "")
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute)
        for method in context.methods or ()
    ]


def test_every_api_route_requires_session_or_is_whitelisted(app: FastAPI) -> None:  # covers: AC-2
    assert unguarded_api_routes(app) == []
    # 确认遍历真的走到了路由器里面。
    paths = {path for _, path in all_api_routes(app)}
    assert {"/api/health", "/api/events", "/api/auth/login-events", "/api/auth/login"} <= paths


def test_route_walk_catches_unguarded_routes(app: FastAPI) -> None:  # covers: AC-2
    async def rogue() -> None: ...

    router = APIRouter(prefix="/api")
    router.get("/rogue")(rogue)
    router.websocket("/rogue-ws")(rogue)
    app.include_router(router)
    app.mount("/api/rogue-mount", FastAPI())

    assert set(unguarded_api_routes(app)) >= {"/api/rogue", "/api/rogue-ws", "/api/rogue-mount"}


async def test_black_box_every_route_without_cookie(
    app: FastAPI, client: httpx.AsyncClient
) -> None:  # covers: AC-1, AC-2
    protected = [(m, p) for m, p in all_api_routes(app) if p not in PUBLIC_API_PATHS]
    assert len(protected) >= 3
    for method, path in protected:
        response = await client.request(method, path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "unauthenticated"


async def test_health_and_events_are_protected(client: httpx.AsyncClient) -> None:  # covers: AC-1
    for path in ("/api/health", "/api/events"):
        response = await client.get(path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"


async def test_livez_is_public_and_needs_no_database(
    client: httpx.AsyncClient,
) -> None:  # covers: AC-1
    response = await client.get("/api/livez")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


async def test_production_has_no_docs() -> None:  # covers: AC-1
    app = create_app(
        api_settings(secure=True, origin="https://dash.example.com"),
        crypto_settings(),
        media_link_base=UNUSED_LINK_BASE,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://dash.example.com"
    ) as c:
        assert (await c.get("/api/docs")).status_code == 404
        assert (await c.get("/api/openapi.json")).status_code == 404
