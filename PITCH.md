# BharatVoice: Munshi ji, an AI phone clerk for kirana stores

**Solo entry, Gnani.ai Great Indian AI Internship Challenge.** Deepanshu Mehta.
Live demo: https://deepanshu-m23csai--bharatvoice-demo-web.us-east.modal.run · Code: https://github.com/Deepanshu-Mehta/bharatvoice-kirana

## Problem
A neighbourhood kirana takes phone orders while serving customers at the counter. Calls get missed, and orders get misheard on noisy phone lines: "do kilo" vs "dhai kilo", Aashirvaad vs Pillsbury, "nahi nahi, teen kilo kar do". Customers speak Hinglish, Hindi and dialects like Haryanvi, which most voice AI handles poorly.

## Who uses it
Kirana owners, and the regular customers who would rather call than type, especially older and less literate customers.

## What it does
Munshi ji answers the shop's phone, takes the order the way people actually talk (code-mixed, with corrections), writes the parchi live, reads it back and places it only after an explicit "haan".
- **Remembers regulars.** Every confirmed order is a verified label, so the agent never asks the same customer the same question twice. Questions per call fall from 3.3 to 1.4 over five calls.
- **Calls before you run out.** It learns each customer's purchase rhythm, so the shop can make a one-tap outbound call where the offer is also the readback.
- **Voice udhaar khata.** Credit orders go into the ledger, with limits checked in code.
- **WhatsApp voice notes** become draft orders the shopkeeper confirms with a tap.

## How it uses Gnani's models
| Model | Role |
|---|---|
| **Prisma v3.0** | Hears every caller turn. Each turn is told which brands are in play (`keyterms`) and what the agent just asked (`previous_text`). |
| **Timbre v3.0** | Munshi ji's voice (Yashraj), streamed, in mixed script. This nearly halves the phone-line error. |
| **Timbre v2.5** | 12+ synthetic test callers. |
| **Evon v3.3** | Self-hosted. Understands items that are described rather than named ("woh meethi safed cheez" → sugar). Used on 3.8% of turns. |

Design rule: **the LLM proposes, code decides.** Prices, totals, stock and order placement are deterministic code.

## Results
Synthetic callers on a noisy G.711 8 kHz line with 10 dB shop noise; every number comes from a script in the repo.
- **Dialogue-conditioned speech biasing:** exact orders went from **58% to 90%** (Hinglish).
- **Exact orders by language:** Hindi **100%**, Haryanvi **90%**, Hinglish **84–90%**.
- **Safety:** **0** made-up prices and **0** unconfirmed orders in every run.
- **Latency:** **0.28 s** p50 from the end of speech to the first audio, for common replies.

## Why it matters beyond one shop
Indian speech AI still fails on dialects: error rates are 20–30% for Bhojpuri against under 10% for standard Hindi (Voice of India benchmark, 2026). A kirana agent produces verified labels as a by-product, because each "haan" confirms what the customer's words meant. With consent, every shop that uses it could help teach speech AI how India actually speaks.

**Next:** real telephony (SIP), barge-in, native-speaker Haryanvi review, more dialects (Bhojpuri, Bundeli), and UPI payment after "haan".
