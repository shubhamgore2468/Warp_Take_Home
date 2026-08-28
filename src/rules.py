"""--no-ai fallback. Regex/keyword only, zero LLM calls. Not smart — just
deterministic and non-crashing, so a rep isn't stuck when the API is down.
"""
import re
from src.pricing import RateData
from src.state import ExtractionState, ExtractedLane, Proposal, ProposalProse, LaneRationale

_ONES = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: i * 10 for i, w in enumerate(
    "zero ten twenty thirty forty fifty sixty seventy eighty ninety".split()) if i >= 2}
_SCALES = {"hundred": 100, "thousand": 1000}
_NUM_WORD = "|".join(sorted({*_ONES, *_TENS, *_SCALES, "and"}, key=len, reverse=True))
_NUM_PHRASE = re.compile(rf"\b(?:(?:{_NUM_WORD})[\s-]*)+\b", re.I)


def _phrase_to_int(phrase: str) -> int:
    total = current = 0
    for word in re.findall(r"[a-z]+", phrase.lower()):
        if word == "and":
            continue
        if word in _SCALES:
            current = (current or 1) * _SCALES[word]
        else:
            current += _ONES.get(word, _TENS.get(word, 0))
        if word == "thousand":
            total += current
            current = 0
    return total + current


def _words_to_digits(text: str) -> str:
    """'six hundred' -> '600', so the existing \\d+ regexes still work."""
    return _NUM_PHRASE.sub(lambda m: str(_phrase_to_int(m.group())), text)


_rates = None


def _get_rates() -> RateData:
    global _rates
    if _rates is None:
        _rates = RateData()
    return _rates


def _find_lane_card(text: str):
    text_low = text.lower()
    for card in _get_rates().rate_card.values():
        if card["origin_metro"].lower() in text_low and card["dest_metro"].lower() in text_low:
            return card
    return None


def rules_extract_turn(state: ExtractionState, turn_text: str) -> ExtractionState:
    """Same shape as extract.extract_turn, no model call. Only tracks one
    lane — good enough for a fallback, not meant to replace the LLM."""
    turn_text = _words_to_digits(turn_text)

    if not state.lanes:
        card = _find_lane_card(turn_text)
        if card:
            state.lanes.append(ExtractedLane(
                origin_metro=card["origin_metro"], origin_state=card["origin_state"],
                dest_metro=card["dest_metro"], dest_state=card["dest_state"],
            ))

    if state.lanes:
        lane = state.lanes[0]
        if m := re.search(r"(\d+)\s*(?:pallets?|skids?)", turn_text, re.I):
            lane.pallets_per_shipment = float(m.group(1))
        if m := re.search(r"(\d+)\s*(?:pounds?|lbs?)\s*(?:a|per|each)?\s*pallet", turn_text, re.I):
            lane.weight_lb_per_pallet = float(m.group(1))
        if m := re.search(r"(\d+)\s*(?:a|per)\s*month", turn_text, re.I):
            lane.shipments_per_month = float(m.group(1))
        if "liftgate" in turn_text.lower() and "LIFTGATE_DEL" not in lane.accessorials:
            lane.accessorials.append("LIFTGATE_DEL")
        if "hazmat" in turn_text.lower():
            lane.hazmat = True
        if "flatbed" in turn_text.lower() or "oversize" in turn_text.lower():
            lane.equipment_needed = "flatbed"

    return state


def rules_prose(proposal: Proposal) -> ProposalProse:
    """Template sentences, no model. Correct but plain — the LLM path reads
    better; this just has to never invent a number."""
    priced = [l for l in proposal.lanes if l.serviceable]
    summary = (
        f"{proposal.customer.company or 'This customer'} discussed "
        f"{len(proposal.lanes)} lane(s); {len(priced)} priced today from the rate card."
    )
    rationales = [
        LaneRationale(
            origin_metro=l.origin_metro, dest_metro=l.dest_metro,
            rationale=f"Priced as {l.mode_quoted or 'LTL'} at {l.service_level or 'STANDARD'} service.",
        )
        for l in priced
    ]
    return ProposalProse(deal_summary=summary, lane_rationales=rationales)
