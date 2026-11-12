"""Write examples/demo_flow.txt: a seeded synthetic order script for lob_replay.

Usage: python3 scripts/make_demo_flow.py [--seed N] [--lines N] [--out PATH]
"""

import argparse
import random

MID = 10_000  # $100.00 in ticks, the same centre as the benchmark flow


def generate(seed: int, lines: int) -> list[str]:
    rng = random.Random(seed)
    out = [f"# Synthetic order flow for lob_replay, seed {seed}. Not market data."]
    live: list[int] = []
    next_id = 1
    while len(out) - 1 < lines:
        roll = rng.random()
        side = rng.choice("BS")
        if roll < 0.20 and live:
            order_id = live.pop(rng.randrange(len(live)))
            out.append(f"CXL {order_id}")
        elif roll < 0.28 and live:
            order_id = rng.choice(live)
            price = MID - rng.randint(1, 6) if side == "B" else MID + rng.randint(1, 6)
            out.append(f"MOD {order_id} {rng.randint(1, 30)} {price}")
        elif roll < 0.36:
            kind = rng.choice(["MARKET", "IOC", "FOK"])
            price = MID + rng.randint(0, 4) if side == "B" else MID - rng.randint(0, 4)
            out.append(f"ADD {next_id} {side} {price} {rng.randint(5, 40)} {kind}")
            next_id += 1
        else:
            away = rng.randint(1, 8)
            price = MID - away if side == "B" else MID + away
            kind = " POST" if rng.random() < 0.1 else ""
            out.append(f"ADD {next_id} {side} {price} {rng.randint(1, 30)}{kind}")
            live.append(next_id)
            next_id += 1
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--lines", type=int, default=200)
    parser.add_argument("--out", default="examples/demo_flow.txt")
    args = parser.parse_args()
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write("\n".join(generate(args.seed, args.lines)) + "\n")


if __name__ == "__main__":
    main()
