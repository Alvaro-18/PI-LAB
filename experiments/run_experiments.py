"""Roda os payloads sob várias configurações de mitigação, para um ou mais modelos, e salva CSVs.

Exemplos (da raiz do projeto):
    python -m experiments.run_experiments --models llama3.2:3b --reps 3
    python -m experiments.run_experiments --models core --reps 3                    # grupo de models.json
    python -m experiments.run_experiments --models phi3:mini,gemma3:4b --split test --reps 5
    python -m experiments.run_experiments --models core --run-id noite1 --resume    # retoma após queda

Desenho do experimento
  * temperature 0,7 e uma seed diferente por repetição (seed_base + rep). A mesma seed é usada em TODAS as configurações,
    então cada defesa é avaliada sobre a mesma amostra do modelo (desenho pareado).
  * As chamadas ao Ollama são memorizadas (modelo, mensagens, temperatura, seed): se duas configurações enviam exatamente
    o mesmo prompt com a mesma seed, a resposta é reaproveitada. Isso reduz o tempo de coleta em cerca de 3x sem alterar
    o resultado (a saída do modelo é a mesma que seria gerada de novo).
  * Cada linha é gravada assim que fica pronta; com --resume uma coleta interrompida continua de onde parou.
"""
import argparse
import csv
import json
import time
from pathlib import Path
from typing import Callable, Optional

from app import config, ollama_client
from app.pipeline import run_chat
from app.schemas import ChatRequest, Mitigations
from experiments import analyze_results

ROOT = Path(__file__).resolve().parent.parent

CONFIGS: dict[str, Mitigations] = {
    "baseline": Mitigations(),
    "input_filter": Mitigations(input_filter=True),
    "llm_guard": Mitigations(llm_guard=True),
    "hardened_prompt": Mitigations(hardened_prompt=True),
    "output_filter": Mitigations(output_filter=True),
    "input_guard": Mitigations(input_filter=True, llm_guard=True),
    "guard_hardened": Mitigations(llm_guard=True, hardened_prompt=True),
    "all": Mitigations(input_filter=True, llm_guard=True, hardened_prompt=True, output_filter=True),
}

FIELDS = ["model", "run_id", "split", "config", "kind", "payload_id", "category", "rep", "seed",
          "outcome", "blocked_by", "leak_kind", "raw_leaked", "error", "response"]


class CachedChat:
    """Memoriza respostas do LLM por (modelo, mensagens, temperatura, seed, num_predict). Erros não são memorizados."""

    def __init__(self, impl: Callable):
        self.impl, self.cache, self.calls, self.hits = impl, {}, 0, 0

    def __call__(self, model, messages, temperature=0, seed=None, num_predict=400):
        key = (model, json.dumps(messages, ensure_ascii=False), temperature, seed, num_predict)
        if key in self.cache:
            self.hits += 1
            return self.cache[key]
        out = self.impl(model=model, messages=messages, temperature=temperature, seed=seed, num_predict=num_predict)
        self.calls += 1
        self.cache[key] = out
        return out


def outcome(res) -> str:
    if res.error:
        return "error"
    if res.leaked:
        return "leaked"
    if res.blocked:
        return "blocked"
    if res.refused:
        return "refused"
    return "answered"


def resolve_models(arg: str) -> list[str]:
    groups = json.loads((ROOT / "models.json").read_text(encoding="utf-8"))
    if arg == "all":
        return [m for k in ("core", "extra") for m in groups[k]]
    if arg in groups and isinstance(groups[arg], list):
        return groups[arg]
    return [m.strip() for m in arg.split(",") if m.strip()]


def fmt_eta(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    return f"{h}h{rem // 60:02d}min"


def main(argv=None, chat_impl: Optional[Callable] = None) -> list[Path]:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="core", help="grupo de models.json (core|extra|all|tight) ou lista separada por vírgula")
    ap.add_argument("--guard-model", default="same", help="'same' = o próprio modelo-alvo (padrão) ou o nome de um modelo fixo")
    ap.add_argument("--reps", type=int, default=3, help="repetições por payload e configuração")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--seed-base", type=int, default=1000)
    ap.add_argument("--split", choices=["all", "dev", "test"], default="all")
    ap.add_argument("--configs", default=",".join(CONFIGS), help="lista separada por vírgula")
    ap.add_argument("--no-benign", action="store_true", help="não medir perda de utilidade")
    ap.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--resume", action="store_true", help="continua uma coleta com o mesmo --run-id")
    ap.add_argument("--out", default=str(ROOT / "results"))
    args = ap.parse_args(argv)

    names = [c.strip() for c in args.configs.split(",") if c.strip()]
    unknown = [c for c in names if c not in CONFIGS]
    if unknown:
        raise SystemExit(f"Configuração desconhecida: {unknown}. Opções: {list(CONFIGS)}")
    models = resolve_models(args.models)

    bank = json.loads((ROOT / "payloads" / "payloads.json").read_text(encoding="utf-8"))
    keep = (lambda x: True) if args.split == "all" else (lambda x: x.get("split") == args.split)
    attacks = [a for a in bank["attacks"] if keep(a)]
    benign = [] if args.no_benign else [b for b in bank["benign"] if keep(b)]
    items = [("attack", a) for a in attacks] + [("benign", b) for b in benign]

    real = chat_impl is None
    impl = chat_impl or ollama_client.chat
    try:
        installed = ollama_client.list_models() if real else []
    except ollama_client.OllamaError as exc:
        raise SystemExit(f"Não consegui falar com o Ollama em {config.OLLAMA_URL} ({exc}).\n"
                         "Inicie com `ollama serve` (ou abra o app do Ollama) e tente de novo.")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    per_model_total = len(names) * len(items) * args.reps
    print(f"{len(models)} modelo(s) | {len(attacks)} ataques + {len(benign)} benignos | {len(names)} configs | "
          f"{args.reps} reps | {per_model_total} execuções por modelo | run-id={args.run_id}")

    for model in models:
        if real and not ollama_client.is_available(model, installed):
            print(f"!! {model} não está baixado. Rode: ollama pull {model}  (pulando)")
            continue
        guard = model if args.guard_model == "same" else args.guard_model
        safe = model.replace(":", "_").replace("/", "_")
        path = out_dir / f"runs_{safe}_{args.run_id}.csv"

        done: set[tuple] = set()
        if args.resume and path.exists():
            with path.open(encoding="utf-8") as f:
                done = {(r["config"], r["kind"], r["payload_id"], int(r["rep"])) for r in csv.DictReader(f)}
        new_file = not path.exists() or not args.resume
        cached = CachedChat(impl)
        t0, n_done = time.time(), 0
        todo = per_model_total - len(done)
        print(f"\n### {model} (guard={guard}) -> {path.name}" + (f" | retomando, faltam {todo}" if done else ""))

        with path.open("w" if new_file else "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new_file:
                w.writeheader()
            for cfg_name in names:
                mit = CONFIGS[cfg_name]
                for kind, item in items:
                    for rep in range(1, args.reps + 1):
                        if (cfg_name, kind, item["id"], rep) in done:
                            continue
                        seed = args.seed_base + rep
                        res = run_chat(ChatRequest(message=item["text"], model=model, guard_model=guard,
                                                   temperature=args.temperature, seed=seed, mitigations=mit),
                                       chat_fn=cached)
                        w.writerow({
                            "model": model, "run_id": args.run_id, "split": item.get("split", "dev"),
                            "config": cfg_name, "kind": kind, "payload_id": item["id"],
                            "category": item.get("category", "benign"), "rep": rep, "seed": seed,
                            "outcome": outcome(res), "blocked_by": res.blocked_by or "",
                            "leak_kind": res.leak_kind or "", "raw_leaked": int(res.raw_leaked),
                            "error": res.error or "", "response": res.response.replace("\n", " ")[:500],
                        })
                        f.flush()
                        n_done += 1
                        if n_done % 25 == 0 or n_done == todo:
                            rate = (time.time() - t0) / n_done
                            print(f"[{n_done}/{todo}] {cfg_name:<16} chamadas={cached.calls} reaproveitadas={cached.hits} "
                                  f"faltam ~{fmt_eta(rate * (todo - n_done))}", flush=True)
        written.append(path)
        if real:
            ollama_client.unload(model)
            if guard != model:
                ollama_client.unload(guard)

    if written:
        print("\n######## RESUMO ########")
        analyze_results.analyze_paths(written, out_dir / f"consolidated_{args.run_id}.csv")
    return written


if __name__ == "__main__":
    main()
