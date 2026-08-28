import os
from dotenv import load_dotenv

load_dotenv()

PROVIDER = os.environ.get("MODEL_PROVIDER", "groq").lower()

# base_url + default model per OpenAI-compatible provider. Groq, Gemini (OpenAI
# compat endpoint), GitHub Models and Ollama all speak this same wire format,
# so one client class covers all of them.
_PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "default_model": "groq/compound-mini",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
        "default_model": "gemini-2.0-flash",
    },
    "github": {
        "base_url": "https://models.inference.ai.azure.com",
        "api_key_env": "GITHUB_TOKEN",
        "default_model": "gpt-4o-mini",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "api_key_env": None,
        "default_model": "llama3.1",
    },
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key_env": "NVIDIA_API_KEY",
        "default_model": "openai/gpt-oss-120b",
    },
}


class LLMClient:
    """Thin wrapper: one method, complete(system, user) -> str."""

    def __init__(self, provider: str = PROVIDER):
        if provider not in _PROVIDERS:
            raise ValueError(
                f"Unknown MODEL_PROVIDER {provider!r}. "
                f"Choose one of {sorted(_PROVIDERS)}."
            )
        cfg = _PROVIDERS[provider]
        api_key = os.environ.get(cfg["api_key_env"]) if cfg["api_key_env"] else "ollama"
        if cfg["api_key_env"] and not api_key:
            raise RuntimeError(
                f"{cfg['api_key_env']} not set. Export it or add it to .env "
                f"(see .env.example), or run with --no-ai."
            )

        from openai import OpenAI

        self.model = os.environ.get("MODEL_NAME", cfg["default_model"])
        self._client = OpenAI(base_url=cfg["base_url"], api_key=api_key, timeout=30, max_retries=1)

    def complete(self, system: str, user: str, json_mode: bool = True) -> str:
        """Run one chat completion, return the raw text content."""
        kwargs = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            **kwargs,
        )
        return response.choices[0].message.content


def get_llm_client() -> LLMClient:
    return LLMClient("nvidia")
