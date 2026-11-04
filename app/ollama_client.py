"""Cliente mínimo para a API local do Ollama."""
from typing import Optional

import httpx

from . import config


class OllamaError(RuntimeError):
    """Falha de comunicação ou de resposta do Ollama (conta como erro técnico, não como defesa)."""


def chat(
    model: str,
    messages: list[dict],
    temperature: float = config.DEFAULT_TEMPERATURE,
    seed: Optional[int] = None,
    num_predict: Optional[int] = 400,
) -> str:
    options: dict = {"temperature": temperature}
    if seed is not None:
        options["seed"] = seed
    if num_predict is not None:
        options["num_predict"] = num_predict
    payload = {"model": model, "messages": messages, "stream": False, "options": options}
    try:
        r = httpx.post(
            f"{config.OLLAMA_URL}/api/chat", json=payload, timeout=config.REQUEST_TIMEOUT
        )
        r.raise_for_status()
        return r.json()["message"]["content"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise OllamaError(f"{type(exc).__name__}: {exc}") from exc


def _with_latest(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


def list_models() -> list[str]:
    """Tags dos modelos já baixados (ex.: ['llama3.2:3b', 'phi3:mini'])."""
    try:
        r = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=10)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise OllamaError(f"{type(exc).__name__}: {exc}") from exc


def is_available(model: str, installed: list[str]) -> bool:
    return _with_latest(model) in {_with_latest(m) for m in installed}


def unload(model: str) -> None:
    """Libera o modelo da memória (importante em máquinas de 8 GB). Falhas são ignoradas."""
    try:
        httpx.post(f"{config.OLLAMA_URL}/api/generate",
                   json={"model": model, "keep_alive": 0}, timeout=30)
    except httpx.HTTPError:
        pass
