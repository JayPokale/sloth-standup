"""sloth-standup: read your pull requests overnight, so you don't have to.

    python -m standup                  # activity in the last 24 hours, digest to stdout
    python -m standup --hours 72 --out digest.md --notify
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from .collect import collect
from .digest import judge, needs_judging, render, unload
from .snapshot import dump, load


def main() -> int:
    parser = argparse.ArgumentParser(prog="standup", description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=24, help="look back this many hours (default 24)")
    parser.add_argument("--model", default="gemma4:e4b", help="Ollama model (default gemma4:e4b)")
    parser.add_argument("--out", help="also write the digest to this file")
    parser.add_argument("--notify", action="store_true", help="show a desktop notification (notify-send)")
    parser.add_argument("--save", metavar="FILE", help="save the collected activity to FILE (JSON)")
    parser.add_argument("--replay", metavar="FILE", help="use activity saved with --save instead of GitHub")
    parser.add_argument("--stats", action="store_true", help="print how much reading the digest saved, to stderr")
    args = parser.parse_args()

    if args.replay:
        login, since, prs, merged = load(args.replay)
    else:
        since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        login, prs, merged = collect(since)
    if args.save:
        dump(args.save, login, since, prs, merged)
    busy = [p for p in prs if needs_judging(p)]
    quiet = [p for p in prs if not needs_judging(p)]
    verdicts = []
    for i, pr in enumerate(busy, 1):
        print(f"[{i}/{len(busy)}] reading {pr.key}", file=sys.stderr)
        verdicts.append(judge(pr, args.model))
    if verdicts:
        unload(args.model)
    digest = render(login, since, verdicts, quiet, merged)

    print(digest)
    if args.out:
        with open(args.out, "w") as f:
            f.write(digest)
    if args.notify:
        act = [v for v in verdicts if v.needs_me]
        title = f"🦥 {len(act)} PR(s) need you" if act else "🦥 Nothing needs you"
        body = "\n".join(f"{v.pr.key}: {v.action or v.summary}" for v in act[:5]) or f"{len(merged)} merged, {len(prs)} open."
        subprocess.run(["notify-send", "-a", "sloth-standup", title, body], check=False)
    if args.stats:
        raw_words = sum(len(e.text.split()) for p in prs for e in p.events)
        stats = {
            "open_prs": len(prs), "prs_with_activity": len(busy), "events": sum(len(p.events) for p in prs),
            "words_of_activity": raw_words, "words_in_digest": len(digest.split()),
            "model_seconds": round(sum(v.seconds for v in verdicts), 1),
        }
        print(json.dumps(stats), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
