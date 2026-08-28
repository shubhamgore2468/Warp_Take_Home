"""Prose only. Runs after pricing.py — the model here reads numbers, never
produces them. It writes deal_summary, per-lane rationale, assumptions, and
open_questions from the transcript plus the already-priced proposal.
"""
import json
from src.llm import get_llm_client
from src.state import Proposal, ProposalProse

SYSTEM_PROMPT = """
You write the prose parts of a freight proposal for a sales rep to read aloud
on a call. You are given the transcript and a proposal whose prices are
already final — copy numbers into sentences if useful, but never invent or
change a dollar figure, discount, or rate.

Write:
- deal_summary: 2-3 sentences describing the customer's freight in their own terms.
- lane_rationales: one sentence per PRICEABLE lane on why this mode and service level.
- assumptions: anything you inferred rather than heard (e.g. an unstated dock assumption).
- open_questions: what the rep should confirm before this becomes a contract.

Respond ONLY with a valid JSON object matching the schema.
"""


def generate_prose(proposal: Proposal, transcript: str) -> ProposalProse:
    client = get_llm_client()
    priceable = [l for l in proposal.lanes if l.serviceable]
    context = {
        "customer": proposal.customer.model_dump(),
        "priceable_lanes": [
            {
                "origin_metro": l.origin_metro, "dest_metro": l.dest_metro,
                "pallets_per_shipment": l.pallets_per_shipment,
                "mode_quoted": l.mode_quoted, "service_level": l.service_level,
                "accessorials": l.accessorials,
                "shipment_total": l.pricing.shipment_total if l.pricing else None,
            }
            for l in priceable
        ],
        "excluded": [e.model_dump() for e in proposal.excluded],
        "monthly_total": proposal.monthly_total,
        "volume_tier": proposal.volume_tier.model_dump(),
    }
    system = f"{SYSTEM_PROMPT} {json.dumps(ProposalProse.model_json_schema(), indent=2)}"
    user = (f"Transcript:\n{transcript}\n\n"
            f"Priced proposal (read-only):\n{json.dumps(context, indent=2)}")
    raw_json = client.complete(system, user)
    return ProposalProse.model_validate_json(raw_json)


def apply_prose(proposal: Proposal, prose: ProposalProse) -> None:
    """Mutates proposal in place — matches rationale to lanes by metro pair
    since the model's list order isn't guaranteed to match ours."""
    proposal.deal_summary = prose.deal_summary
    proposal.assumptions = prose.assumptions
    proposal.open_questions = prose.open_questions

    by_pair = {(r.origin_metro, r.dest_metro): r.rationale for r in prose.lane_rationales}
    for lane in proposal.lanes:
        if lane.serviceable:
            lane.rationale = by_pair.get((lane.origin_metro, lane.dest_metro))
