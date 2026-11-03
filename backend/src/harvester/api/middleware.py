from starlette.types import ASGIApp, Receive, Scope, Send

from harvester.api.errors import error_response

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def origin_check(app: ASGIApp, site_origin: str) -> ASGIApp:
    """会改数据的请求，Origin 必须和 SITE_ORIGIN 完全相同（AC-10）。

    写成纯 ASGI：排在限速和解析请求体之前，也不会缓冲 SSE。
    """
    expected = site_origin.encode()

    async def middleware(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in _MUTATING_METHODS:
            origin = dict(scope["headers"]).get(b"origin")
            if origin != expected:
                response = error_response(403, "bad_origin", "请求来源不被允许")
                await response(scope, receive, send)
                return
        await app(scope, receive, send)

    return middleware
