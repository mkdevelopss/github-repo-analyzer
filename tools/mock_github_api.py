"""Local stand-in for the GitHub REST API, built from real git clones.

Used ONLY for testing the full CLI in environments where api.github.com is not
reachable. It serves the same endpoints and JSON shapes the tool uses:

  /repos/{o}/{r}                         metadata (fields git cannot know are null)
  /repos/{o}/{r}/languages               bytes per language (by file extension)
  /repos/{o}/{r}/git/trees/{ref}         recursive tree (git ls-tree)
  /repos/{o}/{r}/contents/{path}         base64 file content (git show)
  /repos/{o}/{r}/commits?since=&page=    commit list (git log)
  /repos/{o}/{r}/contributors            commit counts (git shortlog, clone history only)

Special repos for error testing:
  ratelimited/anything -> 403 with X-RateLimit-Remaining: 0
  anything else unknown -> 404

Usage:  python tools/mock_github_api.py <clones_dir> [port]
Clone folder names are owner_repo, e.g. psf_requests.
Then:   GITHUB_API_URL=http://127.0.0.1:8765 python analyze.py psf/requests
"""

import base64
import json
import os
import subprocess
import sys
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

LANG_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".ts": "TypeScript", ".html": "HTML",
    ".css": "CSS", ".sh": "Shell", ".c": "C", ".go": "Go", ".rb": "Ruby", ".ejs": "EJS",
}
CLONES = sys.argv[1] if len(sys.argv) > 1 else "."


def git(repo_dir, *args):
    return subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, check=True).stdout


def repo_dir(owner, repo):
    d = os.path.join(CLONES, f"{owner}_{repo}")
    return d if os.path.isdir(d) else None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep test output clean
        pass

    def send(self, status, body, headers=None):
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        parts = [unquote(p) for p in url.path.strip("/").split("/")]
        if parts == ["rate_limit"]:
            return self.send(200, {"resources": {"core": {"limit": 5000, "remaining": 4987, "reset": int(time.time()) + 3000}}})
        if len(parts) < 3 or parts[0] != "repos":
            return self.send(404, {"message": "Not Found"})
        owner, repo, rest = parts[1], parts[2], parts[3:]

        if owner == "ratelimited":
            return self.send(403, {"message": "API rate limit exceeded"},
                             {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 1800)})
        d = repo_dir(owner, repo)
        if not d:
            return self.send(404, {"message": "Not Found"})
        branch = git(d, "rev-parse", "--abbrev-ref", "HEAD").decode().strip()

        if not rest:
            last = git(d, "log", "-1", "--format=%cI").decode().strip()
            return self.send(200, {
                "full_name": f"{owner}/{repo}", "default_branch": branch,
                # Not knowable from a git clone -> null, so the report must print "Not determined"
                "description": None, "stargazers_count": None, "forks_count": None,
                "license": None, "created_at": None, "pushed_at": last, "archived": False,
            })
        if rest == ["languages"]:
            sizes = Counter()
            for line in git(d, "ls-tree", "-r", "-l", "HEAD").decode().splitlines():
                meta, path = line.split("\t", 1)
                ext = os.path.splitext(path)[1].lower()
                if ext in LANG_BY_EXT:
                    sizes[LANG_BY_EXT[ext]] += int(meta.split()[3])
            return self.send(200, dict(sizes.most_common()))
        if rest[:2] == ["git", "trees"]:
            tree = []
            for line in git(d, "ls-tree", "-r", "-t", "-l", "HEAD").decode().splitlines():
                meta, path = line.split("\t", 1)
                _, kind, _, size = meta.split()
                entry = {"path": path, "type": kind}
                if kind == "blob":
                    entry["size"] = int(size)
                tree.append(entry)
            return self.send(200, {"tree": tree, "truncated": False})
        if rest[0] == "contents":
            path = "/".join(rest[1:])
            try:
                raw = git(d, "show", f"HEAD:{path}")
            except subprocess.CalledProcessError:
                return self.send(404, {"message": "Not Found"})
            return self.send(200, {"type": "file", "size": len(raw), "encoding": "base64",
                                   "content": base64.b64encode(raw).decode()})
        if rest == ["commits"]:
            per_page, page = int(q.get("per_page", 30)), int(q.get("page", 1))
            out = git(d, "log", f"--since={q.get('since', '1970-01-01')}",
                      "--format=%H%x1f%an%x1f%ae%x1f%aI").decode().splitlines()
            rows = out[(page - 1) * per_page: page * per_page]
            commits = []
            for r in rows:
                sha, name, email, date = r.split("\x1f")
                login = email.split("@")[0].split("+")[-1] if "noreply.github.com" in email else None
                commits.append({"sha": sha, "author": {"login": login} if login else None,
                                "commit": {"author": {"name": name, "email": email, "date": date}}})
            return self.send(200, commits)
        if rest == ["contributors"]:
            counts = Counter(git(d, "log", "--format=%an").decode().splitlines())
            n = int(q.get("per_page", 30))
            return self.send(200, [{"login": a, "contributions": c, "type": "Bot" if "[bot]" in a else "User"}
                                   for a, c in counts.most_common(n)])
        return self.send(404, {"message": "Not Found"})


if __name__ == "__main__":
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
    print(f"Mock GitHub API on http://127.0.0.1:{port} serving clones from {CLONES}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
