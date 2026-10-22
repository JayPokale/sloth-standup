"""Save collected pull request activity to JSON and load it back, so the model can be evaluated on fixed input."""
from __future__ import annotations

import json
from dataclasses import asdict

from .collect import Event, PullRequest


def dump(path: str, login: str, since: str, prs: list[PullRequest], merged: list[PullRequest]) -> None:
    with open(path, "w") as f:
        json.dump({"login": login, "since": since, "open": [asdict(p) for p in prs],
                   "merged": [asdict(p) for p in merged]}, f, indent=1)


def _pr(d: dict) -> PullRequest:
    d = dict(d)
    d["events"] = [Event(**e) for e in d["events"]]
    d["history"] = [Event(**e) for e in d["history"]]
    d["waiting"] = [Event(**e) for e in d.get("waiting", [])]
    return PullRequest(**d)


def load(path: str) -> tuple[str, str, list[PullRequest], list[PullRequest]]:
    with open(path) as f:
        data = json.load(f)
    return data["login"], data["since"], [_pr(p) for p in data["open"]], [_pr(p) for p in data["merged"]]
