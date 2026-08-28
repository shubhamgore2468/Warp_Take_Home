#!/usr/bin/env python3
"""Validate a DealHub Lite proposal against the rate card and the spec.

    python validate.py out/call_01_northwind.proposal.json
    python validate.py out/                      # every proposal in a directory
    python validate.py out/ --check-facts        # also score extraction vs expected

Pure standard library. Nothing to install.

Two independent things are checked:
  1. Internal consistency. Given the deal facts YOUR proposal states, does YOUR
     pricing follow PROPOSAL_SPEC.md? This is the hard gate.
  2. Extraction (--check-facts, dev calls only). Do the facts you pulled out of
     the transcript match calls/expected/*.facts.json?
"""
import argparse, csv, json, os, sys
from decimal import Decimal, ROUND_HALF_UP

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CALLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls")


def round2(x):
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def close(a, b):
    """Two cents, or 0.01% of the larger value, whichever is more forgiving."""
    if a is None or b is None:
        return False
    tol = max(0.02, abs(b) * 0.0001)
    return abs(float(a) - float(b)) <= tol


def load_csv(name, key=None):
    with open(os.path.join(DATA, name), newline="") as f:
        rows = list(csv.DictReader(f))
    return {r[key]: r for r in rows} if key else rows


class Ref:
    def __init__(self):
        self.accessorials = load_csv("accessorials.csv", "code")
        self.service = load_csv("service_levels.csv", "code")
        self.tiers = load_csv("volume_tiers.csv")
        self.cfg = {r["key"]: float(r["value"]) for r in load_csv("pricing_config.csv")}
        self.lanes = {}
        for r in load_csv("rate_card.csv"):
            k = (r["origin_metro"].lower(), r["origin_state"].lower(),
                 r["dest_metro"].lower(), r["dest_state"].lower())
            self.lanes[k] = r

    def lane(self, o, os_, d, ds):
        if not all(isinstance(v, str) for v in (o, os_, d, ds)):
            return None
        return self.lanes.get((o.lower(), os_.lower(), d.lower(), ds.lower()))

    def tier_for(self, shipments):
        for t in self.tiers:
            lo = int(t["min_monthly_shipments"])
            hi = t["max_monthly_shipments"].strip()
            if shipments >= lo and (hi == "" or shipments <= int(hi)):
                return t["tier_name"], float(t["discount_pct"])
        return None, 0.0


REQUIRED_TOP = ["call_id", "customer", "deal_summary", "lanes", "volume_tier",
                "monthly_total", "annual_total", "excluded", "assumptions",
                "open_questions"]
PROSE = ["deal_summary"]
LIST_PROSE = ["assumptions", "open_questions"]


def price_lane(lane, ref, discount_pct, mode):
    """Recompute one lane per PROPOSAL_SPEC.md. Returns dict or an error string."""
    card = ref.lane(lane.get("origin_metro"), lane.get("origin_state"),
                    lane.get("dest_metro"), lane.get("dest_state"))
    if card is None:
        return "lane not in rate_card.csv"

    pallets = lane.get("pallets_per_shipment")
    wt_pallet = lane.get("weight_lb_per_pallet")
    if not isinstance(pallets, (int, float)) or pallets <= 0:
        return "pallets_per_shipment missing or not positive"

    svc_code = lane.get("service_level", "STANDARD")
    svc = ref.service.get(svc_code)
    if svc is None:
        return f"unknown service_level {svc_code!r}"

    if mode == "FTL":
        base = float(card["ftl_flat_rate"])
    else:
        base = max(float(card["ltl_min_charge"]),
                   float(card["ltl_base"]) + float(card["ltl_per_pallet"]) * pallets)

    linehaul = round2(base * float(svc["linehaul_multiplier"]))
    fuel = round2(linehaul * ref.cfg["fuel_surcharge_pct"] / 100.0)

    acc_total, acc_lines, hourly = 0.0, [], []
    for code in lane.get("accessorials", []) or []:
        a = ref.accessorials.get(code)
        if a is None:
            return f"unknown accessorial {code!r}"
        if a["unit"] == "per_shipment":
            amt = float(a["rate"])
        elif a["unit"] == "per_pallet":
            amt = round2(float(a["rate"]) * pallets)
        else:
            hourly.append(code)
            continue
        acc_lines.append((code, amt))
        acc_total += amt
    acc_total = round2(acc_total)

    subtotal = round2(linehaul + fuel + acc_total)
    discount = round2(subtotal * discount_pct / 100.0)
    ship_total = round2(subtotal - discount)
    spm = lane.get("shipments_per_month") or 0
    monthly = round2(ship_total * spm)
    transit = max(1, int(card["transit_days_standard"]) + int(svc["transit_days_delta"]))

    wt_shipment = None
    if isinstance(wt_pallet, (int, float)):
        wt_shipment = pallets * wt_pallet

    return {"linehaul": linehaul, "fuel_surcharge": fuel,
            "accessorials_total": acc_total, "accessorial_lines": acc_lines,
            "shipment_subtotal": subtotal, "discount": discount,
            "shipment_total": ship_total, "monthly_total": monthly,
            "transit_days": transit, "weight_lb_per_shipment": wt_shipment,
            "hourly_codes": hourly, "lane_id": card["lane_id"]}


def check_proposal(path, ref, expected=None):
    fails, warns = [], []
    try:
        with open(path) as f:
            p = json.load(f)
    except Exception as e:
        return [f"could not parse JSON: {e}"], [], 0

    for k in REQUIRED_TOP:
        if k not in p:
            fails.append(f"missing required top-level field {k!r}")
    if fails:
        return fails, warns, 0

    for k in PROSE:
        if not isinstance(p[k], str) or len(p[k].strip()) < 20:
            fails.append(f"{k} must be a real sentence, not a stub")
    for k in LIST_PROSE:
        if not isinstance(p[k], list):
            fails.append(f"{k} must be a list")

    lanes = p["lanes"]
    if not isinstance(lanes, list) or not lanes:
        return fails + ["lanes must be a non-empty list"], warns, 0

    priceable = [l for l in lanes if l.get("serviceable") is True]
    total_spm = sum(l.get("shipments_per_month") or 0 for l in priceable)

    stated_total = (p.get("volume_tier") or {}).get("total_monthly_shipments")
    if stated_total is not None and stated_total != total_spm:
        fails.append(f"volume_tier.total_monthly_shipments is {stated_total} but "
                     f"priceable lanes sum to {total_spm} (unserviceable freight "
                     f"must not count toward the tier)")

    tier_name, discount_pct = ref.tier_for(total_spm)
    stated_pct = (p.get("volume_tier") or {}).get("discount_pct")
    if stated_pct is not None and not close(stated_pct, discount_pct):
        fails.append(f"volume_tier.discount_pct is {stated_pct} but {total_spm} "
                     f"monthly shipments falls in {tier_name} at {discount_pct}%")

    monthly_sum, checked = 0.0, 0
    for i, lane in enumerate(lanes):
        tag = (f"lane[{i}] {lane.get('origin_metro')} -> {lane.get('dest_metro')}")

        if lane.get("serviceable") is False:
            pr = lane.get("pricing")
            if pr and any(isinstance(v, (int, float)) and v > 0 for v in pr.values()
                          if not isinstance(v, (list, dict))):
                fails.append(f"{tag}: marked unserviceable but carries a nonzero "
                             f"price. Unpriceable freight must never be quoted.")
            if not lane.get("unserviceable_reason"):
                fails.append(f"{tag}: unserviceable with no reason given")
            continue

        card = ref.lane(lane.get("origin_metro"), lane.get("origin_state"),
                        lane.get("dest_metro"), lane.get("dest_state"))
        if card is None:
            fails.append(f"{tag}: priced as serviceable but the lane is not in "
                         f"rate_card.csv. It belongs in excluded.")
            continue

        mode = (lane.get("mode_quoted") or "LTL").upper()
        exp = price_lane(lane, ref, discount_pct, mode)
        if isinstance(exp, str):
            fails.append(f"{tag}: {exp}")
            continue
        checked += 1

        got = lane.get("pricing") or {}
        for field in ["linehaul", "fuel_surcharge", "accessorials_total",
                      "shipment_subtotal", "discount", "shipment_total",
                      "monthly_total"]:
            if field not in got:
                fails.append(f"{tag}: pricing.{field} missing")
            elif not close(got[field], exp[field]):
                fails.append(f"{tag}: pricing.{field} is {got[field]}, "
                             f"rate card gives {exp[field]}")

        if "transit_days" in lane and lane["transit_days"] != exp["transit_days"]:
            fails.append(f"{tag}: transit_days is {lane['transit_days']}, "
                         f"expected {exp['transit_days']}")

        if exp["weight_lb_per_shipment"] is not None:
            stated_w = lane.get("weight_lb_per_shipment")
            if stated_w is not None and not close(stated_w, exp["weight_lb_per_shipment"]):
                fails.append(f"{tag}: weight_lb_per_shipment is {stated_w}, "
                             f"pallets x per-pallet weight gives "
                             f"{exp['weight_lb_per_shipment']}")

        for code in exp["hourly_codes"]:
            listed = [a for a in (got.get("accessorials") or [])
                      if a.get("code") == code and a.get("amount")]
            if listed:
                fails.append(f"{tag}: {code} is a per-hour charge and must not be "
                             f"added to any total; show the rate instead")

        pallets = lane.get("pallets_per_shipment") or 0
        wt = exp["weight_lb_per_shipment"] or 0
        crosses = (pallets >= ref.cfg["ftl_pallet_threshold"]
                   or wt >= ref.cfg["ftl_weight_threshold_lb"])
        if crosses:
            alts = lane.get("mode_alternatives") or []
            modes = {mode} | {(a.get("mode") or "").upper() for a in alts}
            if not {"LTL", "FTL"} <= modes:
                fails.append(f"{tag}: {pallets} pallets / {wt:.0f} lb crosses the "
                             f"FTL threshold, so LTL and FTL must both be priced "
                             f"and compared (mode_alternatives)")
            else:
                other = "FTL" if mode == "LTL" else "LTL"
                alt_exp = price_lane(lane, ref, discount_pct, other)
                if not isinstance(alt_exp, str):
                    alt_got = next((a for a in alts
                                    if (a.get("mode") or "").upper() == other), None)
                    if alt_got and "shipment_total" in alt_got and \
                            not close(alt_got["shipment_total"], alt_exp["shipment_total"]):
                        fails.append(f"{tag}: {other} alternative shipment_total is "
                                     f"{alt_got['shipment_total']}, rate card gives "
                                     f"{alt_exp['shipment_total']}")
                    if alt_exp["shipment_total"] < exp["shipment_total"] - 0.02:
                        warns.append(f"{tag}: {other} is cheaper per shipment "
                                     f"({alt_exp['shipment_total']} vs "
                                     f"{exp['shipment_total']}) but {mode} was quoted. "
                                     f"Defensible only if the proposal says why.")

        if not (lane.get("rationale") or "").strip():
            warns.append(f"{tag}: no rationale given for mode and service level")

        monthly_sum += exp["monthly_total"]

    monthly_sum = round2(monthly_sum)
    if not close(p.get("monthly_total"), monthly_sum):
        fails.append(f"monthly_total is {p.get('monthly_total')}, priceable lanes "
                     f"sum to {monthly_sum}")
    if not close(p.get("annual_total"), round2(monthly_sum * 12)):
        fails.append(f"annual_total is {p.get('annual_total')}, expected "
                     f"{round2(monthly_sum * 12)}")

    unserv = [l for l in lanes if l.get("serviceable") is False]
    if unserv and not p.get("excluded"):
        fails.append("lanes are marked unserviceable but `excluded` is empty; the "
                     "customer must be told in plain language what is not covered")

    if expected:
        fails_f, warns_f = compare_facts(p, expected)
        fails.extend(fails_f); warns.extend(warns_f)

    return fails, warns, checked


def compare_facts(p, exp):
    """Extraction check against calls/expected/*.facts.json (dev calls only)."""
    fails, warns = [], []
    got_lanes = {(str(l.get("origin_metro")).lower(), str(l.get("dest_metro")).lower()): l
                 for l in p["lanes"]}
    for el in exp["lanes"]:
        k = (el["origin_metro"].lower(), el["dest_metro"].lower())
        gl = got_lanes.get(k)
        if gl is None:
            fails.append(f"extraction: lane {el['origin_metro']} -> "
                         f"{el['dest_metro']} was in the call but is missing")
            continue
        if bool(gl.get("serviceable")) != bool(el["serviceable"]):
            fails.append(f"extraction: {el['origin_metro']} -> {el['dest_metro']} "
                         f"serviceable should be {el['serviceable']}")
        if not el["serviceable"]:
            continue
        for field in ["pallets_per_shipment", "shipments_per_month",
                      "weight_lb_per_pallet", "service_level"]:
            want = el.get(field)
            if want is None:
                continue
            have = gl.get(field)
            ok = close(have, want) if isinstance(want, (int, float)) else have == want
            if not ok:
                fails.append(f"extraction: {el['origin_metro']} -> "
                             f"{el['dest_metro']} {field} is {have!r}, "
                             f"the call says {want!r}")
        want_acc = set(el.get("accessorials") or [])
        have_acc = set(gl.get("accessorials") or [])
        if want_acc - have_acc:
            fails.append(f"extraction: {el['origin_metro']} -> {el['dest_metro']} "
                         f"missing accessorials {sorted(want_acc - have_acc)}")
        if have_acc - want_acc:
            warns.append(f"extraction: {el['origin_metro']} -> {el['dest_metro']} "
                         f"has extra accessorials {sorted(have_acc - want_acc)}")
    extra = set(got_lanes) - {(l["origin_metro"].lower(), l["dest_metro"].lower())
                              for l in exp["lanes"]}
    for k in sorted(extra):
        warns.append(f"extraction: lane {k[0]} -> {k[1]} is not in the call")
    return fails, warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="a proposal .json file, or a directory of them")
    ap.add_argument("--check-facts", action="store_true",
                    help="also score extraction against calls/expected/")
    args = ap.parse_args()

    if not os.path.exists(args.target):
        print(f"Nothing at {args.target!r} yet.\n"
              f"Your pipeline should write one proposal JSON per call, then run:\n"
              f"    python validate.py {args.target}\n"
              f"To see a passing run before you write anything, try:\n"
              f"    python validate.py example/proposal.example.json")
        sys.exit(1)
    if os.path.isdir(args.target):
        paths = sorted(os.path.join(args.target, f) for f in os.listdir(args.target)
                       if f.endswith(".json"))
    else:
        paths = [args.target]
    if not paths:
        print(f"no .json proposals found in {args.target}")
        sys.exit(1)

    ref = Ref()
    total_fail = total_warn = total_lanes = 0
    clean = 0

    for path in paths:
        expected = None
        if args.check_facts:
            try:
                cid = json.load(open(path)).get("call_id", "")
            except Exception:
                cid = ""
            ep = os.path.join(CALLS, "expected", f"{cid}.facts.json")
            if os.path.exists(ep):
                expected = json.load(open(ep))

        fails, warns, checked = check_proposal(path, ref, expected)
        total_fail += len(fails); total_warn += len(warns); total_lanes += checked
        name = os.path.basename(path)
        if fails:
            print(f"\nFAIL  {name}")
            for m in fails:
                print(f"  x {m}")
        else:
            clean += 1
            print(f"\nPASS  {name}  ({checked} lane(s) reconciled)")
        for m in warns:
            print(f"  ! {m}")

    print("\n" + "-" * 62)
    print(f"proposals      {clean}/{len(paths)} clean")
    print(f"lanes priced   {total_lanes} reconciled against the rate card")
    print(f"errors         {total_fail}")
    print(f"warnings       {total_warn}   (judgment calls, not automatic failures)")
    sys.exit(1 if total_fail else 0)


if __name__ == "__main__":
    main()
