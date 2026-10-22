"""Turn the collected pull request activity into a short morning digest with a local model.

The model only judges what each PR's new activity means and whether it asks something of you. Everything that can
be wrong in a way that matters (links, names, check counts, merged state) comes from the GitHub data, not from the
model.
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass

from .collect import PullRequest

OLLAMA = "http://localhost:11434"

SYSTEM = """You triage activity on a developer's own open-source pull requests so they can skip reading it.
You get one pull request: its title, review state, failing checks, earlier comments for context ("you" is the
author), and the new comments and reviews posted by other people and bots. Answer in JSON.

- "summary": one plain sentence, at most 25 words, saying what the new events say. Name people by their login.
- "request_for_author": true if a person or a review bot asks the author to change, fix, answer or decide
  something, and the author has not already answered it in the context. False for thanks, approvals, "no
  findings", CI reports that only list results, and comments that say nothing is needed from the author.
- "request": if request_for_author, what the author should do, as a short imperative (at most 15 words); else "".
- "failures_explained": only about the failing checks listed. True if any comment, the author's included, says
  they are unrelated, flaky, or already being fixed elsewhere. False if nobody explains them, or if no check fails.
- "evidence": the number of the new event that best supports your answer, or -1 if none.

Only use facts from the input."""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "request_for_author": {"type": "boolean"},
        "request": {"type": "string"},
        "failures_explained": {"type": "boolean"},
        "evidence": {"type": "integer"},
    },
    "required": ["summary", "request_for_author", "request", "failures_explained", "evidence"],
}


@dataclass
class Verdict:
    pr: PullRequest
    summary: str
    needs_me: bool
    action: str
    evidence_url: str | None
    seconds: float


def describe(pr: PullRequest) -> str:
    lines = [f"Pull request: {pr.key} \"{pr.title}\"",
             f"Review decision: {pr.review_decision or 'none'}; draft: {pr.draft}; labels: {', '.join(pr.labels) or 'none'}",
             f"Checks: {pr.passed_checks} passed, {pr.pending_checks} pending, {len(pr.failed_checks)} failed"
             + (f" ({'; '.join(pr.failed_checks[:8])})" if pr.failed_checks else ""),
             ]
    if pr.history:
        lines.append("Earlier on this PR, for context only (\"you\" is the author):")
        for e in pr.history:
            lines.append(f"- {e.kind} by {e.author} at {e.at}: {e.text[:600]}")
    lines.append("New events (judge these):")
    for i, e in enumerate(pr.events):
        who = f"{e.author} (bot)" if e.is_bot else e.author
        lines.append(f"[{i}] {e.kind} by {who} at {e.at}:\n{e.text}")
    if not pr.events:
        lines.append("(none)")
    return "\n".join(lines)


def ask(model: str, prompt: str) -> dict:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "format": SCHEMA,
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_ctx": 8192},
    }).encode()
    request = urllib.request.Request(f"{OLLAMA}/api/chat", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.loads(json.loads(response.read())["message"]["content"])


def reviewer_followed_up(pr: PullRequest) -> bool:
    """True when whoever last requested changes has commented since: the model reads what they said then."""
    seen = sorted(pr.history + pr.events, key=lambda e: e.at)
    requests = [e for e in seen if e.kind == "review" and e.text.startswith("CHANGES_REQUESTED")]
    if not requests:
        return False
    last = requests[-1]
    return any(e.author == last.author and e.at > last.at for e in seen)


def judge(pr: PullRequest, model: str) -> Verdict:
    start = time.monotonic()
    answer = ask(model, describe(pr))
    evidence = answer.get("evidence", -1)
    url = pr.events[evidence].url if isinstance(evidence, int) and 0 <= evidence < len(pr.events) else None
    # The model answers two narrow questions; the code decides. A failing check needs a look until someone says why.
    asked = bool(answer.get("request_for_author"))
    action = answer.get("request", "").strip() if asked else ""
    unexplained = bool(pr.failed_checks) and not answer.get("failures_explained")
    if unexplained:
        action = (action + " " if action else "") + f"Look at the failing checks ({', '.join(pr.failed_checks[:2])})."
    needs_me = asked or unexplained
    # Judgements we never leave to the model: GitHub already knows these need you.
    if pr.mergeable == "CONFLICTING" and not pr.stale:
        needs_me, action = True, "Merge the base branch in and resolve the conflicts." + (f" Also: {action}" if action else "")
    if pr.review_decision == "CHANGES_REQUESTED" and not action and not reviewer_followed_up(pr):
        needs_me, action = True, "Changes are still requested: address them, or ask for a re-review if you did."
    return Verdict(pr, answer.get("summary", "").strip(), needs_me, action, url, time.monotonic() - start)


def needs_judging(pr: PullRequest) -> bool:
    """Only PRs where someone said something go to the model. Silent failures are listed as they are: with no
    comment to read, the model could only guess why a check failed."""
    if pr.error or (pr.stale and not pr.events):
        return False
    return bool(pr.events or pr.review_decision == "CHANGES_REQUESTED" or pr.mergeable == "CONFLICTING")


def unload(model: str) -> None:
    """Free the GPU right away instead of keeping the model warm for five minutes."""
    body = json.dumps({"model": model, "keep_alive": 0}).encode()
    request = urllib.request.Request(f"{OLLAMA}/api/generate", body, {"Content-Type": "application/json"})
    urllib.request.urlopen(request, timeout=60).read()


def render(login: str, since: str, verdicts: list[Verdict], quiet: list[PullRequest], merged: list[PullRequest]) -> str:
    unread = [p for p in quiet if p.error]
    stale = [p for p in quiet if p.stale and not p.error]
    quiet = [p for p in quiet if not p.stale and not p.error]
    silent_red = [p for p in quiet if p.failed_checks]
    previews = [p for p in quiet if p.preview_checks and not p.failed_checks]
    quiet = [p for p in quiet if not p.failed_checks and not p.preview_checks]
    act = [v for v in verdicts if v.needs_me]
    fyi = [v for v in verdicts if not v.needs_me]
    out = [f"# Morning standup for @{login}", f"Since {since[:16].replace('T', ' ')} UTC.", ""]
    if merged:
        out.append(f"## 🎉 Merged ({len(merged)})")
        out += [f"- [{p.key}]({p.url}) {p.title}" for p in merged]
        out.append("")
    old_threads = [(p, e) for p in [v.pr for v in verdicts] + quiet for e in p.waiting if e.at < since and not p.stale]
    out.append(f"## 🦥 Needs you ({len(act)})")
    if not act:
        out.append("Nothing. Go back to sleep.")
    for v in act:
        link = f" ([why]({v.evidence_url}))" if v.evidence_url else ""
        out.append(f"- **[{v.pr.key}]({v.pr.url})**: {v.action or v.summary}{link}")
        out.append(f"  {v.summary}")
    out.append("")
    if old_threads:
        out.append(f"## 🧵 Still unanswered ({len(old_threads)})")
        out.append("Review threads from before this digest whose last word is someone else's:")
        out += [f"- [{p.key}]({e.url}): {e.author} on {e.at[:10]}: {e.text[:160]}" for p, e in old_threads]
        out.append("")
    if fyi:
        out.append(f"## 👀 FYI ({len(fyi)})")
        out += [f"- [{v.pr.key}]({v.pr.url}): {v.summary}" for v in fyi]
        out.append("")
    if silent_red:
        out.append(f"## 🔴 Checks failing, nobody said why ({len(silent_red)})")
        out.append("Open the logs when you're awake; it may be the PR or the project's CI.")
        out += [f"- [{p.key}]({p.url}): {', '.join(p.failed_checks[:4])}" + (" ..." if len(p.failed_checks) > 4 else "")
                for p in silent_red]
        out.append("")
    if previews:
        out.append(f"## 🔒 Deploy preview not authorized ({len(previews)})")
        out.append("Previews of PRs from forks wait for a maintainer; nothing to do.")
        out += [f"- [{p.key}]({p.url}): {', '.join(p.preview_checks)}" for p in previews]
        out.append("")
    if unread:
        out.append(f"## ⚠️ Couldn't read ({len(unread)})")
        out.append("GitHub didn't answer for these, so they are not in this digest: "
                   + ", ".join(f"[{p.key}]({p.url})" for p in unread))
        out.append("")
    out.append(f"## 💤 Quiet ({len(quiet)})")
    out.append(", ".join(f"[{p.key}]({p.url})" for p in quiet) or "None.")
    if stale:
        out.append("")
        out.append(f"🕸️ Untouched for 6+ months, maybe close them: " + ", ".join(f"[{p.key}]({p.url})" for p in stale))
    return "\n".join(out) + "\n"
