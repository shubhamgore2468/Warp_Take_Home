import csv
import json
import os
from src.llm import get_llm_client
from src.state import ExtractionState

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def _accessorial_codes() -> str:
    with open(os.path.join(DATA, "accessorials.csv"), newline="") as f:
        rows = list(csv.DictReader(f))
    return "\n".join(f"- {r['code']}: {r['name']} ({r['notes']})" for r in rows)


SYSTEM_PROMPT = f"""
You are a precise data extraction assistant for freight sales calls. Respond
ONLY with a valid JSON object matching the schema.

For `accessorials`, use ONLY these exact codes, never invent your own or
paraphrase them:
{_accessorial_codes()}
"""


def extract_structured_data(file_path: str):
    try:
        # 1. Read file content safely
        with open(file_path, "r", encoding="utf-8") as f:
            file_content = f.read()

        # 2. Instantiate the client
        client = get_llm_client()

        # 3. Call the completion API
        system = f"{SYSTEM_PROMPT} {json.dumps(ExtractionState.model_json_schema(), indent=2)}"
        user = f"Extract information from the following text: {file_content}"
        raw_json = client.complete(system, user)

        # 4. Parse/validate with Pydantic
        return ExtractionState.model_validate_json(raw_json)

    except FileNotFoundError as e:
        print(f"Error: The file target '{file_path}' was not found.")
        raise e 

    except Exception as e:
        print(f"Unexpected Error: {e}")
        raise e


TURN_SYSTEM_PROMPT = f"""
You are a precise data extraction assistant for freight sales calls, run
incrementally: you see one new turn at a time plus the state extracted so
far, and you return the FULL UPDATED state.

Rules:
- Keep every fact from the current state that this turn does not contradict.
- If this turn corrects a fact ("actually seven, not six"), overwrite it on
  the SAME lane. Never add a duplicate lane for a correction.
- Settle ranges ("six to eight pallets") to a single number once the
  customer settles on one; keep the current value if still a range.
- Convert kilograms to pounds: 1 kg = 2.20462 lb, round to the nearest whole
  pound. Note the conversion in `notes`.
- Understand freight slang: "skid" = pallet, "kilo" = kilogram, etc.
- A lane needs at least origin_metro, origin_state, dest_metro, dest_state to
  exist. Don't invent lanes from vague mentions.

For `accessorials`, use ONLY these exact codes, never invent your own or
paraphrase them:
{_accessorial_codes()}

Respond ONLY with a valid JSON object matching the schema.
"""


def extract_turn(state: ExtractionState, turn_text: str) -> ExtractionState:
    """One turn in, updated full ExtractionState out. Pure LLM call — no
    pricing, no dollars; see src/pricing.py for that."""
    client = get_llm_client()
    system = f"{TURN_SYSTEM_PROMPT} {json.dumps(ExtractionState.model_json_schema(), indent=2)}"
    user = (f"Current state:\n{state.model_dump_json(indent=2)}\n\n"
            f"New turn:\n{turn_text}\n\n"
            f"Return the full updated state.")
    raw_json = client.complete(system, user)
    return ExtractionState.model_validate_json(raw_json)

