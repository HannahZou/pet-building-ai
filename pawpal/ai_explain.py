"""Plain-language explanations of scheduling recommendations.

The LLM only *explains* a decision that deterministic code already made. It
receives a small fact sheet (first names, dates, counts, reasons - never medical
notes, special instructions or prices) and is told not to change anything.
If no API key is configured or the call fails, a template explanation is used.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from . import config
from .scheduling import Recommendation

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You explain a pet-care schedule that a deterministic scheduler has ALREADY decided.
Rules:
- Use only the facts in the JSON. Do not invent caregivers, times, dates, prices or reasons.
- Do not change, add or remove any assignment; do not promise availability.
- Never mention prices or money.
- 3 to 5 short sentences, warm and professional, addressed to the resident.
- If visits are unassigned, say so plainly and mention the listed alternatives, noting they need the resident's approval."""


def template_explanation(facts: dict[str, Any]) -> str:
    total, assigned = facts["total_visits"], facts["assigned_visits"]
    per = facts["visits_per_caregiver"]
    parts = []
    if per:
        who = ", ".join(f"{name} ({n} visit{'s' if n != 1 else ''})" for name, n in per.items())
        parts.append(f"We matched {assigned} of {total} visits with {who}.")
    if len(per) == 1 and assigned == total:
        parts.append("One caregiver covers the whole stay, so your pet sees a familiar face every visit.")
    top = facts["top_candidates"][:1]
    if top and top[0]["already_in_building"]:
        parts.append(
            f"{top[0]['first_name']} already has {top[0]['already_in_building']} visit(s) in {facts['building']} "
            "on these dates, which keeps travel short."
        )
    if facts["unassigned"]:
        parts.append(f"{len(facts['unassigned'])} visit(s) could not be matched in your requested windows.")
        if facts["alternatives"]:
            a = facts["alternatives"][0]
            parts.append(f"For example, {a['caregiver']} is free on {a['date']} at {a['time']}; alternatives need your approval.")
        else:
            parts.append("No nearby alternative times are free; please choose different windows or dates.")
    if not parts:
        parts.append("No caregiver is currently available for this request.")
    return " ".join(parts)


def explain_recommendation(rec: Recommendation, client: Any | None = None) -> tuple[str, str]:
    """Return (explanation, source) where source is 'ai' or 'template'."""
    facts = rec.facts()
    if client is None:
        key = config.openai_api_key()
        if not key:
            return template_explanation(facts), "template"
        try:
            from openai import OpenAI

            client = OpenAI(api_key=key)
        except Exception:  # library missing or misconfigured
            return template_explanation(facts), "template"
    try:
        completion = client.chat.completions.create(
            model=config.openai_model(),
            temperature=0.2,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(facts, default=str)},
            ],
        )
        text = (completion.choices[0].message.content or "").strip()
        if not text:
            raise ValueError("empty")
        return text, "ai"
    except Exception as exc:
        logger.warning("AI explanation failed: %s", type(exc).__name__)
        return template_explanation(facts), "template"
