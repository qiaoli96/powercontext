"""HTTP bridge backed by persistent PowerContext Source and Memory records."""

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource


class LiteralPipeline:
    async def extract(self, request, /):
        return tuple(
            MemoryEntryInput(kind="document", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


async def memory_operation(db_path, secret, request):
    op, code = request.get("op"), request.get("code")
    if op == "check":
        return {"ok": True}
    if op not in {"read", "advance"} or not isinstance(code, str) or not re.fullmatch("[A-Z0-9]{4,32}", code):
        return {"ok": False, "error": "invalid_request"}
    key = hmac.digest(secret.encode(), code.encode(), "sha256").hex()
    config = BuiltinConfig(database={"url": f"sqlite+aiosqlite:///{db_path}"})
    async with open_builtin_runtime(config, candidate_pipeline=LiteralPipeline()) as runtime:
        scope = await runtime.scopes.create(
            ScopeDraft(title="失物招领存档", summary="Dify 剧情进度", idempotency_key=f"lost-found-{key}")
        )
        memory = runtime.memory.for_scope(scope.scope_id)
        # Drain a captured event even if a prior process stopped before flush.
        while (await memory.flush()).processed:
            pass
        entries = (await memory.list()).entries
        stage = max((json.loads(record.entry.text)["stage"] for record in entries), default=0)
        if op == "advance":
            expected, target = request.get("expected"), request.get("stage")
            if type(expected) is not int or type(target) is not int or not 0 <= expected < target <= 3 or target != expected + 1:
                return {"ok": False, "error": "invalid_transition"}
            if stage == expected:
                await runtime.sources.for_scope(scope.scope_id).capture(
                    CaptureSource(
                        source_id=f"lost-found-{key[:24]}-stage-{target}",
                        content=json.dumps({"stage": target}),
                        metadata={"game": "lost-found", "stage": target},
                    )
                )
                while (await memory.flush()).processed:
                    pass
                stage = max(
                    (json.loads(record.entry.text)["stage"] for record in (await memory.list()).entries),
                    default=0,
                )
            if stage != target:
                return {"ok": False, "error": "stale_or_uncommitted", "stage": stage}
    print(f"PowerContext {op} save={key[:8]} stage={stage}", flush=True)
    return {"ok": True, "stage": stage}


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.respond(200, {"ok": True}) if self.path == "/health" else self.respond(404, {"ok": False})

    def do_POST(self):
        if self.path != "/memory":
            self.respond(404, {"ok": False})
            return
        if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), f"Bearer {self.server.secret}".encode()):
            self.respond(401, {"ok": False})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("invalid size")
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict):
                raise ValueError("expected object")
        except (ValueError, UnicodeError):
            self.respond(400, {"ok": False, "error": "invalid_json"})
            return
        try:
            result = asyncio.run(memory_operation(self.server.db_path, self.server.secret, request))
        except Exception as error:
            print(f"PowerContext operation failed: {type(error).__name__}: {error}", flush=True)
            self.respond(503, {"ok": False})
            return
        self.respond(200, result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--db", type=Path, default=Path.home() / ".local/state/dify-lost-found/memory.db")
    args = parser.parse_args()
    secret = os.environ.get("GAME_API_TOKEN", "")
    if len(secret) < 16:
        raise SystemExit("Set GAME_API_TOKEN to at least 16 characters.")
    path = args.db.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with HTTPServer((args.host, args.port), Handler) as server:
        server.secret = secret
        server.db_path = path
        print(f"PowerContext memory bridge listening on {args.host}:{server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
