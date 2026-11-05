# BharatVoice: a phone-order agent for kirana stores

**Takes grocery orders over the phone in Hinglish, Hindi and Haryanvi, gets them exactly right on noisy lines, and asks only when it is genuinely unsure.** Built entirely on Gnani.ai's models: **Prisma** (speech-to-text), **Timbre** (text-to-speech and voice cloning) and **Evon v3.3** (language understanding).

> Entry for Gnani.ai's **Great Indian AI Internship Challenge** · solo build · `#GnaniAI` `#GreatIndianAIInternshipChallenge`
>
> 📞 **Try it live:** [call the kirana yourself](https://deepanshu-m23csai--bharatvoice-demo-web.us-east.modal.run) (Chrome + headphones; [shopkeeper board](https://deepanshu-m23csai--bharatvoice-demo-web.us-east.modal.run/board))
> · 🎥 Demo video: *coming soon* · 📊 Results: [below](#results) · 🧪 186 tests, all offline

---

## Why
A neighbourhood kirana takes phone orders while serving customers at the counter. Calls get missed, and orders get misheard on noisy phone lines:
- "do kilo" vs "dhai kilo"
- Aashirvaad vs Pillsbury
- "nahi nahi, teen kilo kar do"

BharatVoice answers the call and takes the order the way people actually talk (code-mixed, with corrections, in their own dialect). It reads the order back and places it only after an explicit "haan". The shopkeeper sees every order and every handoff live on a board.

## What a call looks like
```
Agent : Namaste! Main Sharma General Store ka AI assistant hoon. Boliye, kya bhejna hai?
Caller: Bhaiya, do kilo atta aur ek litre Amul Taaza doodh bhej dena
Agent : Aashirvaad wala ya Pillsbury wala?             ← asks only because "atta" is ambiguous
Caller: Aashirvaad wala
Agent : Theek hai, do kilo Aashirvaad Atta, ek litre Amul Taaza Milk. Aur kuch?
Caller: nahi nahi, doodh do litre kar do                ← mid-call correction
Agent : do litre Amul Taaza Milk kar diya. Aur kuch?
Caller: bas itna hi
Agent : Aapka order: do kilo Aashirvaad Atta, do litre Amul Taaza Milk. Total do sau solah rupaye. Confirm karun?
Caller: haan
Agent : Order confirm! Aadhe ghante mein pahunch jayega. Dhanyavaad!   → order lands on the shopkeeper board
```

## The idea: the shopkeeper's memory, on the phone
A kirana's real edge over a 10-minute delivery app is that bhaiya **knows his customers**: when Sharma ji says "atta" he means Aashirvaad, 5 kilo. BharatVoice learns the same thing, from one source only.

**Every confirmed order is a verified label.** The readback plus an explicit "haan" means we know exactly what the customer's words meant, with no annotation and no retraining. From those confirmed orders, per customer:
- **It never asks the same customer the same question twice.** Tau ji's "kapde dhone aala surf" gets a "kaun sa?" once ("Ghadi aala"). After he confirms, the phrase is his:
  - matched instantly next time
  - added to Prisma's `keyterms` for his calls, so it is *heard* better too
- **It knows the usual.** "Atta bhej dena" gets "Pillsbury atta, pichhli baar jitna das kilo?"
- **It calls you before you run out.** From each customer's purchase rhythm, the counter shows who's due. One tap and the shop calls: *"Namaste Sharma ji, aapka do litre Amul doodh aur paanch kilo Aashirvaad atta khatam hone wala hoga. Wahi bhej doon?"* The offer *is* the readback, so nothing is ordered without a yes, and "haan, par atta das kilo" just works.

Everything learned is per customer, visible on the shop counter and erasable. In the public demo it stays private to each visitor.

## Features
- **Code-mixed and dialect-aware.** Hinglish, Hindi and Haryanvi, with a dialect layer tuned to how Prisma actually transcribes Haryanvi. For example, the negation *konya* comes out as कौन या / कोइना / कोनिया.
- **Dialogue-conditioned speech recognition.** Every turn, Prisma is told which brands are in play (`keyterms`) and what the agent just asked (`previous_text`).
- **Asks only when unsure.** A phonetic resolver matches Devanagari and Roman spellings (आशीर्वाद = Aashirvaad = ashirwad) and gives a confidence score. Confidence decides between adding silently, a two-way question ("Aashirvaad wala ya Pillsbury?") and an open question.
- **Real shop behaviour:**
  - corrections ("nahi nahi…", "atta hata do", "X ki jagah Y")
  - fractions (aadha, paav, sawa, dhai sau gram)
  - out-of-stock substitution with the price difference spoken
  - repeat orders ("pichli baar wala saaman")
  - a sanity check on implausible quantities
  - handoff to the shopkeeper
- **Voice udhaar khata.** "Udhaar mein likh do" works at checkout: the code checks the caller's credit limit (re-checked at placement), updates the ledger and tells them the new balance. Callers can ask "mera kitna baaki hai?". The board shows every balance with a **Remind** button that speaks a polite dues reminder in the customer's own register.
- **WhatsApp voice-note inbox.** Drop a customer's voice note (.ogg/.opus/.m4a) on the board. It is decoded and split at pauses into ≤20 s chunks, with `previous_text` chaining between them, then passed through the same agent logic to a **draft order**. Ambiguous items appear as one-tap choices and the shopkeeper confirms with a tap.
- **Price and stock questions, and money quantities.** "Aashirvaad atta kitne ka hai?", "Amul Gold hai kya?" and "bees rupaye ka dhaniya" are all handled. The quantity is derived from the catalog price, and money lines are read back as money.
- **"Dheere boliye".** The agent repeats itself more slowly (Timbre speed 0.85) for elderly callers.
- **Safe by construction.** Prices, totals and stock come only from code. An order is placed only after a readback and an explicit yes, and placement is idempotent.
- **Fast.** Streaming TTS and cached phrases give about **0.9 s** from the end of the caller's speech to the first agent audio (server side).
- **Measured.** A self-testing harness replays scenarios through Timbre → phone channel (G.711, noise, packet loss) → Prisma → agent, and scores **Order Exact Match**.

## Architecture: the LLM proposes, code decides
```
caller ──► browser VAD ──► Prisma v3.0 (REST, one call per utterance)
                              ▲ keyterms + previous_text built from the dialogue state
                              │
           transcript ──► extractor:  rules (fast path)  ──or──  Evon v3.3 (hard / free-form turns)
                                              │
                                      turn frame (strict JSON): add · update · remove · replace · choose · done …
                                              │
           quantity parser ──► resolver (phonetic, Devanagari ⇄ Roman) ──► calibrated confidence
                                              │
           dialogue manager: gated questions · corrections · substitution · readback · handoff
                     (prices, totals, stock and order placement are deterministic code)
                                              │
           Timbre (SSE streaming, cached) ──► caller            events ──► shopkeeper board
```

### Gnani models used, and exactly where
| Model | Where | How |
|---|---|---|
| **Prisma v3.0** (STT) | Every caller utterance | REST `/stt/v3`, `language_code=hi-IN,en-IN`, `format=codemix`. Per-turn `keyterms` and `previous_text`, plus `topic`, `enable_substitution` and `substitution_map` |
| **Prisma v2.5** (STT) | Ablations | Baseline comparison |
| **Timbre v2.5** (TTS) | Agent voice (Poorvi, Hinglish); every synthetic test caller | SSE streaming for live replies, REST for test audio; everything cached |
| **Voice cloning** (`vachana-vc-v1`) | Optional shopkeeper voice (with consent) | Embedding from a 5–30 s clip, then cloned streaming TTS |
| **Evon v3.3** (30B MoE, open weights) | Turns the rules cannot parse; Evon-only ablation | Self-hosted with vLLM on Modal (A100-80GB, scale-to-zero), thinking off, strict per-action JSON schema |

## Results
All numbers come from `eval/` runs (`eval/results/<run_id>/summary.json`). Test callers are Timbre voices and the noise is synthetic unless stated; see [Limitations](#limitations).

**0. It learns each regular customer** (`eval/learning_curve.py`): 3 personas with their own words × 5 consecutive calls; memory off (every call starts fresh) vs on (learned from earlier *confirmed* orders only)

| | Questions per call (avg) | By the 5th call | Exact orders |
|---|---|---|---|
| Text transcripts, memory off → on | 3.33 → **1.40** | 3.0 → **0.33** | 93% → **100%** |
| Noisy phone audio (G.711 + 10 dB), off → on | 3.27 → **1.67** | 3.0 → **0.33** | 80% → 80% |

Negative result, kept honest: biasing known customers toward *only their own* brands lost accuracy on noisy audio (0.80 → 0.60). The shop-wide brand list stays, and each customer's learned words are added on top.

**1. Dialogue-conditioned ASR biasing** (Hinglish, n = 31, G.711 8 kHz + 10 dB shop noise)

| Prisma context | Order Exact Match | Item F1 | Questions per call |
|---|---|---|---|
| none | 0.58 | 0.73 | 0.84 |
| static brand keyterms | 0.77 | 0.85 | 1.00 |
| **dynamic: brands in play + agent's last question** | **0.90** | **0.94** | **0.48** |

**2. Per language on noisy phone audio** (G.711 + 10 dB, dynamic biasing)

| Register | n | OEM | Notes |
|---|---|---|---|
| Hinglish | 31 | 0.90 | |
| Hindi | 31 | 0.97 | |
| Haryanvi | 34 | 0.77 → **0.88** | After the STT-aware dialect layer; handoffs 15% → 6% |

**3. Where Evon earns its place** (text transcripts)

| Extractor | 93 standard scenarios | 9 free-form ("woh meethi safed cheez jo chai mein daalte hain") | LLM calls |
|---|---|---|---|
| Rules only | 1.00* | 0.78 | 0 |
| Evon v3.3 only | 0.81 (Haryanvi 0.71) | 0.89 | every turn |
| **Hybrid** | **1.00** | **1.00** | **3.8% of turns** |

*The rules extractor was developed on these scenarios, so its score there is an upper bound.

**4. STT loopback alias mining** (Timbre → channel → Prisma over every catalog name)
- Genuine Prisma confusions were curated by hand: "type powder" → Tide, "rind" → Rin, "broke bond" → Red Label, "klinik" → Clinic Plus, "traffic chana" → Kabuli Chana.
- **Held-out** name resolution, using voices never used in mining: **75.0% → 90.3%**.

**5. Hardest channel and a negative result** (G.711 + 5 dB SNR + 2% packet loss, n = 68):
- OEM is 0.71 overall: Hinglish 0.53, Haryanvi 0.88. Compare Hinglish at 0.90 on the 10 dB channel.
- A "second opinion" (re-transcribe weak turns with `format=transliterate` and keep the hypothesis the resolver trusts more) re-transcribed 17% of turns but did **not** help (0.69). It is off by default.

**6. Safety.** Hallucinated-price rate **0** and unconfirmed-order rate **0** in every run, including Evon-only runs.

**7. Latency** (server side, end of speech to first agent audio): STT ≈ 0.2–0.5 s + agent logic ≈ 0.01–0.15 s + first TTS chunk ≈ 0.3–0.6 s ≈ **0.9 s**. Add about 0.45 s of browser end-pointing for what the caller feels.

## Making the shop sound like a person
`infra/voice_lab.py` (listen locally at `/voices`) compares Timbre setups on the same four shop lines and measures how well Prisma understands each voice after an 8&nbsp;kHz call with shop noise:

| Setup | Phone-line error (CER) |
|---|---|
| Before: Poorvi (female), all-Roman text | 14.8% |
| Mixed script, Devanagari Hindi + Latin brands, any voice | 8.2–8.8% |

Changes made:
- **Mixed script.** Timbre reads all-Latin text with an English profile, which sounded anglicised. The display stays Roman, while what Timbre speaks is Devanagari for Hindi and Haryanvi words with Latin brands, and money as "₹216" for Timbre's own number reading. This **nearly halves the phone-line error**.
- **A male, conversational voice** (Timbre v3.0 *Devansh*), matching the shop's male first-person replies, with one persona across Hindi, Hinglish and Haryanvi.
- **`<pauses>` before the closing question**, as Gnani's docs recommend for prompts.
- **Varied acknowledgements** instead of the same sentence every turn.
- **Optional:** the shopkeeper's own cloned voice, with consent (`infra/clone_check.py --ref … --save`).

## What we learned about Gnani's models
- **Number normalisation:** Prisma's default ITN turned "paav kilo" (¼ kg) into "0.5 किलो". `format=codemix` keeps spoken numbers, so we parse fractions ourselves.
- **Biasing can swallow numbers:** "do packet" became "Dove packet" and "moong do kilo" became "Moong Dal kilo". We therefore bias only on brand names of 5+ characters with no digits, and add short brands only when they are in play. Done this way, biasing is the largest gain we measured.
- **Haryanvi through a Hindi model:** *konya* is written as कौन या / कोइना / कोनिया / कौनियाँ, and "bas itna e" as "पास इतना एक". Hence the STT-aware dialect lexicon and the quantity sanity check ("paav kilo haldi" was once heard as "पाँच किलो").
- **One active request per account:** all our processes share a lock and retry 429s patiently.
- **Evon v3.3** follows a strict JSON schema reliably, with no invalid frames. It paraphrases where it should copy ("doodh" → "dugdha") and does not know Haryanvi negation, so it handles the turns rules cannot.

## Quick start
**Prerequisites:** Python 3.13, [uv](https://docs.astral.sh/uv/), `ffmpeg`, a Gnani API key (and optionally a [Modal](https://modal.com) account for Evon).

```bash
git clone https://github.com/Deepanshu-Mehta/bharatvoice-kirana.git && cd bharatvoice-kirana
uv sync
cp .env.example .env                # add GNANI_API_KEY (and HUGGING_FACE_TOKEN for Evon weights)
uv run pytest -q                    # 186 tests, no API calls
uv run uvicorn app.server:app --port 8080
```
Open **http://localhost:8080** (call page; use Chrome and headphones) and **http://localhost:8080/board** (shopkeeper board). You can type turns instead of speaking; that uses no speech-to-text credits.

**Evon v3.3 on Modal (optional):**
```bash
uv run modal setup
uv run modal run infra/modal_evon.py::download      # weights into a Modal volume (CPU)
uv run modal deploy infra/modal_evon.py             # prints the endpoint; set EVON_URL=<endpoint>/v1 in .env
uv run python -m infra.evon_day1_test               # health, JSON-field and latency check
```
Then choose **Hybrid** or **Evon** in the call page's extractor menu.

**Evaluation:**
```bash
uv run python -m eval.run --mode text                                         # agent logic only, zero credits
uv run python -m eval.run --mode audio --condition g711_10db --dry-run         # cost estimate
uv run python -m eval.run --mode audio --condition g711_10db --biasing dynamic --budget 20
uv run python -m eval.run --mode text --extractor hybrid                      # needs EVON_URL
uv run python -m eval.mine_aliases --budget 40                                # STT loopback alias mining
```

## Project structure
```
app/
  gnani_client.py     every Gnani call: cache → credit guard → API → ledger (+ cross-process lock)
  stt_prisma.py       Prisma REST + dialogue-conditioned keyterms / previous_text / substitution
  tts_timbre.py       Timbre REST + SSE streaming, cloned voice
  server.py           FastAPI WebSocket call server + shopkeeper board
  brain/
    text.py           Devanagari → Roman (schwa deletion), phonetic keys, Hindi number words
    qty.py            quantity/unit parser (aadha, paav, sawa, dhai sau gram, darjan …)
    catalog.py        synthetic catalog loader
    resolver.py       phonetic product resolver + confidence
    extract_rules.py  rules extractor + Haryanvi dialect layer
    extract_evon.py   Evon v3.3 extractor (strict JSON schema, fallback to rules)
    extract_hybrid.py rules first, Evon for hard turns
    dm.py             dialogue manager (the only place that decides); outbound "you're due" calls
    memory.py         per-customer memory learned only from confirmed orders
    templates.py      register-aware replies (Hinglish / Hindi / Haryanvi)
  static/             call page, board
data/                 catalog.yaml, customers, Haryanvi lexicon, scenarios, mined aliases, substitution rules
eval/                 runner (text / audio / real-speaker modes), phone channel, scenario generator, STT bake-off,
                      alias mining, held-out check, results/
infra/                Modal/vLLM deployment, smoke tests, voice-clone check, phrase pre-cache
tests/                186 offline tests
```

## Public demo
The live demo runs on Modal (CPU only) in `PUBLIC_DEMO` mode:
- **Spending:** daily Gnani credit cap of 60 on a persistent ledger.
- **Per-IP hourly limits:** calls, voice notes and reminders, plus 25 turns and 6 minutes per call.
- **No Evon:** the rules extractor only, so strangers never wake a GPU.
- **No echoing:** the agent never speaks a caller's words back, and the board hides transcripts.
- **Nothing stored:** no audio or transcripts are kept.

Deploy your own with `uv run modal deploy infra/modal_app.py`.

## Cost control
- Every Gnani call goes through one guarded client:
  - cache (re-runs are free)
  - daily credit cap, per-run budget, throttle, kill switch (`touch STOP_CREDITS`), loop detector
  - ledger
- Measured costs: STT ≈ 0.0056 credits/s and TTS ≈ 0.0018 credits/char.
- All evaluation so far used about 1–2% of the 5,000 programme credits.
- Evon runs on Modal: at most one container, scale-to-zero after 10 idle minutes, endpoint protected by an API key.

## Recording real speakers
`http://localhost:8080/record` (local only, disabled in the public demo):
- Speakers pick an alias, read and tick a consent statement, then record each prompt their own way. Clips auto-stop at 20 s and can be played back.
- Two prompt sets: the 30-prompt STT bake-off, or full order conversations per register.
- Clips are saved to the gitignored `data/real/`. Evaluate them with:
  - `uv run python -m eval.bakeoff --source real --budget 10`
  - `uv run python -m eval.run --mode real --speaker <alias> --budget 10`

## Data policy
**Synthetic data only.**
- Customers are `CUST-001`-style IDs, and the catalog and prices are synthetic.
- Recordings of real people are made only with consent and are never committed (`data/real/` is gitignored).

## Limitations
- Test callers are TTS voices and the shop noise is synthetic. A real phone-call set from consenting speakers is in progress.
- The scenarios were written by us, and the rules extractor was tuned on them, so text-mode scores are an upper bound.
- There is one synthetic catalog (81 SKUs) and no real telephony (the browser microphone stands in for the phone).
- Haryanvi resources (lexicon, reply templates, scenarios) are drafts until reviewed by native speakers.
- Evon runs in a US region, which adds about 0.25 s per call from India.

## Why these features (research notes)
- **Udhaar:** industry estimates put more than 70% of kirana transactions partly on credit, and paper ledgers lose money, so the agent handles credit instead of handing it off.
- **Voice notes:** WhatsApp voice notes are already a primary ordering channel; customers send rough spoken lists in mixed Hindi and English.
- **Biasing plus a deterministic resolver instead of LLM post-correction:** recent work finds smaller fine-tuned models beat larger LLMs at Hindi post-ASR correction (Kumar et al., EACL 2026), and in a small practitioner test hotword boosting beat LLM correction. Our measurements agree: dialogue-conditioned biasing was the largest single gain.
- **Haryanvi:** the only open Haryanvi speech corpus we found is Snow Mountain (Bible readings, one speaker), and there is no conversational ASR benchmark.

## Roadmap
Real phone-call evaluation · native-speaker Haryanvi review · SIP telephony · Warp speech-to-speech for lower latency · more dialects (Bhojpuri, Bundeli).

## Acknowledgements
Built for [Gnani.ai's Great Indian AI Internship Challenge](https://www.gnani.ai/internship) using Gnani's Prisma, Timbre and Evon models. Developed with Claude Code.

**Author:** [Deepanshu Mehta](https://github.com/Deepanshu-Mehta) (solo entry)
