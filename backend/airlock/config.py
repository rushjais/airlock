"""Settings from ~/airlock/.env.

Invariant 1: STRIPE_SECRET_KEY is never loaded here. Only airlock.executor reads it, in its own process.
"""
import os
import pathlib

from dotenv import dotenv_values

ROOT = pathlib.Path(__file__).resolve().parents[2]
# AIRLOCK_NO_DOTENV=1 ignores ../.env entirely (run exactly as the container does, with only real env vars).
_NO_DOTENV = os.environ.get("AIRLOCK_NO_DOTENV") == "1"
_ENV = {} if _NO_DOTENV else {k: v for k, v in dotenv_values(ROOT / ".env").items() if k != "STRIPE_SECRET_KEY"}

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
DB_SCHEMA = get("AIRLOCK_DB_SCHEMA", "airlock")  # tests use airlock_test so they never touch the live demo
INBOX_DOMAIN = (get("AGENT_INBOX_ADDRESS", "ap@wise-egret.primitive.email") or "").split("@")[-1]
AGENT_INBOX = get("AGENT_INBOX_ADDRESS", f"ap@{INBOX_DOMAIN}")
EXECUTOR_URL = get("EXECUTOR_URL", "http://127.0.0.1:8001")

# DEMO_MODE: the hosted, no-sign-up demo. No model calls (the reader replays recorded Claude output), no email or
# texts leave the server (the vendor's phone is simulated on screen), and payments go to a simulated rail unless a
# Stripe TEST key is given to the executor. Every visitor gets their own sandbox schema. Needs only DATABASE_URL.
DEMO_MODE = (get("DEMO_MODE") or "").lower() in ("1", "true", "yes")
RECORDINGS = ROOT / "demo" / "reads"
