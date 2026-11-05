"""Stage 2 tests: API client error handling with fake HTTP responses (no network)."""

import base64
import os
import sys
import unittest
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analyzer.errors import NetworkError, RateLimitError, RepoNotFoundError
from analyzer.github_client import GitHubClient


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, text_body=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self._text = text_body

    def json(self):
        if self._text is not None:
            raise ValueError("not json")
        return self._body


class FakeSession:
    """Returns queued responses (or raises queued exceptions) in order."""

    def __init__(self, *responses):
        self.responses, self.headers, self.calls = list(responses), {}, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client(*responses, token=""):
    return GitHubClient(token=token, base_url="https://api.test", session=FakeSession(*responses))


class ClientErrors(unittest.TestCase):
    def test_repo_ok(self):
        c = client(FakeResponse(200, {"full_name": "o/r", "default_branch": "main"}))
        self.assertEqual(c.repo("o", "r")["default_branch"], "main")
        self.assertEqual(c.requests_made, 1)

    def test_repo_not_found(self):
        with self.assertRaisesRegex(RepoNotFoundError, "o/r' was not found"):
            client(FakeResponse(404, {"message": "Not Found"})).repo("o", "r")

    def test_rate_limit_with_reset_time(self):
        resp = FakeResponse(403, {"message": "API rate limit exceeded"},
                            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1893456000"})
        with self.assertRaisesRegex(RateLimitError, "resets at .*GITHUB_TOKEN"):
            client(resp).repo("o", "r")

    def test_rate_limit_no_token_tip_when_token_set(self):
        resp = FakeResponse(429, {}, {"Retry-After": "30"})
        with self.assertRaises(RateLimitError) as ctx:
            client(resp, token="x").repo("o", "r")
        self.assertIn("30 seconds", str(ctx.exception))
        self.assertNotIn("GITHUB_TOKEN", str(ctx.exception))

    def test_plain_403_is_not_rate_limit(self):
        with self.assertRaises(NetworkError):
            client(FakeResponse(403, {"message": "Forbidden"}, {"X-RateLimit-Remaining": "40"})).repo("o", "r")

    def test_bad_token(self):
        with self.assertRaisesRegex(NetworkError, "401"):
            client(FakeResponse(401, {})).repo("o", "r")

    def test_timeout_and_connection_errors(self):
        with self.assertRaisesRegex(NetworkError, "did not respond"):
            client(requests.exceptions.Timeout()).repo("o", "r")
        with self.assertRaisesRegex(NetworkError, "internet connection"):
            client(requests.exceptions.ConnectionError()).repo("o", "r")

    def test_invalid_json(self):
        with self.assertRaisesRegex(NetworkError, "not valid JSON"):
            client(FakeResponse(200, text_body="<html>")).repo("o", "r")

    def test_server_error(self):
        with self.assertRaisesRegex(NetworkError, "502"):
            client(FakeResponse(502, {"message": "Bad gateway"})).repo("o", "r")


class ClientData(unittest.TestCase):
    def test_empty_repo_tree_and_commits(self):
        c = client(FakeResponse(409, {"message": "Git Repository is empty."}),
                   FakeResponse(409, {"message": "Git Repository is empty."}),
                   FakeResponse(204, None))
        self.assertEqual(c.tree("o", "r", "main"), ([], False))
        self.assertEqual(c.commits_since("o", "r", datetime.now(timezone.utc)), ([], False))
        self.assertEqual(c.contributors("o", "r"), [])

    def test_tree_truncated_flag(self):
        c = client(FakeResponse(200, {"tree": [{"path": "a.py", "type": "blob"}], "truncated": True}))
        entries, truncated = c.tree("o", "r", "main")
        self.assertEqual(len(entries), 1)
        self.assertTrue(truncated)

    def test_file_text_decodes_and_rejects(self):
        content = base64.b64encode(b"requests==2.0\n").decode()
        c = client(FakeResponse(200, {"type": "file", "size": 14, "encoding": "base64", "content": content}),
                   FakeResponse(200, {"type": "file", "size": 5_000_000, "encoding": "base64", "content": ""}),
                   FakeResponse(404, {}))
        self.assertEqual(c.file_text("o", "r", "requirements.txt", "main"), "requests==2.0\n")
        self.assertIsNone(c.file_text("o", "r", "big.json", "main"))
        self.assertIsNone(c.file_text("o", "r", "missing.txt", "main"))

    def test_commit_pagination_and_cap(self):
        full_page = [{"sha": str(i)} for i in range(100)]
        c = client(FakeResponse(200, full_page), FakeResponse(200, full_page[:30]))
        commits, capped = c.commits_since("o", "r", datetime.now(timezone.utc))
        self.assertEqual((len(commits), capped), (130, False))
        c = client(*[FakeResponse(200, full_page) for _ in range(10)])
        commits, capped = c.commits_since("o", "r", datetime.now(timezone.utc))
        self.assertEqual((len(commits), capped), (1000, True))

    def test_token_header_only_when_set(self):
        self.assertNotIn("Authorization", client(token="").session.headers)
        self.assertEqual(client(token="abc").session.headers["Authorization"], "Bearer abc")


if __name__ == "__main__":
    unittest.main()
