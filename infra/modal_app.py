"""Public "call the kirana yourself" demo: the BharatVoice FastAPI app on Modal (CPU only, no GPU).

Guard rails (PUBLIC_DEMO=1, see app/server.py):
  * Gnani daily credit cap 60 (ledger on a persistent Modal Volume, so restarts don't reset it)
  * per-IP hourly limits on calls, voice notes and reminders; 25 turns / 6 minutes per call; <= 3 concurrent calls
  * rules extractor only (strangers never wake the Evon GPU); the agent never speaks a caller's words back
  * max_containers=2 so a redeploy can start while old connections drain (429 retries cover the one-request limit)

  uv run modal deploy infra/modal_app.py                 # prints https://<workspace>--bharatvoice-demo-web.modal.run
  uv run modal run infra/modal_app.py::seed_cache        # copy pre-cached agent phrases into the volume (optional)
  uv run modal app stop bharatvoice-demo                 # take the demo offline
"""
from __future__ import annotations

from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parent.parent
REMOTE = "/root/bv"


def _secret_values() -> dict[str, str]:
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return {"GNANI_API_KEY": env.get("GNANI_API_KEY", "")}  # nothing else leaves the laptop


image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install("fastapi>=0.115", "uvicorn>=0.30", "httpx", "pyyaml", "rapidfuzz", "indic-transliteration",
                 "pydantic>=2", "python-dotenv", "numpy", "soundfile", "python-multipart")
    .env({"PUBLIC_DEMO": "1", "GNANI_DAILY_CAP": "60", "GNANI_MAX_CALLS_PER_MIN": "60",
          "GNANI_LEDGER_PATH": "/state/credits.db", "GNANI_CACHE_DIR": "/state/cache",
          "GNANI_LOCK_PATH": "/tmp/.gnani.lock", "EXTRACTOR": "rules", "PYTHONPATH": REMOTE})
    .add_local_dir(ROOT / "app", f"{REMOTE}/app", ignore=["**/__pycache__/**"])
    .add_local_dir(ROOT / "data", f"{REMOTE}/data", ignore=["real/**", "**/*_raw.*", "noise/*.wav"])
)
state = modal.Volume.from_name("bharatvoice-demo-state", create_if_missing=True)
secret = modal.Secret.from_dict(_secret_values() if modal.is_local() else {})
app = modal.App("bharatvoice-demo")


@app.function(image=image, volumes={"/state": state}, secrets=[secret], max_containers=2, scaledown_window=10 * 60,
              timeout=60 * 60)
@modal.concurrent(max_inputs=40)
@modal.asgi_app()
def web():
    import os
    os.makedirs("/state/cache", exist_ok=True)
    from app.server import app as fastapi_app
    return fastapi_app


@app.local_entrypoint()
def seed_cache():
    """Upload the locally cached agent phrases (slot-free templates) so the demo greets instantly."""
    import string
    import sys

    sys.path.insert(0, str(ROOT))
    from app.brain.templates import T
    from app.gnani_client import GnaniClient
    from app.tts_timbre import LANG, MODEL, VOICE, spoken_text

    c = GnaniClient(api_key="x")
    n = 0
    with state.batch_upload(force=True) as batch:
        for name, table in T.items():
            for reg, tmpl in table.items():
                fields = [f for _, f, _, _ in string.Formatter().parse(tmpl) if f]
                if fields and fields != ["shop"]:
                    continue
                text = tmpl.format(shop="Sharma General Store") if fields else tmpl
                for speed in (1.0, 0.85):
                    _, key = c._tts_body(spoken_text(text), VOICE, MODEL, LANG, speed, 16000, None)
                    local = ROOT / "eval" / "cache" / "tts" / f"{key}.wav"
                    if local.exists():
                        batch.put_file(str(local), f"/cache/tts/{key}.wav")
                        n += 1
        import yaml
        from app.brain.templates import render
        for cid, cust in yaml.safe_load((ROOT / "data" / "customers.yaml").read_text()).items():
            for reg in ("hinglish", "hindi", "haryanvi"):  # "Namaste Sharma ji!" plays instantly for regulars
                text = render("greet_known", reg, name=cust["name"], shop="Sharma General Store")
                _, key = c._tts_body(spoken_text(text), VOICE, MODEL, LANG, 1.0, 16000, None)
                local = ROOT / "eval" / "cache" / "tts" / f"{key}.wav"
                if local.exists():
                    batch.put_file(str(local), f"/cache/tts/{key}.wav")
                    n += 1
    print(f"uploaded {n} cached phrases")
