"""Voice lab: which Timbre setup makes the shopkeeper sound like a person, not an AI?

Same 4 shop lines in several setups. Writes WAVs to state/voicelab/ (listen at http://localhost:8080/voices) and
measures intelligibility over the phone line: Timbre -> G.711 8 kHz + 10 dB noise -> Prisma, character error rate.

  uv run python -m infra.voice_lab --budget 6
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rapidfuzz.distance import Levenshtein

from app.brain.text import to_roman
from app.gnani_client import get_client
from eval.channel import apply

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "state" / "voicelab"

# Current product text (Roman, as the templates produce it)
ROMAN = {
    "greet": "Namaste! Main Sharma General Store ka AI assistant hoon. Boliye, kya bhejna hai?",
    "clarify": "Aashirvaad wala ya Pillsbury wala?",
    "readback": "Aapka order: do kilo Aashirvaad Atta, do litre Amul Taaza Milk. Total do sau solah rupaye. Confirm karun?",
    "outbound": "Namaste Sharma ji, Sharma General Store se bol raha hoon. Aapka do litre Amul Taaza Milk, paanch kilo "
                "Aashirvaad Atta khatam hone wala hoga. Wahi bhej doon? Total teen sau bahattar rupaye.",
}
# Spoken script: Devanagari for Hindi words, Latin for brands/English (Timbre's Hinglish profile), humanised,
# with digits and ₹ left to Timbre's own text normalisation.
SPOKEN = {
    "greet": "नमस्ते जी! Sharma General Store से, मैं दुकान का AI assistant बोल रहा हूँ। बोलिए, क्या भेजना है?",
    "clarify": "अच्छा जी, Aashirvaad वाला या Pillsbury वाला?",
    "readback": "ठीक है। तो दो किलो Aashirvaad आटा, और दो लीटर Amul Taaza दूध। कुल ₹216 हुए। Confirm कर दूँ?",
    "outbound": "नमस्ते शर्मा जी! Sharma General Store से बोल रहा हूँ। आपका Amul दूध और Aashirvaad आटा ख़त्म होने वाला होगा। "
                "वही भेज दूँ? दो लीटर दूध, पाँच किलो आटा, कुल ₹372।",
}
# v3.0 only: a pause before the question, the way the docs recommend for IVR prompts
TAGGED = {
    "greet": "नमस्ते जी! Sharma General Store से, मैं दुकान का AI assistant बोल रहा हूँ। <pauses> बोलिए, क्या भेजना है?",
    "clarify": "अच्छा जी, <pauses> Aashirvaad वाला या Pillsbury वाला?",
    "readback": "ठीक है। तो दो किलो Aashirvaad आटा, और दो लीटर Amul Taaza दूध। कुल ₹216 हुए। <pauses> Confirm कर दूँ?",
    "outbound": "नमस्ते शर्मा जी! Sharma General Store से बोल रहा हूँ। आपका Amul दूध और Aashirvaad आटा ख़त्म होने वाला होगा। "
                "<pauses> वही भेज दूँ? दो लीटर दूध, पाँच किलो आटा, कुल ₹372।",
}
SETUPS = [
    ("A", "Current: Poorvi (female), Roman text", "timbre-v2.5", "Poorvi", "hi-en", ROMAN),
    ("B", "Poorvi, spoken Devanagari+Latin script", "timbre-v2.5", "Poorvi", "auto", SPOKEN),
    ("C", "Vikrant (male, v2.5), spoken script", "timbre-v2.5", "Vikrant", "auto", SPOKEN),
    ("D", "Hemraj (male, v2.5), spoken script", "timbre-v2.5", "Hemraj", "auto", SPOKEN),
    ("E", "Devansh (male, v3.0 conversational), spoken + pauses", "timbre-v3.0", "Devansh", "auto", TAGGED),
    ("F", "Yashraj (male, v3.0 warm), spoken + pauses", "timbre-v3.0", "Yashraj", "auto", TAGGED),
    ("G", "Shubham (male, v3.0 expressive), spoken + pauses", "timbre-v3.0", "Shubham", "auto", TAGGED),
]


# Round 2: the "Munshi ji" persona (greets regulars by name, remembers the usual) across male v3.0 voices
MUNSHI = {
    "greet": "नमस्ते शर्मा जी! शर्मा जनरल स्टोर से, मैं दुकान का AI मुंशी बोल रहा हूँ। <pauses> बोलिए, आज क्या भेजूँ?",
    "clarify": "अच्छा जी, Aashirvaad आटा, वही पिछली बार जितना, पाँच किलो? <pauses> भेज दूँ?",
    "readback": "ठीक है शर्मा जी। तो पाँच किलो Aashirvaad आटा, और दो लीटर Amul Taaza दूध। कुल ₹372 हुए। <pauses> Confirm कर दूँ?",
    "outbound": "राम राम ताऊ जी! शर्मा जनरल स्टोर तै, मैं दुकान का AI मुंशी बोल र्या सूँ। <pauses> बोलो, आज के भेजूँ?",
}
ROUND2 = [
    ("M1", "Yashraj, warm", "timbre-v3.0", "Yashraj", "auto", MUNSHI, 1.0),
    ("M2", "Yashraj, warm, brisk counter pace", "timbre-v3.0", "Yashraj", "auto", MUNSHI, 1.08),
    ("M3", "Devansh, conversational", "timbre-v3.0", "Devansh", "auto", MUNSHI, 1.0),
    ("M4", "Devansh, conversational, brisk", "timbre-v3.0", "Devansh", "auto", MUNSHI, 1.08),
    ("M5", "Prakash, professional", "timbre-v3.0", "Prakash", "auto", MUNSHI, 1.0),
    ("M6", "Harshad, professional", "timbre-v3.0", "Harshad", "auto", MUNSHI, 1.0),
    ("M7", "Avinash, professional", "timbre-v3.0", "Avinash", "auto", MUNSHI, 1.0),
    ("M8", "Shubham, expressive and energetic", "timbre-v3.0", "Shubham", "auto", MUNSHI, 1.0),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=6)
    ap.add_argument("--round", type=int, default=1)
    a = ap.parse_args()
    setups = SETUPS if a.round == 1 else ROUND2
    OUT.mkdir(parents=True, exist_ok=True)
    c = get_client()
    results = []
    run_id = "voicelab" if a.round == 1 else f"voicelab{a.round}"
    with c.run(run_id, budget=a.budget):
        for setup in setups:
            sid, label, model, voice, lang, lines = setup[:6]
            speed = setup[6] if len(setup) > 6 else 1.0
            row = {"id": sid, "label": label, "model": model, "voice": voice, "speed": speed, "clips": {}, "cer": {}}
            for name, text in lines.items():
                wav = c.tts(text, voice, model=model, language=lang, speed=speed)
                (OUT / f"{sid}-{name}.wav").write_bytes(wav)
                heard = c.stt(apply(wav, "g711_10db", seed=7), "v.wav", model="prisma-v3.0",
                              language_code="hi-IN,en-IN", format="codemix").get("transcript", "")
                ref = to_roman(text.replace("<pauses>", ""))
                row["cer"][name] = round(Levenshtein.normalized_distance(ref, to_roman(heard)), 3)
                row["clips"][name] = {"text": text, "heard": heard}
            row["mean_cer"] = round(sum(row["cer"].values()) / len(row["cer"]), 3)
            results.append(row)
            print(f"{sid} {label:55s} phone-line CER {row['mean_cer']:.3f}")
    (OUT / ("results.json" if a.round == 1 else f"results{a.round}.json")).write_text(
        json.dumps(results, ensure_ascii=False, indent=1))
    print(f"spent ~{c.spent_in_run(run_id):.3f} credits; listen at http://localhost:8080/voices")


if __name__ == "__main__":
    main()
