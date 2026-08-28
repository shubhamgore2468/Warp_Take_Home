import json
from src.llm import get_llm_client
from src.state import ExtractionState 

# Define or import your system prompt here
SYSTEM_PROMPT="""
You are a precise data extraction assistant. Respond ONLY with a valid JSON object matching the schema.
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

