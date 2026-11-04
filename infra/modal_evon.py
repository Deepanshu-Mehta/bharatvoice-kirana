"""Serve Evon v3.3 on Modal with vLLM (OpenAI-compatible), scale-to-zero, protected by an API key.

Cost control:
  * A100-80GB billed per second (~$2.50/h list price); scales to zero after SCALEDOWN_MIN idle minutes
  * max_containers=1 so a bug can never fan out to many GPUs
  * weights downloaded once into a Modal Volume by a CPU function (no GPU time spent downloading)
  * Starter plan without a card = hard stop at the $30/month included credit

One-time setup (on the Mac):
  uv run modal setup                                   # browser login
  uv run modal run infra/modal_evon.py::download       # CPU: pull ~60 GB of weights into the volume
Serve:
  uv run modal deploy infra/modal_evon.py              # prints https://<workspace>--bharatvoice-evon-serve.modal.run
  export EVON_URL=https://<...>.modal.run/v1  EVON_API_KEY=<value in .env>
Stop (frees the GPU immediately):
  uv run modal app stop bharatvoice-evon
"""
from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path

import modal

MODEL = "gnani/gnani-evon-v3.3-30B-A3B"
GPU = os.environ.get("EVON_GPU", "A100-80GB")
SCALEDOWN_MIN = int(os.environ.get("EVON_SCALEDOWN_MIN", "10"))
ROOT = Path(__file__).resolve().parent.parent


def _local_secrets() -> dict[str, str]:
    """HF token + an API key for the endpoint. Only these two values are sent to Modal (never GNANI_API_KEY)."""
    env: dict[str, str] = {}
    envfile = ROOT / ".env"
    if envfile.exists():
        for line in envfile.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    if "EVON_API_KEY" not in env and envfile.exists() and modal.is_local():
        env["EVON_API_KEY"] = secrets.token_urlsafe(32)
        with envfile.open("a") as f:
            f.write(f"\nEVON_API_KEY={env['EVON_API_KEY']}\n")
    return {"HF_TOKEN": env.get("HUGGING_FACE_TOKEN", ""), "EVON_API_KEY": env.get("EVON_API_KEY", "")}


image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("vllm>=0.12.0", "huggingface_hub[hf_transfer]>=0.30")
    # FlashInfer's sampler JIT-compiles with nvcc, which debian_slim lacks -> use vLLM's PyTorch sampler.
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "VLLM_USE_FLASHINFER_SAMPLER": "0"})
)
hf_cache = modal.Volume.from_name("evon-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("evon-vllm-cache", create_if_missing=True)
evon_secret = modal.Secret.from_dict(_local_secrets() if modal.is_local() else {})
app = modal.App("bharatvoice-evon")
VOLUMES = {"/root/.cache/huggingface": hf_cache, "/root/.cache/vllm": vllm_cache}


@app.function(image=image, volumes=VOLUMES, secrets=[evon_secret], timeout=2 * 60 * 60, cpu=4)
def download():
    from huggingface_hub import snapshot_download

    path = snapshot_download(MODEL, token=os.environ.get("HF_TOKEN") or None)
    hf_cache.commit()
    print("weights at", path)


@app.function(image=image, gpu=GPU, volumes=VOLUMES, secrets=[evon_secret], timeout=24 * 60 * 60,
              scaledown_window=SCALEDOWN_MIN * 60, max_containers=1)
@modal.concurrent(max_inputs=32)
@modal.web_server(port=8000, startup_timeout=12 * 60)  # a crashed engine must not hold a GPU for long
def serve():
    # No --reasoning-parser: thinking is disabled per request and the JSON must land in `content`.
    cmd = [
        "vllm", "serve", MODEL, "--served-model-name", MODEL,
        "--trust-remote-code", "--dtype", "bfloat16", "--max-model-len", "16384",
        "--gpu-memory-utilization", "0.90", "--host", "0.0.0.0", "--port", "8000",
        "--api-key", os.environ["EVON_API_KEY"],
    ]
    subprocess.Popen(cmd)
