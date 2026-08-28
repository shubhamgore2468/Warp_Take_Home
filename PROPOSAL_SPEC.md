# What a proposal is, and exactly how it is priced

This is the contract. `validate.py` enforces every rule on this page, so read it
before you write pricing code.

## The one rule that matters most

**The language model never does arithmetic.** Every dollar figure in the proposal
must be computed by your code from the CSVs in `data/`. A model may read the call
and decide *what* the customer needs. It must never decide *what it costs*.

If you let a model produce prices, the validator will catch it, because the numbers
will not reconcile against the rate card. This is not a trick. It is how you build
anything that quotes real money.

## The three parts of a proposal

1. **The deal facts.** What the customer told you, in structured form: lanes,
   volumes, weights, service levels, accessorials. Extracted from the call.
2. **The pricing.** Deterministic math over those facts, from the rate card.
3. **The honest edges.** What you could not price, what you assumed, and what you
   still need to ask. A proposal that quietly drops a lane is worse than one that
   names it as unpriced.

## Pricing algorithm

Work lane by lane. All rates come from `data/`.

### Step 0. Serviceability

A lane is **not** priceable if any of these hold. Do not invent a number for it.
Put it in `excluded` with a reason and carry on with the rest of the deal.

| Condition | Reason code |
|---|---|
| The origin/destination pair has no row in `rate_card.csv` | `lane_not_in_rate_card` |
| The freight needs flatbed, open-deck, or is oversize | `equipment_not_offered` |
| The freight is hazmat, any class or quantity | `commodity_not_accepted` |
| Pallets per shipment exceeds `ltl_max_pallets` and FTL is not viable | `exceeds_capacity` |

Warp operates dry van, reefer, box truck and cargo van. Warp does not run flatbed
or open-deck, does not take oversize freight, and does not accept hazmat.

### Step 1. Weight

```
weight_lb_per_shipment = pallets_per_shipment x weight_lb_per_pallet
```

If the customer speaks in kilograms, convert at **1 kg = 2.20462 lb** and round to
the nearest whole pound. Record that you converted.

### Step 2. Mode

Look up `ftl_pallet_threshold` and `ftl_weight_threshold_lb` in `pricing_config.csv`.

- If `pallets_per_shipment >= ftl_pallet_threshold` **or**
  `weight_lb_per_shipment >= ftl_weight_threshold_lb`, you must price **both** LTL
  and FTL and recommend the cheaper one.
- Otherwise price LTL only.
- If `pallets_per_shipment > ltl_max_pallets`, LTL is invalid; FTL only.

A customer saying "we've always bought this as LTL" does not override the
comparison. Show them both and let them choose.

### Defaults when the customer does not say

Customers rarely state everything. Where the call is silent, use these, and list
the ones you relied on in `assumptions`:

| Unstated | Default |
|---|---|
| Service level | `STANDARD`. Only use EXPEDITED or GUARANTEED when the customer asks for speed or a date guarantee. |
| Mode | Decide it with the Step 2 rule, not by assumption. |
| Accessorials | None. Add one only when the call gives you a reason, such as "there's no dock" implying a liftgate. |

Inferring a liftgate from "the building has no dock" is good listening. Inventing
an appointment fee nobody mentioned is padding the quote, and it is the kind of
thing a customer catches on the second call.

### Step 3. Linehaul

```
LTL: linehaul_base = max(ltl_min_charge, ltl_base + ltl_per_pallet x pallets_per_shipment)
FTL: linehaul_base = ftl_flat_rate

linehaul = round2(linehaul_base x linehaul_multiplier)
```

`linehaul_multiplier` comes from `service_levels.csv` for the chosen service level.

### Step 4. Fuel

```
fuel_surcharge = round2(linehaul x fuel_surcharge_pct / 100)
```

Fuel applies to the linehaul **after** the service multiplier, and never to
accessorials.

### Step 5. Accessorials

For each accessorial code on the lane, from `accessorials.csv`:

| unit | amount |
|---|---|
| `per_shipment` | `rate` |
| `per_pallet` | `round2(rate x pallets_per_shipment)` |
| `per_hour` | **not included in the monthly total** |

`per_hour` items (detention) are variable and unknowable at quote time. Show the
rate and the free-hours allowance in the proposal so the customer can model it, but
do not invent an hour count and do not add it to any total.

```
accessorials_total = round2(sum of included accessorial amounts)
```

### Step 6. Volume tier

The tier is set by **total monthly shipments across priceable lanes only**. Freight
you cannot serve does not earn a discount.

```
discount_pct = the tier in volume_tiers.csv whose range contains total_monthly_shipments
```

### Step 7. Totals

```
shipment_subtotal = round2(linehaul + fuel_surcharge + accessorials_total)
discount          = round2(shipment_subtotal x discount_pct / 100)
shipment_total    = round2(shipment_subtotal - discount)
lane_monthly_total= round2(shipment_total x shipments_per_month)

monthly_total = sum of lane_monthly_total over priceable lanes
annual_total  = round2(monthly_total x 12)
```

### Step 8. Transit

```
transit_days = max(1, transit_days_standard + transit_days_delta)
```

### Rounding

`round2` means round to two decimals, half away from zero. The validator allows a
tolerance of two cents or 0.01% of the value, whichever is larger, so ordinary
float arithmetic will pass. Do not try to match a specific floating point quirk.

## Worked example

Northwind Paper, Chicago IL to Atlanta GA (lane `L001`), 8 pallets at 600 lb,
40 shipments a month, standard service, liftgate at delivery.

```
weight_lb_per_shipment = 8 x 600                      = 4800
mode: 8 < 12 pallets and 4800 < 10000 lb              -> LTL only
linehaul_base = max(224.14, 305.69 + 73.00 x 8)       = 889.69
linehaul      = 889.69 x 1.00                         = 889.69
fuel          = 889.69 x 0.185                        =  164.59
accessorials  = LIFTGATE_DEL per_shipment             =   95.00
shipment_subtotal = 889.69 + 164.59 + 95.00           = 1149.28
tier: 40 shipments -> Growth                          = 3.00%
discount      = 1149.28 x 0.03                        =   34.48
shipment_total= 1149.28 - 34.48                       = 1114.80
monthly_total = 1114.80 x 40                          = 44592.00
annual_total  = 44592.00 x 12                         = 535104.00
transit_days  = 3 + 0                                 = 3
```

## What the written proposal must contain

Beyond the numbers, the proposal object carries the parts a rep actually reads
aloud on the call. These are prose fields, and they are where a language model
earns its place:

- `deal_summary` - two or three sentences a rep can say out loud describing the
  customer's freight in the customer's own terms.
- `rationale` per lane - one sentence on why this mode and service level.
- `assumptions` - anything you inferred rather than heard. If the customer never
  said whether the origin had a dock and you assumed one, say so.
- `open_questions` - what the rep should ask before this becomes a contract.
- `excluded` - every lane or requirement you could not price, with the reason in
  plain language the customer would accept.
- `comparable_account` - optionally, the closest match from `account_history.csv`
  and one sentence on why it is relevant.

A proposal that prices correctly but says nothing useful is half a proposal. A rep
cannot read a JSON blob to a customer.
