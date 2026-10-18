"""Treasurer: the internal Claude agent that holds Airlock's tools.

It sees only the Auditor's verdict (decision, reason, flags, facts, next_action), never the raw email, and its
payment tool accepts nothing but an authorization_id. If it were fooled or hallucinated an id, the executor would
still refuse: the agent can propose, only Airlock records can authorize.
"""
import json
import re
import logging

import anthropic

from . import config, pipeline

log = logging.getLogger("airlock.treasurer")
MODEL = "claude-opus-5-5"

SYSTEM = """You are Treasurer, the accounts-payable agent for Kiln Supply Co. You work one case at a time.
Airlock (deterministic policy code) has already decided what is allowed and tells you the next_action.
Follow next_action using your tools. Only use ids that appear in the verdict. Never invent ids, accounts, or amounts.
When done, write ONE short sentence (under 25 words) for the case thread saying what you did and why.
Don't comment on email addresses or routing; Airlock already checked them. No markdown."""

TOOLS = [
    {"name": "execute_payment", "strict": True,
     "description": "Execute a payment Airlock already authorized. Takes only the authorization_id from the verdict.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["authorization_id"],
                      "properties": {"authorization_id": {"type": "string"}}}},
    {"name": "request_vendor_verification", "strict": True,
     "description": "Ask the vendor to confirm a requested bank change through the phone number already on file.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["change_request_id"],
                      "properties": {"change_request_id": {"type": "string"}}}},
    {"name": "reply_to_vendor", "strict": True,
     "description": "Send the vendor a templated reply in the original email thread.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["template"],
                      "properties": {"template": {"type": "string",
                                                  "enum": ["already_paid", "ask_vendor_to_explain_or_reissue",
                                                           "ask_vendor_to_resend", "verification_pending"]}}}},
    {"name": "escalate_to_admin", "strict": True,
     "description": "Flag the case for a human admin with a short reason.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["reason"],
                      "properties": {"reason": {"type": "string"}}}},
]

_client: anthropic.Anthropic | None = None


def _anthropic() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.need("ANTHROPIC_API_KEY"), max_retries=3)
    return _client


def compact(verdict: dict) -> dict:
    """What Treasurer is allowed to see: the verdict, no raw content."""
    keep = ("vendor_id", "vendor_name", "invoice_number", "po_number", "claimed_cents", "obligation_cents", "amount_cents",
            "destination", "change_request_id", "authorization_id", "deviation_pct", "standing_limit")
    facts = {k: v for k, v in verdict["facts"].items() if k in keep and v is not None}
    # The claimed invoice number is attacker-controlled text: only a strict token shape reaches a tool-holding model.
    if "invoice_number" in facts and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,31}", str(facts["invoice_number"])):
        facts["invoice_number"] = "(unparseable)"
    return {"decision": verdict["decision"], "reason": verdict["reason"],
            "flags": [re.sub(r"[^A-Z0-9_:.~-]", "", f.upper())[:60] for f in verdict["flags"]],
            "next_action": verdict["next_action"],
            "facts": facts,
            **({"authorization_id": verdict["authorization_id"]} if verdict.get("authorization_id") else {})}


def run(case_id: str, verdict: dict, handlers: dict) -> dict:
    """One agent turn-loop over a verdict. `handlers` maps tool name -> callable(**input) -> dict."""
    messages = [{"role": "user", "content": f"Case {case_id}. Airlock verdict:\n{json.dumps(compact(verdict))}"}]
    actions = []
    for _ in range(6):
        resp = _anthropic().messages.create(model=MODEL, max_tokens=2000, system=SYSTEM, tools=TOOLS,
                                            output_config={"effort": "low"}, messages=messages)
        if resp.stop_reason == "refusal":
            return {"summary": "Treasurer declined to act; escalating to admin.", "actions": actions}
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if b.type == "tool_use"]
        if not uses:
            summary = " ".join(b.text for b in resp.content if b.type == "text").strip()
            return {"summary": summary, "actions": actions}
        results = []
        for u in uses:
            try:
                out = handlers[u.name](**u.input)
            except Exception as e:
                out = {"ok": False, "error": str(e)[:200]}
            actions.append({"tool": u.name, "input": u.input, "result": out})
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": json.dumps(out, default=str)})
        messages.append({"role": "user", "content": results})
    return {"summary": "Treasurer stopped after too many steps; escalating.", "actions": actions}
