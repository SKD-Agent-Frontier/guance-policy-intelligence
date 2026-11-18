"""Reject oversized export and workbook requests before body parsing."""
from __future__ import annotations

from collections import deque

EXPORT_REQUEST_BYTES = 2_000_000
WORKBOOK_REQUEST_BYTES = 3 * 1024 * 1024 + 64 * 1024


class RequestBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path.startswith("/api/exports/"):
            limit = EXPORT_REQUEST_BYTES
        elif path == "/api/validation-batches/preview":
            limit = WORKBOOK_REQUEST_BYTES
        else:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = 0
        if declared > limit:
            await self._reject(send)
            return

        received = 0
        buffered = deque()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    await self._reject(send)
                    return
            buffered.append(message)
            if not message.get("more_body", False):
                break

        async def replay_receive():
            return buffered.popleft() if buffered else await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(send):
        body = '{"detail":"请求内容过大"}'.encode()
        await send({"type": "http.response.start", "status": 413, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]})
        await send({"type": "http.response.body", "body": body})
