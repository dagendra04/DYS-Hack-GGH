class ProviderError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


_ENV = {"OpenAI": "OPENAI_API_KEY", "Mistral": "MISTRAL_API_KEY", "Gemini": "GEMINI_API_KEY"}


def from_provider(name: str, status: int) -> ProviderError:
    if status in (401, 403):
        return ProviderError(502, "bad_api_key", f"{name} rejected the API key. Check {_ENV.get(name, 'the API key')} in .env.")
    if status == 429:
        return ProviderError(502, "quota", f"{name} rate limit or quota reached. Wait a minute, check usage/billing, or switch provider in .env.")
    return ProviderError(502, "provider_failed", f"{name} request failed (HTTP {status}).")


def from_openai(status: int) -> ProviderError:
    return from_provider("OpenAI", status)


def network() -> ProviderError:
    return ProviderError(502, "network", "Could not reach the AI provider (network error or timeout).")
