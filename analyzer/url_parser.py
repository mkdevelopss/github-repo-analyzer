"""Stage 1: turn whatever the user typed into a validated (owner, repo) pair.

Accepted forms (all mean the same repo):
    https://github.com/owner/repo
    http://github.com/owner/repo
    https://www.github.com/owner/repo
    github.com/owner/repo
    https://github.com/owner/repo.git
    https://github.com/owner/repo/
    https://github.com/owner/repo/tree/main/src     (extra path is ignored)
    git@github.com:owner/repo.git                   (SSH clone URL)
    owner/repo                                      (short form)

Anything else raises InvalidURLError with a message saying what is wrong.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .errors import InvalidURLError

# GitHub's own naming rules:
# - owner: 1-39 chars, letters/digits/single hyphens, cannot start or end with '-'
# - repo:  1-100 chars, letters/digits/'.', '_', '-'; cannot be '.' or '..'
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")

# First path segments on github.com that are site pages, not users/orgs.
RESERVED_OWNERS = {
    "about", "explore", "features", "marketplace", "orgs", "pricing",
    "settings", "topics", "trending", "login", "join", "sponsors",
    "notifications", "issues", "pulls", "search", "collections", "apps",
}

USAGE_HINT = "Expected something like: https://github.com/owner/repo"


@dataclass(frozen=True)
class RepoRef:
    owner: str
    repo: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.full_name}"


def parse_repo_url(raw: str) -> RepoRef:
    """Validate the input and return a RepoRef, or raise InvalidURLError."""
    if raw is None or not raw.strip():
        raise InvalidURLError(f"No repository URL given. {USAGE_HINT}")

    text = raw.strip()

    # SSH form: git@github.com:owner/repo.git
    ssh = re.match(r"^git@github\.com:(.+)$", text, re.IGNORECASE)
    if ssh:
        path = ssh.group(1)
    elif re.match(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+/?$", text) and "." not in text.split("/")[0]:
        # Short form owner/repo (owner part has no dot, so it is not a domain)
        path = text
    else:
        # Add a scheme if missing so urlparse puts the domain in netloc.
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
            text = "https://" + text
        parsed = urlparse(text)

        if parsed.scheme.lower() not in ("http", "https"):
            raise InvalidURLError(
                f"Unsupported URL scheme '{parsed.scheme}'. {USAGE_HINT}"
            )

        host = (parsed.hostname or "").lower()
        if not host or " " in host or "." not in host:
            raise InvalidURLError(f"'{raw.strip()}' is not a valid URL. {USAGE_HINT}")
        if host not in ("github.com", "www.github.com"):
            raise InvalidURLError(
                f"'{host or raw}' is not github.com. This tool only analyses "
                f"public GitHub repositories. {USAGE_HINT}"
            )
        path = parsed.path

    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise InvalidURLError(
            f"The URL does not include both an owner and a repository name. {USAGE_HINT}"
        )

    owner, repo = parts[0], parts[1]
    if repo.lower().endswith(".git"):
        repo = repo[:-4]

    if owner.lower() in RESERVED_OWNERS:
        raise InvalidURLError(
            f"'github.com/{owner}/...' is a GitHub site page, not a repository. {USAGE_HINT}"
        )
    if not OWNER_RE.match(owner):
        raise InvalidURLError(f"'{owner}' is not a valid GitHub user or organisation name.")
    if not repo or repo in (".", "..") or not REPO_RE.match(repo):
        raise InvalidURLError(f"'{repo}' is not a valid GitHub repository name.")

    return RepoRef(owner=owner, repo=repo)
