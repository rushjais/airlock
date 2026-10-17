"""Settings from ~/airlock/.env.

Invariant 1: STRIPE_SECRET_KEY is never loaded here. Only airlock.executor reads it, in its own process.
"""
import os
import pathlib

from dotenv import dotenv_values

ROOT = pathlib.Path(__file__).resolve().parents[2]
_ENV = {k: v for k, v in dotenv_values(ROOT / ".env").items() if k != "STRIPE_SECRET_KEY"}

FIXTURES = ROOT / "fixtures"
SCENARIOS = ROOT / "scenarios"


def get(name: str, default: str | None = None) -> str | None:
    if name == "STRIPE_SECRET_KEY":
        raise RuntimeError("STRIPE_SECRET_KEY is only available to the payment executor")
    value = os.environ.get(name) or _ENV.get(name)
    return value if value else default


def need(name: str) -> str:
    value = get(name)
    if not value:
        raise RuntimeError(f"{name} is not set in {ROOT / '.env'}")
    return value


DATABASE_URL = get("DATABASE_URL")
INBOX_DOMAIN = (get("AGENT_INBOX_ADDRESS", "ap@wise-egret.primitive.email") or "").split("@")[-1]
AGENT_INBOX = get("AGENT_INBOX_ADDRESS", f"ap@{INBOX_DOMAIN}")
EXECUTOR_URL = get("EXECUTOR_URL", "http://127.0.0.1:8001")
