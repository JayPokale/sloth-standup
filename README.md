# sloth-standup 🦥

Reads every open pull request you have, overnight, with a local open-weight model, and leaves you a short morning
digest of the few that actually need you.

If you contribute to several projects, a morning starts with opening dozens of PR tabs: CI reports, bot comments,
review threads. Most say nothing that needs you. sloth-standup reads them so you don't have to, and is built to be
**honest about what it can't tell**: a failing check nobody has explained is listed as exactly that, not guessed at.

```
# Morning standup for @JayPokale
## 🎉 Merged (3)
## 🦥 Needs you (1)
- interledger/rhyza#355: Look at the failing checks (Check if admin changelog is up to date).
## 🧵 Still unanswered (0)
## 👀 FYI (2)
## 🔴 Checks failing, nobody said why (1)
## 🔒 Deploy preview not authorized (1)
## 💤 Quiet (36)
```

## Run it

Needs Python 3.10+, the [GitHub CLI](https://cli.github.com) (logged in), and [Ollama](https://ollama.com) with a
Gemma 4 model. No other dependencies.

```bash
ollama pull gemma4:e2b
python3 -m standup                         # the last 24 hours, digest on stdout
python3 -m standup --hours 72 --out digest.md --notify
python3 -m standup --author someone-else   # anyone's public PRs
```

Every morning at 07:00 (or when the laptop wakes, if it was asleep), with a desktop notification:

```bash
cp -r . ~/sloth-standup
cp systemd/sloth-standup.* ~/.config/systemd/user/
systemctl --user enable --now sloth-standup.timer
```

## How it decides

The code decides everything GitHub already knows; the model only reads text.

**Rules (no model):**
- a merge conflict, or "changes requested" that the reviewer hasn't followed up on, needs you;
- a review thread whose last word is someone else's needs you, however old it is;
- checks that only roll up other checks (`Mergeable Check`), deploy previews of fork PRs, and PRs untouched for six
  months are not news;
- a comment can only explain a failing check if it was written after the latest commit;
- a failing check with no comment to read is listed under "nobody said why", never guessed at.

**The model (Gemma 4 through Ollama, JSON-schema output, temperature 0)** answers two narrow questions per PR:
does a person or a review bot ask the author for something they haven't answered yet, and has anyone explained the
failing checks? Names, links and counts in the digest come from the GitHub data, never from the model.

## How well it works

`eval/` has saved snapshots of real PR activity and hand labels ("does the author need to do something today?"):

```bash
python3 eval/run.py gemma4:e2b gemma4:e4b none     # "none" = rules only
```

41 labelled PRs from five snapshots: 18 of my own (at two points in time), 9 of an AI agent account that contributes
to ClickHouse, and 14 of a ClickHouse maintainer. All results on 2026-10-22, on a GTX 1660 Ti
(`eval/results-2026-10-22.txt`):

| 41 hand-labelled PRs | correct | needs you: caught | false alarms | model time |
|---|--:|--:|--:|--:|
| rules only, no model | 34/41 | 16/17 | 6/24 | 0 s |
| **Gemma 4 E2B** | **40/41** | 16/17 | **0/24** | 357 s |
| Gemma 4 E4B | 38/41 | 16/17 | 2/24 | 473 s |

Read these with their caveats:

- **27 of the 41 were used to tune the rules and the prompt** (mine and the agent's), so they flatter the tool. The 14-PR maintainer set was
  labelled before any run on it and used once: there, rules alone scored 14/14 and both models 13/14. Most of those
  PRs are decided by the open-thread rule, so that set says more about the rules than about the model.
- What the model adds is reading explanations: all 6 false alarms of the rules-only run are failures that a person
  had already explained in a comment. Its one miss (in both models) is the opposite: it took an unrelated comment as
  an explanation of a failing performance check.
- The smaller model did better than the larger one here, and twice as fast; it is the default.
- 2026-10-22, later: the prompt now says a failing check's name is never a request (the model had turned "Check if
  admin changelog is up to date" into "update the admin changelog"). E2B after the change: 40/41 on all labelled PRs
  (a different miss: a PR whose only comment is a bot calling it safe) and 14/14 on the held-out set
  (`eval/results-prompt-v2.txt`).

## Layout

- `standup/collect.py`: GitHub GraphQL through `gh`, with retries and per-PR errors
- `standup/digest.py`: rules, prompt, model call, Markdown digest
- `standup/snapshot.py`: save/replay collected activity (`--save`, `--replay`)
- `eval/`: snapshots, labels, scorer; `tests/`: the rules, no network or model needed

MIT licensed.
