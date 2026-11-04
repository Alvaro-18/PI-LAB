#!/usr/bin/env bash
# Baixa os modelos de um grupo de models.json.   Uso: scripts/pull_models.sh [core|extra|tight|all]
set -euo pipefail
cd "$(dirname "$0")/.."
group="${1:-core}"
models=$(python3 - "$group" <<'EOF'
import json, sys
g = sys.argv[1]
d = json.load(open("models.json", encoding="utf-8"))
names = [m for k in ("core", "extra", "tight") for m in d[k]] if g == "all" else d[g]
print(" ".join(names))
EOF
)
for m in $models; do
  echo "==> ollama pull $m"
  ollama pull "$m" || echo "!! não consegui baixar $m (tag inexistente?). Seguindo."
done
ollama list
