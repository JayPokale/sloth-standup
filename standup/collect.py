"""Collect what happened on your open pull requests since a point in time, using the `gh` CLI.

Everything here is facts from the GitHub API; nothing is summarised yet.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# Bots whose comments are only links or status boilerplate. Other bots (CI reports, review bots) are kept.
# Deploy previews fail on pull requests from forks until a maintainer authorizes them; they say nothing about the code.
PREVIEW_CHECKS = ("vercel", "netlify", "cloudflare pages", "deploy preview", "render preview")
NOISE_BOTS = {"netlify", "changeset-bot", "vercel", "codecov", "github-actions", "coderabbitai", "sonarqubecloud"}
FAILED = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}
# Checks that only roll up the others: red when any job failed, or when CI has not run yet (e.g. awaiting approval).
ROLLUP_CHECKS = {"Mergeable Check", "PR", "Finish Check", "CI running"}
# Checks the author cannot act on, e.g. a sync to a private mirror.
IGNORED_CHECKS = {"CH Inc sync"}
PENDING = {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED", "EXPECTED"}

QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      state merged isDraft reviewDecision mergeable updatedAt
      labels(first: 20) { nodes { name } }
      comments(last: 50) { nodes { author { login __typename } createdAt body url } }
      reviews(last: 30) { nodes { author { login __typename } state submittedAt body url } }
      reviewThreads(last: 50) {
        nodes {
          isResolved isOutdated path line
          comments(last: 10) { nodes { author { login __typename } createdAt body url } }
        }
      }
      commits(last: 1) {
        nodes { commit { oid committedDate statusCheckRollup { state contexts(first: 100) { ...Contexts } } } }
      }
    }
  }
}
""" + "\n"

CONTEXTS = """
fragment Contexts on StatusCheckRollupContextConnection {
  pageInfo { hasNextPage endCursor }
  nodes {
    __typename
    ... on CheckRun { name status conclusion }
    ... on StatusContext { context state }
  }
}
"""

MORE_CONTEXTS = """
query($owner: String!, $name: String!, $oid: GitObjectID!, $after: String!) {
  repository(owner: $owner, name: $name) {
    object(oid: $oid) { ... on Commit { statusCheckRollup { contexts(first: 100, after: $after) { ...Contexts } } } }
  }
}
"""


def gh(*args: str, attempts: int = 4):
    """Run `gh` and parse its JSON. Retries, since this runs unattended and the network is not always awake yet."""
    for attempt in range(attempts):
        result = subprocess.run(["gh", *args], capture_output=True, text=True)
        if result.returncode == 0:
            return json.loads(result.stdout) if result.stdout.strip() else None
        if attempt + 1 < attempts:
            time.sleep(5 * 2 ** attempt)
    raise RuntimeError(f"gh {' '.join(args[:2])} failed {attempts} times: {result.stderr.strip()[:300]}")


@dataclass
class Event:
    kind: str          # review, comment, inline_comment
    author: str
    at: str
    text: str
    url: str

    @property
    def is_bot(self) -> bool:
        return self.author.endswith("[bot]")


@dataclass
class PullRequest:
    repo: str
    number: int
    title: str
    url: str
    state: str = "OPEN"
    merged: bool = False
    draft: bool = False
    review_decision: str | None = None
    mergeable: str | None = None
    updated_at: str = ""
    head_committed_at: str = ""  # when the head commit was made: explanations of failures before it are about older code
    rollup_failed: bool = False  # only "all checks" rollups are red, i.e. CI has not run or another job failed
    labels: list[str] = field(default_factory=list)
    failed_checks: list[str] = field(default_factory=list)
    pending_checks: int = 0
    passed_checks: int = 0
    events: list[Event] = field(default_factory=list)   # new, from other people: what the digest is about
    history: list[Event] = field(default_factory=list)  # the last few earlier ones, yours included, as context
    preview_checks: list[str] = field(default_factory=list)  # failed deploy previews (need a maintainer on forks)
    waiting: list[Event] = field(default_factory=list)  # last word of each unresolved thread, when it isn't yours
    error: str = ""  # set when the PR could not be read; the digest says so instead of guessing
    open_threads: int = 0  # unresolved, not outdated review threads, whenever they started

    @property
    def stale(self) -> bool:
        """Nobody has touched it in half a year: its old conflicts and red checks are not news."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return bool(self.updated_at) and self.updated_at < cutoff

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"


def clean(body: str, limit: int = 1500) -> str:
    """Strip what a reader does not see (HTML comments, collapsed details) and keep the start."""
    body = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    body = re.sub(r"<details>.*?</details>", "[details collapsed]", body, flags=re.S)
    body = re.sub(r"<sub>.*?</sub>", "", body, flags=re.S)
    # Links are noise to a reader skimming for meaning (CI links can be kilobytes of base64); keep their text.
    body = re.sub(r"!?\[([^\]]*)\]\([^)\s]+\)", r"\1", body)
    body = re.sub(r"https?://\S{60,}", "[link]", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body if len(body) <= limit else body[:limit] + " [...]"


def my_login() -> str:
    return gh("api", "user")["login"]


def open_pull_requests(login: str, limit: int = 100) -> list[PullRequest]:
    items = gh("search", "prs", "--author", login, "--state", "open", "--sort", "updated", "--limit", str(limit),
               "--json", "repository,number,title,url")
    return [PullRequest(i["repository"]["nameWithOwner"], i["number"], i["title"], i["url"]) for i in items]


def merged_since(login: str, since: str) -> list[PullRequest]:
    items = gh("search", "prs", "--author", login, "--merged", "--merged-at", f">={since[:10]}", "--limit", "100",
               "--json", "repository,number,title,url,closedAt")
    return [PullRequest(i["repository"]["nameWithOwner"], i["number"], i["title"], i["url"], state="MERGED", merged=True)
            for i in items if i["closedAt"] >= since]


def fill(pr: PullRequest, since: str, login: str) -> PullRequest:
    """Add the PR's state, checks, and what other people said since `since` that is still relevant."""
    owner, name = pr.repo.split("/")
    data = gh("api", "graphql", "-f", f"query={QUERY + CONTEXTS}", "-F", f"owner={owner}", "-F", f"name={name}",
              "-F", f"number={pr.number}")["data"]["repository"]["pullRequest"]
    pr.state, pr.merged, pr.draft = data["state"], data["merged"], data["isDraft"]
    pr.review_decision, pr.mergeable, pr.updated_at = data["reviewDecision"], data["mergeable"], data["updatedAt"]
    pr.labels = [label["name"] for label in data["labels"]["nodes"]]

    commit = data["commits"]["nodes"][0]["commit"] if data["commits"]["nodes"] else {}
    pr.head_committed_at = commit.get("committedDate") or ""
    contexts = (commit.get("statusCheckRollup") or {}).get("contexts") or {"nodes": [], "pageInfo": {}}
    nodes = list(contexts["nodes"])
    # Big projects run hundreds of checks; the failed ones are often past the first page.
    while contexts["pageInfo"].get("hasNextPage"):
        contexts = gh("api", "graphql", "-f", f"query={MORE_CONTEXTS + CONTEXTS}", "-F", f"owner={owner}",
                      "-F", f"name={name}", "-F", f"oid={commit['oid']}", "-F", f"after={contexts['pageInfo']['endCursor']}",
                      )["data"]["repository"]["object"]["statusCheckRollup"]["contexts"]
        nodes += contexts["nodes"]
    for ctx in nodes:
        if ctx["__typename"] == "CheckRun":
            label, status = ctx["name"], ctx["conclusion"] if ctx["status"] == "COMPLETED" else ctx["status"]
        else:
            label, status = ctx["context"], ctx["state"]
        status = (status or "").upper()
        if label in IGNORED_CHECKS:
            continue
        if status in FAILED and label in ROLLUP_CHECKS:
            pr.rollup_failed = True
        elif status in FAILED and any(p in label.lower() for p in PREVIEW_CHECKS):
            pr.preview_checks.append(label)
        elif status in FAILED:
            pr.failed_checks.append(label)
        elif status in PENDING:
            pr.pending_checks += 1
        else:
            pr.passed_checks += 1

    def author(node) -> str:
        who = node.get("author") or {}
        login_ = who.get("login") or "ghost"
        return f"{login_}[bot]" if who.get("__typename") == "Bot" else login_

    every: list[Event] = []

    def add(kind: str, who: str, at: str | None, text: str, url: str) -> None:
        if at and who.removesuffix("[bot]") not in NOISE_BOTS:
            every.append(Event(kind, "you" if who == login else who, at, text, url))

    for c in data["comments"]["nodes"]:
        add("comment", author(c), c["createdAt"], clean(c["body"]), c["url"])
    for r in data["reviews"]["nodes"]:
        # A review with no body and state COMMENTED is only the wrapper of inline comments, which come from threads.
        if r["body"] or r["state"] != "COMMENTED":
            add("review", author(r), r["submittedAt"], f"{r['state']}: {clean(r['body'])}", r["url"])
    for t in data["reviewThreads"]["nodes"]:
        # A resolved thread is settled. An outdated one (the code it was on has changed) still waits for you while
        # its last word is someone else's, e.g. a reviewer's question about the old code.
        comments = t["comments"]["nodes"]
        if t["isResolved"] or not comments or (t["isOutdated"] and author(comments[-1]) == login):
            continue
        pr.open_threads += 1
        where = f"{t['path']}:{t['line'] or 'outdated'}"
        last = comments[-1]
        # Whatever its age: a thread nobody answered does not stop waiting because a day went by.
        if author(last) != login:
            pr.waiting.append(Event("thread", author(last), last["createdAt"], f"[{where}] {clean(last['body'], 300)}", last["url"]))
        for c in comments:
            add("inline_comment", author(c), c["createdAt"], f"[{where}] {clean(c['body'])}", c["url"])
    every.sort(key=lambda e: e.at)
    pr.events = [e for e in every if e.at >= since and e.author != "you"]
    # Context for judging the new events, e.g. that you already explained a failure. Your own replies within the
    # window count as context too: they are often the answer to what the reviewer asked.
    pr.history = [e for e in every if e.at < since or e.author == "you"][-4:]
    return pr


def collect(since: str, workers: int = 8, author: str | None = None,
            limit: int = 100) -> tuple[str, list[PullRequest], list[PullRequest]]:
    login = author or my_login()
    prs = open_pull_requests(login, limit)
    def safe_fill(pr: PullRequest) -> PullRequest:
        try:
            return fill(pr, since, login)
        except RuntimeError as e:
            pr.error = str(e)
            return pr

    with ThreadPoolExecutor(workers) as pool:
        prs = list(pool.map(safe_fill, prs))
    return login, prs, merged_since(login, since)
