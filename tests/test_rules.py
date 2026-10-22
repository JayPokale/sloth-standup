"""The rules the digest applies without the model. Run: python3 -m unittest discover tests"""
import unittest

from standup.collect import Event, PullRequest, clean
from standup.digest import judge, needs_judging


def pr(**kw) -> PullRequest:
    return PullRequest("o/r", 1, "t", "u", **kw)


def ev(author: str, at: str, text: str = "x", kind: str = "comment") -> Event:
    return Event(kind, author, at, text, "url")


class Rules(unittest.TestCase):
    def test_conflict_needs_you(self):
        self.assertTrue(judge(pr(mergeable="CONFLICTING"), "none").needs_me)

    def test_stale_conflict_is_not_news(self):
        p = pr(mergeable="CONFLICTING", updated_at="2022-08-21T00:00:00Z")
        self.assertFalse(needs_judging(p))

    def test_unanswered_thread_needs_you_whatever_its_age(self):
        p = pr(waiting=[ev("reviewer", "2026-01-01T00:00:00Z", kind="thread")])
        self.assertTrue(needs_judging(p))
        self.assertIn("Answer 1 open review thread", judge(p, "none").action)

    def test_changes_requested_needs_you_until_the_reviewer_speaks_again(self):
        review = ev("rev", "2026-10-19T00:00:00Z", "CHANGES_REQUESTED: no", kind="review")
        self.assertTrue(judge(pr(review_decision="CHANGES_REQUESTED", events=[review]), "none").needs_me)
        later = ev("rev", "2026-10-22T00:00:00Z", "looks addressed")
        followed = pr(review_decision="CHANGES_REQUESTED", history=[review], events=[later])
        self.assertFalse(judge(followed, "none").needs_me)

    def test_failure_explained_before_the_latest_commit_does_not_count(self):
        p = pr(failed_checks=["tests"], head_committed_at="2026-10-22T00:00:00Z",
               history=[ev("you", "2026-10-20T00:00:00Z", "the failure is flaky")])
        self.assertFalse(needs_judging(p))  # nothing after the head commit for the model to read

    def test_rollup_only_failure_is_not_a_failure(self):
        self.assertFalse(needs_judging(pr(rollup_failed=True)))

    def test_clean_drops_link_targets(self):
        self.assertEqual(clean("see [the report](https://x.example/a?b=c)"), "see the report")


if __name__ == "__main__":
    unittest.main()
