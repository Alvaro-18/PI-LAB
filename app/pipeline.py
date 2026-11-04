"""Pipeline único. A ordem das camadas é fixa; só o conjunto ativo varia por requisição:

    entrada -> [input_filter] -> [llm_guard] -> LLM (prompt vulnerável ou isolado) -> [output_filter] -> saída
"""
from typing import Callable, Optional

from . import mitigations as mit
from . import ollama_client, prompts
from .schemas import ChatRequest, ChatResponse


def run_chat(req: ChatRequest, chat_fn: Callable = ollama_client.chat) -> ChatResponse:
    m = req.mitigations
    trace: list[str] = []

    def result(response: str, *, blocked=False, blocked_by: Optional[str] = None,
               raw_leaked=False, error: Optional[str] = None) -> ChatResponse:
        kind = mit.leak_kind(response)
        return ChatResponse(
            response=response, model=req.model, mitigations=m, blocked=blocked,
            blocked_by=blocked_by, leaked=kind is not None, leak_kind=kind, raw_leaked=raw_leaked,
            refused=(not blocked and not error and kind is None and mit.is_refusal(response)),
            error=error, trace=trace,
        )

    # 1) filtro de entrada (regex/heurística)
    if m.input_filter:
        hit = mit.input_filter(req.message)
        trace.append(f"input_filter: {'BLOQUEOU' if hit else 'passou'}")
        if hit:
            return result(prompts.REFUSAL_MESSAGE, blocked=True, blocked_by="input_filter")

    # 2) LLM guard
    if m.llm_guard:
        try:
            is_attack, raw = mit.llm_guard(req.message, chat_fn, req.guard_model)
        except ollama_client.OllamaError as exc:
            trace.append("llm_guard: ERRO")
            return result("", error=f"llm_guard: {exc}")
        trace.append(f"llm_guard: {'BLOQUEOU' if is_attack else 'passou'} (saída={raw.strip()[:20]!r})")
        if is_attack:
            return result(prompts.REFUSAL_MESSAGE, blocked=True, blocked_by="llm_guard")

    # 3) chamada ao LLM-alvo (com ou sem isolamento de contexto)
    messages = mit.build_messages(req.message, hardened=m.hardened_prompt)
    trace.append(f"llm: prompt {'isolado' if m.hardened_prompt else 'vulnerável'}")
    try:
        raw_out = chat_fn(model=req.model, messages=messages,
                          temperature=req.temperature, seed=req.seed)
    except ollama_client.OllamaError as exc:
        trace.append("llm: ERRO")
        return result("", error=str(exc))

    raw_leaked = mit.detect_leak(raw_out)

    # 4) filtro de saída
    if m.output_filter:
        final, was_blocked = mit.output_filter(raw_out)
        trace.append(f"output_filter: {'BLOQUEOU' if was_blocked else 'passou'}")
        return result(final, blocked=was_blocked,
                      blocked_by="output_filter" if was_blocked else None, raw_leaked=raw_leaked)

    return result(raw_out, raw_leaked=raw_leaked)
