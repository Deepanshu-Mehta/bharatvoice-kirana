#!/usr/bin/env bash
# Run ON THE RENTED GPU BOX (A100/H100 80 GB, Linux). Costs money while running — stop it after each session.
# Live server deliberately has NO --reasoning-parser: thinking is off per request, and without the parser vLLM
# cannot swap the JSON into the reasoning field. Tool-call flags are omitted: we use structured outputs, not tools.
set -euo pipefail
pip install -q "vllm>=0.12.0" "huggingface_hub[cli]"
export HF_TOKEN="${HUGGING_FACE_TOKEN:?set HUGGING_FACE_TOKEN}"
vllm serve gnani/gnani-evon-v3.3-30B-A3B \
  --trust-remote-code --dtype bfloat16 --max-model-len 16384 \
  --gpu-memory-utilization 0.90 --port 8000
# From the Mac:  ssh -N -L 8000:localhost:8000 <gpu-host>   then   export EVON_URL=http://localhost:8000/v1
