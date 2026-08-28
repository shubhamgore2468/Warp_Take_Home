import csv
import os
from decimal import Decimal, ROUND_HALF_UP

from src.state import ExtractionState, Proposal, ProposalLane, VolumeTier, ExcludedItem


def round2(x):
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def _load_csv(name, key=None):
    with open(os.path.join(DATA, name), newline="") as f:
        rows = list(csv.DictReader(f))
    return {r[key]: r for r in rows} if key else rows


class RateData:
    def __init__(self):
        self.accessorials = _load_csv("accessorials.csv", "code")
        self.service_levels = _load_csv("service_levels.csv", "code")
        self.volume_tiers = _load_csv("volume_tiers.csv")
        self.account_history = _load_csv("account_history.csv")
        self.config = {r["key"]: float(r["value"]) for r in _load_csv("pricing_config.csv")}
        self.rate_card = {}
        for r in _load_csv("rate_card.csv"):
            k = (r["origin_metro"].lower(), r["origin_state"].lower(),
                 r["dest_metro"].lower(), r["dest_state"].lower())
            self.rate_card[k] = r

    def lane_rates(self, origin_metro, origin_state, dest_metro, dest_state):
        key = (origin_metro.lower(), origin_state.lower(),
               dest_metro.lower(), dest_state.lower())
        return self.rate_card.get(key)

    def tier_for(self, total_monthly_shipments):
        for t in self.volume_tiers:
            lo = int(t["min_monthly_shipments"])
            hi = t["max_monthly_shipments"].strip()
            if total_monthly_shipments >= lo and (hi == "" or total_monthly_shipments <= int(hi)):
                return t["tier_name"], float(t["discount_pct"])
        return "Starter", 0.0


def check_serviceability(lane, rates: RateData):
    """Step 0. Returns (serviceable: bool, reason: str|None). """
    if lane.hazmat:
        return False, "commodity_not_accepted"

    disallowed = {"flatbed", "open-deck", "open deck", "oversize"}
    if lane.equipment_needed and lane.equipment_needed.lower() in disallowed:
        return False, "equipment_not_offered"

    card = rates.lane_rates(lane.origin_metro, lane.origin_state,
                             lane.dest_metro, lane.dest_state)
    if card is None:
        return False, "lane_not_in_rate_card"

    pallets = lane.pallets_per_shipment or 0
    ftl_viable = bool(card.get("ftl_flat_rate"))
    if pallets > rates.config["ltl_max_pallets"] and not ftl_viable:
        return False, "exceeds_capacity"

    return True, None


def price_lane(lane, card, rates: RateData, discount_pct: float) -> dict:
    """Calc rate"""
    pallets = lane.pallets_per_shipment or 0
    weight_lb = pallets * (lane.weight_lb_per_pallet or 0)          # step 1
    svc = rates.service_levels[lane.service_level]
    cfg = rates.config

    def price_as(mode):                                             # steps 3-5, 7-8, 12
        if mode == "FTL":
            base = float(card["ftl_flat_rate"])
        else:
            base = max(float(card["ltl_min_charge"]),
                       float(card["ltl_base"]) + float(card["ltl_per_pallet"]) * pallets)
        linehaul = round2(base * float(svc["linehaul_multiplier"]))
        fuel = round2(linehaul * cfg["fuel_surcharge_pct"] / 100.0)

        acc_lines, acc_total = [], 0.0
        for code in lane.accessorials:
            a = rates.accessorials.get(code)
            if a is None:  # extraction returned a code not in accessorials.csv
                continue
            if a["unit"] == "per_shipment":
                amt = float(a["rate"])
            elif a["unit"] == "per_pallet":
                amt = round2(float(a["rate"]) * pallets)
            else:  # per_hour (e.g. detention) — shown separately, never totaled
                continue
            acc_lines.append({"code": code, "amount": amt})
            acc_total += amt
        acc_total = round2(acc_total)

        subtotal = round2(linehaul + fuel + acc_total)
        discount = round2(subtotal * discount_pct / 100.0)
        shipment_total = round2(subtotal - discount)
        monthly_total = round2(shipment_total * (lane.shipments_per_month or 0))
        transit_days = max(1, int(card["transit_days_standard"]) + int(svc["transit_days_delta"]))

        return {
            "mode": mode, "linehaul": linehaul, "fuel_surcharge": fuel,
            "accessorials": acc_lines, "accessorials_total": acc_total,
            "shipment_subtotal": subtotal, "discount_pct": discount_pct,
            "discount": discount, "shipment_total": shipment_total,
            "monthly_total": monthly_total, "transit_days": transit_days,
        }

    if pallets > cfg["ltl_max_pallets"]:                             # step 2
        crosses_threshold, forced_mode = False, "FTL"
    else:
        crosses_threshold = (pallets >= cfg["ftl_pallet_threshold"]
                              or weight_lb >= cfg["ftl_weight_threshold_lb"])
        forced_mode = None

    if forced_mode:
        chosen, alternative = price_as(forced_mode), None
    elif crosses_threshold:
        ltl, ftl = price_as("LTL"), price_as("FTL")
        chosen, alternative = (ltl, ftl) if ltl["shipment_total"] <= ftl["shipment_total"] else (ftl, ltl)
    else:
        chosen, alternative = price_as("LTL"), None

    return {"weight_lb_per_shipment": weight_lb, "pricing": chosen, "alternative": alternative}


def volume_tier(priceable_lanes, rates: RateData) -> dict:
    """ """
    total_shipments = sum(l.shipments_per_month or 0 for l in priceable_lanes)
    tier_name, discount_pct = rates.tier_for(total_shipments)
    return {
        "tier_name": tier_name,
        "total_monthly_shipments": total_shipments,
        "discount_pct": discount_pct,
    }


def sum_totals(lane_pricings: list[dict]) -> dict:
    """ """
    monthly_total = round2(sum(p["monthly_total"] for p in lane_pricings))
    annual_total = round2(monthly_total * 12)
    return {"monthly_total": monthly_total, "annual_total": annual_total}


def build_proposal(extraction: ExtractionState) -> Proposal:
    """Everything after extraction: run pricing, assemble the schema shape.
    Prose fields are placeholders here — a model fills them in separately;
    this function's job is only the numbers, per PROPOSAL_SPEC.md."""
    rates = RateData()

    priceable, excluded_lanes = [], []
    for lane in extraction.lanes:
        serviceable, reason = check_serviceability(lane, rates)
        if serviceable:
            priceable.append(lane)
        else:
            lane.serviceable, lane.unserviceable_reason = False, reason
            excluded_lanes.append(lane)

    vt = volume_tier(priceable, rates)

    proposal_lanes, lane_pricings = [], []
    for lane in priceable:
        card = rates.lane_rates(lane.origin_metro, lane.origin_state,
                                 lane.dest_metro, lane.dest_state)
        result = price_lane(lane, card, rates, vt["discount_pct"])
        pricing, alt = result["pricing"], result["alternative"]
        lane_pricings.append(pricing)

        proposal_lanes.append(ProposalLane(
            origin_metro=lane.origin_metro, origin_state=lane.origin_state,
            dest_metro=lane.dest_metro, dest_state=lane.dest_state,
            lane_id=card["lane_id"], serviceable=True,
            pallets_per_shipment=lane.pallets_per_shipment,
            weight_lb_per_pallet=lane.weight_lb_per_pallet,
            weight_lb_per_shipment=result["weight_lb_per_shipment"],
            shipments_per_month=lane.shipments_per_month,
            service_level=lane.service_level, accessorials=lane.accessorials,
            mode_quoted=pricing["mode"], transit_days=pricing["transit_days"],
            mode_alternatives=[alt] if alt else [],
            pricing=pricing,
        ))

    for lane in excluded_lanes:
        proposal_lanes.append(ProposalLane(
            origin_metro=lane.origin_metro, origin_state=lane.origin_state,
            dest_metro=lane.dest_metro, dest_state=lane.dest_state,
            serviceable=False, unserviceable_reason=lane.unserviceable_reason,
            pallets_per_shipment=lane.pallets_per_shipment,
            shipments_per_month=lane.shipments_per_month,
        ))

    totals = sum_totals(lane_pricings)

    excluded = [ExcludedItem(
        description=f"{l.origin_metro}, {l.origin_state} -> {l.dest_metro}, {l.dest_state}",
        reason=l.unserviceable_reason,
    ) for l in excluded_lanes]

    return Proposal(
        call_id=extraction.call_id,
        customer=extraction.customer,
        deal_summary=(
            f"{extraction.customer.company or 'This customer'} ships across "
            f"{len(proposal_lanes)} lane(s), {len(priceable)} of which we can "
            f"price today."
        ),
        lanes=proposal_lanes,
        volume_tier=VolumeTier(**vt),
        monthly_total=totals["monthly_total"],
        annual_total=totals["annual_total"],
        excluded=excluded,
        assumptions=[],
        open_questions=[],
    )
