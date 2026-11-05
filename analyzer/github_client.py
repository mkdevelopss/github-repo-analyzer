"""Stage 2: one small client for every GitHub REST API call the tool makes.

Why one client:
- Every request goes through `_get`, so 404 / rate limit / network errors are
  handled in ONE place and always become clear AnalyzerError messages.
- The token is read from the GITHUB_TOKEN environment variable only (never
  hard-coded, never printed). Without it GitHub allows 60 requests/hour; with
  it, 5,000. One full analysis uses roughly 10-25 requests.
- GITHUB_API_URL can point the client at GitHub Enterprise (or a local test
  server). Default: https://api.github.com
"""

import base64
import os
from datetime import datetime, timezone

import requests

from .errors import NetworkError, RateLimitError, RepoNotFoundError

DEFAULT_API = "https://api.github.com"
TIMEOUT = 20  # seconds per request
MAX_COMMIT_PAGES = 10  # 10 x 100 = at most 1,000 commits for the activity chart
MAX_FILE_BYTES = 1_000_000  # do not download manifest files larger than 1 MB


class GitHubClient:
    def __init__(self, token=None, base_url=None, session=None):
        self.base_url = (base_url or os.environ.get("GITHUB_API_URL") or DEFAULT_API).rstrip("/")
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "repo-analyzer-cli",
        })
        if self.token:
            self.session.headers["Authorization"] = f"Bearer {self.token}"
        self.requests_made = 0

    # ------------------------------------------------------------------ core
    def _get(self, path, params=None, allow_statuses=()):
        """GET a path. Returns (status_code, json). Raises AnalyzerError on failure.

        `allow_statuses` lists codes the caller wants back instead of an error
        (e.g. 409 = empty repository for the commits endpoint).
        """
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.get(url, params=params, timeout=TIMEOUT)
        except requests.exceptions.Timeout:
            raise NetworkError(f"GitHub did not respond within {TIMEOUT}s. Check your connection and try again.")
        except requests.exceptions.ConnectionError:
            raise NetworkError("Could not connect to GitHub. Check your internet connection.")
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Request to GitHub failed: {e.__class__.__name__}")
        self.requests_made += 1

        status = resp.status_code
        if status in allow_statuses:
            return status, _safe_json(resp)

        if status in (403, 429) and _is_rate_limited(resp):
            raise RateLimitError(_rate_limit_message(resp, bool(self.token)))
        if status == 401:
            raise NetworkError("GitHub rejected the token in GITHUB_TOKEN (401). Remove or fix it and try again.")
        if status == 404:
            raise RepoNotFoundError("Not found (404)")  # caller adds context
        if status >= 400:
            msg = (_safe_json(resp) or {}).get("message", "") if isinstance(_safe_json(resp), dict) else ""
            raise NetworkError(f"GitHub API error {status} for {path}. {msg}".strip())

        data = _safe_json(resp)
        if data is None:
            raise NetworkError(f"GitHub returned a response that is not valid JSON for {path}.")
        return status, data

    # --------------------------------------------------------------- queries
    def repo(self, owner, repo):
        try:
            _, data = self._get(f"/repos/{owner}/{repo}")
        except RepoNotFoundError:
            raise RepoNotFoundError(
                f"Repository '{owner}/{repo}' was not found. Check the spelling. "
                f"Private repositories cannot be analysed without access."
            )
        return data

    def languages(self, owner, repo):
        """{language: bytes}, as measured by GitHub's Linguist."""
        _, data = self._get(f"/repos/{owner}/{repo}/languages")
        return data if isinstance(data, dict) else {}

    def tree(self, owner, repo, branch):
        """All files and folders in one call. Returns (entries, truncated).

        Empty repositories have no tree: returns ([], False).
        """
        try:
            status, data = self._get(
                f"/repos/{owner}/{repo}/git/trees/{branch}",
                params={"recursive": "1"}, allow_statuses=(409,),
            )
        except RepoNotFoundError:
            return [], False  # branch has no commits yet
        if status == 409 or not isinstance(data, dict):
            return [], False
        return data.get("tree", []), bool(data.get("truncated"))

    def file_text(self, owner, repo, path, ref):
        """Text of one file, or None if it is missing, binary or too large."""
        try:
            _, data = self._get(f"/repos/{owner}/{repo}/contents/{path}", params={"ref": ref})
        except RepoNotFoundError:
            return None
        if not isinstance(data, dict) or data.get("type") != "file":
            return None
        if data.get("size", 0) > MAX_FILE_BYTES or data.get("encoding") != "base64":
            return None
        try:
            return base64.b64decode(data.get("content", "")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None

    def commits_since(self, owner, repo, since: datetime):
        """Commits on the default branch since `since`. Returns (commits, capped).

        `capped` is True when we stopped at MAX_COMMIT_PAGES, so the report can
        say the count is a lower bound instead of pretending it is complete.
        """
        commits = []
        for page in range(1, MAX_COMMIT_PAGES + 1):
            status, data = self._get(
                f"/repos/{owner}/{repo}/commits",
                params={"since": since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "per_page": 100, "page": page},
                allow_statuses=(409,),
            )
            if status == 409 or not data:  # 409 = empty repository
                return commits, False
            commits.extend(data)
            if len(data) < 100:
                return commits, False
        return commits, True

    def rate_limit(self):
        """Remaining core API requests: {"limit", "remaining", "reset"} or None if unknown.

        GitHub does not count calls to /rate_limit against the limit."""
        try:
            _, data = self._get("/rate_limit")
        except Exception:
            return None
        core = (data.get("resources") or {}).get("core") or data.get("rate") or {}
        if "remaining" not in core:
            return None
        return {"limit": core.get("limit"), "remaining": core.get("remaining"), "reset": core.get("reset"),
                "authenticated": bool(self.token)}

    def contributors(self, owner, repo, top=5):
        """Top contributors by all-time commit count (GitHub's own ranking)."""
        status, data = self._get(
            f"/repos/{owner}/{repo}/contributors",
            params={"per_page": top}, allow_statuses=(204,),
        )
        if status == 204 or not isinstance(data, list):
            return []  # 204 = no contributors (empty repo)
        return data[:top]


# --------------------------------------------------------------- helpers
def _safe_json(resp):
    try:
        return resp.json()
    except ValueError:
        return None


def _is_rate_limited(resp):
    if resp.headers.get("X-RateLimit-Remaining") == "0":
        return True
    if resp.status_code == 429 or "Retry-After" in resp.headers:
        return True
    body = _safe_json(resp)
    msg = body.get("message", "") if isinstance(body, dict) else ""
    return "rate limit" in msg.lower()


def _rate_limit_message(resp, has_token):
    reset = resp.headers.get("X-RateLimit-Reset")
    retry_after = resp.headers.get("Retry-After")
    when = ""
    if reset and reset.isdigit():
        reset_dt = datetime.fromtimestamp(int(reset), tz=timezone.utc).astimezone()
        when = f" It resets at {reset_dt:%H:%M:%S} (local time)."
    elif retry_after and retry_after.isdigit():
        when = f" Try again in {retry_after} seconds."
    tip = "" if has_token else (
        " Tip: set a GitHub token in the GITHUB_TOKEN environment variable to raise "
        "the limit from 60 to 5,000 requests per hour."
    )
    return f"GitHub API rate limit reached.{when}{tip}"
