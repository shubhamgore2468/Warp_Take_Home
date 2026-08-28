import argparse
import json
import os
from src.pricing import build_proposal
from src.state import ExtractionState, Customer

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("call_file", nargs="?", default="calls/call_01_northwind.txt")
    ap.add_argument("--no-ai", action="store_true", help="rules-based fallback, no LLM calls")
    args = ap.parse_args()

    if args.no_ai:
        from src.rules import rules_extract_turn as extract_fn, rules_prose
        prose_fn = lambda proposal, transcript: rules_prose(proposal)
    else:
        from src.extract import extract_turn as extract_fn
        from src.prose import generate_prose
        prose_fn = generate_prose
    from src.prose import apply_prose

    try:
        call_id = os.path.splitext(os.path.basename(args.call_file))[0]
        with open(args.call_file) as f:
            transcript = f.read()

        company = None
        for line in transcript.splitlines():
            line = line.strip()
            if line.startswith("CUSTOMER:") and not line.startswith("["):
                company = line.split(":", 1)[1].strip()
                break

        state = ExtractionState(call_id=call_id, customer=Customer(company=company))
        proposal = None
        pending_rep = []
        for line in transcript.splitlines():
            line = line.strip()
            if not line.startswith("["):  # skip header lines and blanks
                continue

            speaker = line.split("] ", 1)[-1].split(":")[0].strip()
            if speaker != "CUSTOMER":  # a REP line — hold it for context
                pending_rep.append(line)
                continue

            # one turn = the customer's line plus whatever REP said just
            # before it, so a bare "every time" still has its question
            turn = "\n".join(pending_rep + [line])
            pending_rep = []

            state = extract_fn(state, turn)
            print(f"\n--- after: {line[:60]} ---")
            if not state.lanes:
                print("(no lane identified yet)")
                continue
            proposal = build_proposal(state)   # pure, cheap — no LLM here
            print(json.dumps(proposal.model_dump(), indent=2))

        if proposal is None:
            print("No lane was ever identified in this call.")
            return

        apply_prose(proposal, prose_fn(proposal, transcript))  # once, at the end

        os.makedirs("out", exist_ok=True)
        out_path = f"out/{proposal.call_id}.proposal.json"
        with open(out_path, "w") as f:
            json.dump(proposal.model_dump(), f, indent=4)
        print(f"\nwrote {out_path}")
    except Exception as e:
        print("An error occurred : ", e)

if __name__ == "__main__":
    main()
