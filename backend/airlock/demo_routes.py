"""HTTP surface of DEMO_MODE: sandbox cookie, rate limits, the Try-it API, and the built frontend.

Only an allowlist of routes is reachable in demo mode. Everything else (the email webhook, local-only demo
controls, the agent API) returns 404, so the hosted demo can't send mail, text anyone, or touch a real inbox.
"""
import logging
import pathlib
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import config, db, demo

log = logging.getLogger("airlock.demo")
DIST = config.ROOT / "frontend" / "dist"
API_ALLOW = re.compile(r"^/api/(try/|approvals/|(cases|summary|money|messages|controls|mail|results)(/|$))")
_rail_cache: dict = {}


def _rail() -> str:
    """'simulated' or 'stripe_test', as reported by the executor process (the only one that knows the key)."""
    if "rail" not in _rail_cache:
        import httpx
        try:
            _rail_cache["rail"] = httpx.get(f"{config.EXECUTOR_URL}/health", timeout=3).json().get("rail", "unknown")
        except Exception:
            return "unknown"
    return _rail_cache["rail"]


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else request.client.host) or "?"


def mount(app: FastAPI) -> None:
    @app.middleware("http")
    async def sandbox_middleware(request: Request, call_next):
        path = request.url.path
        if path == "/health" or not path.startswith(("/api/", "/webhooks", "/agent", "/mcp", "/public", "/docs",
                                                         "/openapi", "/redoc")):
            return await call_next(request)  # the built frontend (static files) and the health check
        if not API_ALLOW.match(path):
            return JSONResponse({"detail": "not available in the hosted demo"}, status_code=404)
        if request.method != "GET" and path.startswith("/api/") and not path.startswith(("/api/try/", "/api/approvals/")):
            return JSONResponse({"detail": "read-only in the hosted demo"}, status_code=403)
        ip = _ip(request)
        name = request.cookies.get(demo.COOKIE)
        fresh = None
        from starlette.concurrency import run_in_threadpool
        if not await run_in_threadpool(demo.valid, name):
            if not demo.allow(ip, "new", demo.NEW_PER_IP_HOUR):
                return JSONResponse({"detail": "Too many new demo sessions from your network. Try again in an hour."},
                                    status_code=429)
            try:
                name = fresh = await run_in_threadpool(demo.claim)
            except Exception:
                log.exception("sandbox build failed")
                return JSONResponse({"detail": "Couldn't start a demo session. Try again in a moment."}, status_code=503)
        if request.method != "GET" and not demo.allow(ip, "action", demo.ACTIONS_PER_IP_HOUR):
            return JSONResponse({"detail": "Rate limit reached. Try again later."}, status_code=429)
        token = db.SANDBOX.set(name)
        try:
            response = await call_next(request)
        finally:
            db.SANDBOX.reset(token)
        if fresh:
            response.set_cookie(demo.COOKIE, fresh, max_age=demo.IDLE_TTL_S, httponly=True, samesite="lax",
                                secure=request.headers.get("x-forwarded-proto") == "https")
        return response

    @app.get("/api/try/state")
    def state():
        return {"inbox": demo.inbox(),
                "attacks": [{"id": k, "title": v["title"], "pitch": v["pitch"]} for k, v in demo.ATTACKS.items()],
                "rail": _rail()}

    @app.post("/api/try/send/{attack}")
    def send(attack: str):
        if attack not in demo.ATTACKS:
            raise HTTPException(404)
        if db.one("select count(*) n from messages")["n"] > 120:
            return JSONResponse({"detail": "This demo session is full. Press Reset to start over."}, status_code=429)
        out = demo.send(attack)
        return {**out, "view": demo.view(out["case_id"]) if out.get("case_id") else None}

    @app.get("/api/try/case/{case_id}")
    def case(case_id: str):
        v = demo.view(case_id)
        if v is None:
            raise HTTPException(404)
        return v

    class PhoneReply(BaseModel):
        answer: str
        code: str = ""

    @app.post("/api/try/phone/{change_request_id}")
    def phone(change_request_id: str, body: PhoneReply):
        out = demo.phone_reply(change_request_id, body.answer.lower(), body.code[:12])
        if out.get("ok"):
            out["view"] = demo.view(out["case_id"])
        return out

    @app.post("/api/try/counterfactual/{case_id}")
    def counterfactual(case_id: str):
        return demo.counterfactual(case_id)

    @app.post("/api/try/reset")
    def reset(request: Request):
        old = db.SANDBOX.get()
        name = demo.claim()
        if old:
            demo.drop(old)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(demo.COOKIE, name, max_age=demo.IDLE_TTL_S, httponly=True, samesite="lax",
                        secure=request.headers.get("x-forwarded-proto") == "https")
        return resp

    if DIST.exists():
        from fastapi.staticfiles import StaticFiles
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            f = DIST / path
            if path and f.is_file() and pathlib.Path(f).resolve().is_relative_to(DIST.resolve()):
                return FileResponse(f)
            return FileResponse(DIST / "index.html")
