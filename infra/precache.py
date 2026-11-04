"""Pre-synthesise every slot-free agent phrase in every register, so they play from cache instantly (0 ms TTS).

  uv run python -m infra.precache
"""
import string

from app.brain.templates import T
from app.tts_timbre import synth

SHOP = "Sharma General Store"


def main():
    n = 0
    for name, table in T.items():
        for reg, tmpl in table.items():
            fields = [f for _, f, _, _ in string.Formatter().parse(tmpl) if f]
            if fields and fields != ["shop"]:
                continue
            synth(tmpl.format(shop=SHOP) if fields else tmpl)
            n += 1
    print(f"cached {n} phrases")


if __name__ == "__main__":
    main()
