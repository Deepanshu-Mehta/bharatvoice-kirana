"""The demo video's script: four REAL calls through the running app, captured by video/capture.py.

Nothing here is acted out by the app. The caller's lines are spoken by Gnani Timbre voices (labelled as such in
the video), passed through the same simulated phone line as our evals (G.711 8 kHz + street noise, 10 dB SNR),
and played into the browser as if from a microphone. Everything after that (Prisma STT, the agent, Evon, Munshi
ji's voice, the parchi) is the live system doing its job.
"""
from __future__ import annotations

CHANNEL = "g711_10db"  # the noisy-phone condition the README reports results for
# Calls are recorded with the caller's clean voice; a scene with "noisy": True plays the same lines through the
# simulated noisy phone line instead (the film's labelled noisy-line beat uses such a take, "first_noisy").

# Caller voices (Timbre v2.5). The shop's own voice is Munshi ji (Timbre v3.0 Yashraj), as in the product.
VOICES = {
    "sharma": ("Kaveri", "hi-en"),   # Sharma ji's household, Hinglish
    "gupta": ("Bhavna", "hi-IN"),    # Gupta ji, Hindi
    "tau": ("Roopesh", "hi-en"),     # Tau ji, Haryanvi
}

SCENES = [
    {"id": "first", "customer": "CUST-001", "voice": "sharma", "fresh_memory": True, "lines": [
        "Bhaiya, do kilo Aashirvaad atta, ek litre Amul Taaza doodh, aur kapde dhone wala powder bhej do.",
        "Surf Excel wala.",
        "Aur haan, doodh, nahi nahi, doodh do litre kar do.",
        "Bas itna hi.",
        "Haan, confirm kar do.",
    ]},
    {"id": "again", "customer": "CUST-001", "voice": "sharma", "lines": [
        "Wahi kapde dhone wala powder bhej do.",
        "Bas itna hi.",
        "Haan.",
    ]},
    {"id": "evon", "customer": "CUST-003", "voice": "gupta", "lines": [
        "वो मीठी सफ़ेद चीज़ जो चाय में डालते हैं, एक किलो भेज दीजिए।",
        "बस इतना ही।",
        "हाँ।",
    ]},
    {"id": "outbound", "customer": "CUST-002", "voice": "tau", "outbound": True, "fresh_memory": True, "lines": [
        "हाँ भेज दे, खाते में लिख दियो।",
    ]},
]
