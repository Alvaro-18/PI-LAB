"""API FastAPI. Uma única rota de chat; as mitigações são ligadas/desligadas pelo corpo da requisição.

Também serve o frontend (pasta front/) em /ui e o feed ao vivo dos experimentos em /events.
"""
import json
from pathlib import Path

import httpx
from fastapi import Body, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config, ollama_client
from .live import EventStore
from .pipeline import run_chat
from .schemas import ChatRequest, ChatResponse

app = FastAPI(
    title="Prompt Injection Lab",
    description="Chatbot local (Ollama) para demonstrar prompt injection e camadas de mitigação.",
    version="0.2.0",
)
# O frontend pode ser aberto direto do disco (file://) ou de outra porta; a API é só local.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ROOT = Path(__file__).resolve().parent.parent
PAYLOADS_FILE = ROOT / "payloads" / "payloads.json"
MODELS_FILE = ROOT / "models.json"
FRONT_DIR = ROOT / "front"

events = EventStore()


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


@app.get("/models")
def models():
    """Modelos baixados no Ollama + grupos de models.json (para o seletor do frontend)."""
    groups = json.loads(MODELS_FILE.read_text(encoding="utf-8"))
    try:
        installed, ollama = ollama_client.list_models(), "ok"
    except ollama_client.OllamaError:
        installed, ollama = [], "indisponível"
    return {
        "ollama": ollama,
        "installed": installed,
        "groups": {k: v for k, v in groups.items() if isinstance(v, list)},
        "default_model": config.DEFAULT_MODEL,
        "default_temperature": config.DEFAULT_TEMPERATURE,
    }


@app.get("/payloads")
def payloads():
    """Banco de payloads de ataque (para copiar no Swagger)."""
    return json.loads(PAYLOADS_FILE.read_text(encoding="utf-8"))


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    """Rota única. Exemplo de mitigações: {"input_filter": true, "hardened_prompt": true}."""
    return run_chat(req)


# ------------------------------------------------------------------ feed ao vivo
@app.get("/events/ping")
def events_ping():
    return {"ok": True, "session": events.session}


@app.get("/events")
def events_list(after: int = 0, limit: int = 500):
    """Eventos publicados pelo runner com id > after. Se `session` mudar, o feed foi reiniciado."""
    return {"session": events.session, "last_id": events.last_id, "events": events.since(after, limit)}


@app.post("/events")
def events_add(event: dict = Body(...)):
    """Usado por experiments/run_experiments.py para publicar cada execução."""
    return {"id": events.add(event)["id"]}


@app.delete("/events")
def events_clear():
    events.clear()
    return {"ok": True, "session": events.session}


# ------------------------------------------------------------------ frontend
if FRONT_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=FRONT_DIR, html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/ui/")
