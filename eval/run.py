"""Score the model's needs-you judgement against hand labels, on saved snapshots of real PR activity.

    python3 eval/run.py [model ...]
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from standup.digest import Verdict, judge, needs_judging, unload  # noqa: E402
from standup.snapshot import load  # noqa: E402

HERE = os.path.dirname(__file__)


def main() -> None:
    args = sys.argv[1:]
    only = None
    if args and args[0].endswith(".json"):
        only, args = args[0], args[1:]
    models = args or ["gemma4:e2b"]
    labels = json.load(open(os.path.join(HERE, "labels.json")))
    if only:
        labels = {only: labels[only]}
    for model in models:
        tp = fp = fn = tn = 0
        seconds = 0.0
        for snapshot, expected in labels.items():
            if snapshot.startswith("_"):
                continue
            _, _, prs, _ = load(os.path.join(HERE, snapshot))
            for pr in prs:
                if pr.key not in expected:
                    if needs_judging(pr):
                        print(f"  unlabelled: {pr.key}")
                    continue
                want, why = expected[pr.key]
                if want is None:  # ambiguous even to a person: not scored
                    continue
                if needs_judging(pr):
                    v = judge(pr, model)
                    seconds += v.seconds
                else:
                    # Decided by the rules without the model. A failing check nobody commented on is listed under
                    # "Checks failing, nobody said why": that asks the author to look, so it counts as flagged.
                    flagged = bool(pr.failed_checks) and not pr.stale and not pr.error
                    v = Verdict(pr, "(rules: failing, nobody said why)" if flagged else "(rules: not news)",
                                flagged, "", None, 0.0)
                tp += v.needs_me and want
                fp += v.needs_me and not want
                fn += want and not v.needs_me
                tn += not want and not v.needs_me
                mark = "ok " if v.needs_me == want else "BAD"
                print(f"  {mark} {pr.key}: said {v.needs_me}, want {want} ({why}). {v.action or v.summary}")
        unload(model)
        n = tp + fp + fn + tn
        print(f"{model}: {tp + tn}/{n} correct; needs-you caught {tp}/{tp + fn}, false alarms {fp}/{fp + tn}; "
              f"{seconds:.0f}s of model time")


if __name__ == "__main__":
    main()
