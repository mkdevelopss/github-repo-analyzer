"""Web frontend for the GitHub Repository Analyzer (Track B).

    pip install -r requirements.txt
    python web.py              -> http://127.0.0.1:8000

It reuses exactly the same analysis code as the CLI (analyze.collect), so the
web page and `python analyze.py <url>` always agree. The token is read from
GITHUB_TOKEN (environment or a .env file next to this script) and never sent
to the browser.

API
  GET /api/analyze?url=<repo>[&refresh=1]   full report as JSON (cached 10 minutes per repo)
  GET /api/rate                              remaining GitHub API calls
"""

import os
import sys
import threading
import time
from datetime import date, datetime

from flask import Flask, jsonify, request, send_from_directory

from analyze import collect
from analyzer.errors import AnalyzerError, InvalidURLError, NetworkError, RateLimitError, RepoNotFoundError
from analyzer.github_client import GitHubClient
from analyzer.url_parser import parse_repo_url

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "web_static")
CACHE_SECONDS = 600


def _load_dotenv():
    path = os.path.join(HERE, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()
app = Flask(__name__, static_folder=None)
_cache = {}
_lock = threading.Lock()


class _Silent:
    """collect() reports progress to a status object; the web version has no spinner text."""

    def update(self, *_):
        pass


def _iso(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def to_json(d):
    """Turn the CLI's report data into plain JSON (no Python objects, no secrets)."""
    repo, st, act, dep = d["repo"], d["structure"], d["activity"], d["deps"]
    lic = (repo.get("license") or {}).get("spdx_id") if repo.get("license") else None
    return {
        "repo": {
            "full_name": d["ref"].full_name, "url": d["ref"].html_url,
            "description": repo.get("description"), "stars": repo.get("stargazers_count"),
            "forks": repo.get("forks_count"), "open_issues": repo.get("open_issues_count"),
            "license": lic if lic and lic != "NOASSERTION" else None,
            "default_branch": repo.get("default_branch"), "created": (repo.get("created_at") or "")[:10] or None,
            "pushed": (repo.get("pushed_at") or "")[:10] or None, "archived": bool(repo.get("archived")),
            "topics": repo.get("topics") or [], "homepage": repo.get("homepage") or None,
        },
        "tech": d["tech"],
        "structure": {k: v for k, v in st.items() if k != "paths"},
        "complexity": d["complexity"],
        "deps": {
            "manifests": [m for m, _ in dep["manifests"]], "more_manifests": dep["more_manifests"],
            "unreadable": [{"path": p, "reason": r} for p, r in dep["unreadable"]],
            "summary": dep["summary"],
            "items": [{"name": x.name, "spec": x.spec, "ecosystem": x.ecosystem, "manifest": x.manifest,
                       "dev": x.dev, "latest": x.latest, "released": _iso(x.latest_release),
                       "status": x.status, "issues": x.issues, "note": x.note, "pinned": x.pinned}
                      for x in dep["deps"]],
        },
        "activity": {
            "weekly": act["weekly"], "week_starts": [_iso(w) for w in act["week_starts"]],
            "total": act["total"], "capped": act["capped"], "active_weeks": act["active_weeks"],
            "busiest_week": [_iso(act["busiest_week"][0]), act["busiest_week"][1]] if act["busiest_week"] else None,
            "top_recent": act["top_recent"], "top_all_time": act["top_all_time"],
        },
        "generated": _iso(d["generated"]), "api_requests": d["api_requests"],
    }


ERROR_STATUS = {InvalidURLError: 400, RepoNotFoundError: 404, RateLimitError: 429, NetworkError: 502}


@app.get("/api/analyze")
def api_analyze():
    raw = request.args.get("url", "")
    refresh = request.args.get("refresh") == "1"
    try:
        ref = parse_repo_url(raw)
        key = ref.full_name.lower()
        with _lock:
            hit = _cache.get(key)
        if hit and not refresh and time.time() - hit[0] < CACHE_SECONDS:
            return jsonify({**hit[1], "cached": True, "cached_age_s": int(time.time() - hit[0])})
        client = GitHubClient()
        t0 = time.perf_counter()
        data = to_json(collect(ref, client, _Silent()))
        data["seconds"] = round(time.perf_counter() - t0, 2)
        with _lock:
            _cache[key] = (time.time(), data)
            while len(_cache) > 50:
                _cache.pop(next(iter(_cache)))
        return jsonify({**data, "cached": False})
    except AnalyzerError as e:
        status = next((s for cls, s in ERROR_STATUS.items() if isinstance(e, cls)), 500)
        return jsonify({"error": str(e), "kind": e.__class__.__name__}), status
    except Exception as e:  # unexpected bug: say so plainly, keep the server alive
        app.logger.exception("analysis failed")
        return jsonify({"error": f"Unexpected error ({e.__class__.__name__}). See the server log.",
                        "kind": "InternalError"}), 500


@app.get("/api/rate")
def api_rate():
    info = GitHubClient().rate_limit()
    return jsonify(info or {"unknown": True})


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/<path:path>")
def static_files(path):
    return send_from_directory(STATIC, path)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    token = "set" if os.environ.get("GITHUB_TOKEN") else "NOT SET (60 requests/hour)"
    print(f"Repo Analyzer web UI: http://127.0.0.1:{port}  | GITHUB_TOKEN {token}")
    app.run(host="127.0.0.1", port=port, debug=False)
