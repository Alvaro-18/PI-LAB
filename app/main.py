"""API FastAPI. Uma única rota de chat; as mitigações são ligadas/desligadas pelo corpo da requisição."""
import json
from pathlib import Path

import httpx
from fastapi import FastAPI

from . import config
from .pipeline import run_chat
from .schemas import ChatRequest, ChatResponse

app = FastAPI(
    title="Prompt Injection Lab",
    description="Chatbot local (Ollama) para demonstrar prompt injection e camadas de mitigação.",
    version="0.1.0",
)

PAYLOADS_FILE = Path(__file__).resolve().parent.parent / "payloads" / "payloads.json"


@app.get("/health")
def health():
    """Verifica se o Ollama responde e lista os modelos baixados."""
    try:
        r = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        return {"ollama": "ok", "models": models, "default_model": config.DEFAULT_MODEL}
    except httpx.HTTPError as exc:
        return {"ollama": "indisponível", "detail": str(exc)}


@app.get("/payloads")
def payloads():
    """Banco de payloads de ataque (para copiar no Swagger)."""
    return json.loads(PAYLOADS_FILE.read_text(encoding="utf-8"))


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    """Rota única. Exemplo de mitigações: {"input_filter": true, "hardened_prompt": true}."""
    return run_chat(req)
