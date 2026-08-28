# Warp proposal builder

Feeds a call transcript turn by turn, extracts deal facts, prices them from
`data/*.csv`, writes one proposal JSON per call.

## Run it

```bash
uv sync
cp .env.example .env   # fill in GROQ_API_KEY

# one call
uv run python main.py calls/call_01_northwind.txt --provider nvidia

# all six
for f in calls/call_*.txt; do uv run python main.py "$f"; --provider nvidia; done done

# no model at all
uv run python main.py calls/call_01_northwind.txt --no-ai

# check output
python3 validate.py out/ --check-facts
```

## Model Used
Nvidia nim model - softer rate limits and req/per - able to process all turn in 6 conversations

## CSV vs mock server

Read CSVs directly, in-process. Numbers are identical either way.

Tradeoff: call for rate engine that's a network call, can slow or
go down mid-conversation. Reading CSVs in-process skips that entirely.

## Validator output

```
python3 validate.py out/ --check-facts

PASS  call_01_northwind.proposal.json  (1 lane(s) reconciled)
PASS  call_02_brightline.proposal.json  (2 lane(s) reconciled)
PASS  call_03_summit.proposal.json  (1 lane(s) reconciled)
PASS  call_04_ironclad.proposal.json  (1 lane(s) reconciled)
PASS  call_05_cascade.proposal.json  (1 lane(s) reconciled)
  ! extraction: Seattle -> Portland has extra accessorials ['DETENTION']
PASS  call_06_meridian.proposal.json  (2 lane(s) reconciled)

proposals      6/6 clean
lanes priced   8 reconciled against the rate card
errors         0
warnings       1
```

## Decisions and tradeoffs

- **Two-pass pricing, not one.** 
  - Volume tier discount needs the sum of
  shipments across all lanes, which needs serviceability checked first. So:
  serviceability → tier → per-lane pricing. 
- **Extraction and pricing never share a call.** `pricing.py` has zero LLM calls

- **Prose runs once, after pricing, not per turn.** 
  - It reads already-computed
  numbers read-only and writes sentences around them. Running it every turn
  would burn the rate limit"
  - On;y ExtractionState is computed after every turn
- **Turn = one customer line + the REP line before it**
  - Improved LLM calls ratetimiting, making every call faster
  - A CUSTOMER needs the REP's question right before it to mean
  anything; pairing halves LLM calls over sending every raw line separately.
- **equipment_needed only disqualifies the disallowed types** (flatbed,
  open-deck, oversize) — not any truthy value. Early version treated
  "dry van" (which Warp offers) as unserviceable. Fixed to check against the
  actual disallowed set.
