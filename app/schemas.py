from typing import Optional

from pydantic import BaseModel, Field

from . import config


class Mitigations(BaseModel):
    input_filter: bool = Field(False, description="Regex/heurística sobre a entrada")
    llm_guard: bool = Field(False, description="Classificador LLM sobre a entrada")
    hardened_prompt: bool = Field(False, description="System prompt estruturado + isolamento da entrada")
    output_filter: bool = Field(False, description="Bloqueia respostas que contenham o segredo ou trechos do prompt")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    model: str = config.DEFAULT_MODEL
    guard_model: str = config.GUARD_MODEL
    temperature: float = config.DEFAULT_TEMPERATURE
    seed: Optional[int] = None
    mitigations: Mitigations = Mitigations()


class ChatResponse(BaseModel):
    response: str
    model: str
    mitigations: Mitigations
    blocked: bool = Field(..., description="Algum filtro impediu a resposta do LLM ou a alterou")
    blocked_by: Optional[str] = Field(None, description="input_filter | llm_guard | output_filter")
    leaked: bool = Field(..., description="Segredo ou trecho do prompt na resposta FINAL entregue ao usuário")
    leak_kind: Optional[str] = Field(None, description="secret (código de desconto) | prompt (trecho do system prompt)")
    raw_leaked: bool = Field(..., description="Vazamento na saída bruta do LLM (antes do filtro de saída)")
    refused: bool = Field(False, description="O próprio LLM recusou o pedido (sem atuação de filtro)")
    error: Optional[str] = Field(None, description="Erro técnico (Ollama indisponível etc.), não é defesa")
    trace: list[str] = Field(default_factory=list, description="Passos executados, na ordem")
