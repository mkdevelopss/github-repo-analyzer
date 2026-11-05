"""Stage 6: find dependencies in manifest files and judge their health.

Manifests supported:
    Python : requirements*.txt, pyproject.toml (PEP 621 and Poetry)
    Node.js: package.json (dependencies + devDependencies)

"Healthy" in this tool means ALL of the following hold:
    1. Pinned   - the manifest fixes an exact version (Python '==', npm '1.2.3').
                  Ranges like '>=2.0' or '^1.4' are flagged 'unpinned' because a
                  fresh install may pull a different version than the author tested.
    2. Current  - an exact pin is on the same MAJOR version as the latest release
                  on PyPI / npm; a version range must still accept the latest
                  release (e.g. '<3' when 3.x is out, or '^4' when 5.x is out,
                  does not). Otherwise 'outdated'.
    3. Maintained - the package's latest release is less than 2 years old.
                  No release for 2+ years = 'unmaintained'.
Anything we cannot check (registry unreachable, unknown package, git/URL
dependency, too many packages) is reported as 'not checked', never guessed.
"""

import json
import re
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

UNMAINTAINED_DAYS = 730          # 2 years
MAX_REGISTRY_LOOKUPS = 60        # keep runtime and network use bounded
REGISTRY_TIMEOUT = 10

MANIFEST_PATTERNS = [
    (re.compile(r"(^|/)requirements[^/]*\.txt$", re.I), "pypi"),
    (re.compile(r"(^|/)pyproject\.toml$", re.I), "pypi"),
    (re.compile(r"(^|/)package\.json$", re.I), "npm"),
]
SKIP_DIRS = ("node_modules/", "vendor/", "site-packages/", ".venv/", "venv/", "third_party/")


@dataclass
class Dependency:
    name: str
    spec: str            # version text as written in the manifest ('' = none)
    ecosystem: str       # 'pypi' or 'npm'
    manifest: str        # path of the file it came from
    dev: bool = False
    # filled in by check_health()
    pinned: bool = False
    current_version: str | None = None   # version the spec points at (exact or minimum)
    latest: str | None = None
    latest_release: datetime | None = None
    issues: list = field(default_factory=list)
    status: str = "not checked"          # healthy / warning / not checked
    note: str = ""


# ---------------------------------------------------------------- discovery
def find_manifests(tree_paths, limit=6):
    """Return [(path, ecosystem)] for manifest files, root-level ones first."""
    found = []
    for path in tree_paths:
        low = path.lower()
        if any(s in low for s in SKIP_DIRS):
            continue
        for pattern, eco in MANIFEST_PATTERNS:
            if pattern.search(path):
                found.append((path, eco))
                break
    found.sort(key=lambda pe: (pe[0].count("/"), pe[0]))
    return found[:limit], max(0, len(found) - limit)


# ------------------------------------------------------------------ parsers
_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")


def parse_requirements(text, path):
    deps = []
    for raw in text.splitlines():
        line = raw.split(" #")[0].split(";")[0].strip()   # drop comments & env markers
        if not line or line.startswith(("#", "-", "git+", "http:", "https:", "file:")):
            continue
        m = _REQ_LINE.match(line)
        if m:
            deps.append(Dependency(m.group(1), m.group(3).strip(), "pypi", path))
    return deps


def parse_pyproject(text, path):
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None  # caller reports 'could not parse'
    deps = []
    project = data.get("project", {})
    for req in project.get("dependencies", []) or []:
        deps += parse_requirements(req, path)
    for group in (project.get("optional-dependencies", {}) or {}).values():
        for req in group:
            for d in parse_requirements(req, path):
                d.dev = True
                deps.append(d)
    poetry = data.get("tool", {}).get("poetry", {})
    for section, dev in (("dependencies", False), ("dev-dependencies", True)):
        for name, spec in (poetry.get(section, {}) or {}).items():
            if name.lower() == "python":
                continue
            if isinstance(spec, dict):
                spec = spec.get("version", "")
            deps.append(Dependency(name, str(spec), "pypi", path, dev=dev))
    for group in (poetry.get("group", {}) or {}).values():
        for name, spec in (group.get("dependencies", {}) or {}).items():
            if isinstance(spec, dict):
                spec = spec.get("version", "")
            deps.append(Dependency(name, str(spec), "pypi", path, dev=True))
    return deps


def parse_package_json(text, path):
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    deps = []
    for section, dev in (("dependencies", False), ("devDependencies", True)):
        for name, spec in (data.get(section) or {}).items():
            deps.append(Dependency(name, str(spec), "npm", path, dev=dev))
    return deps


def parse_manifest(path, text):
    low = path.lower()
    if low.endswith("package.json"):
        return parse_package_json(text, path)
    if low.endswith("pyproject.toml"):
        return parse_pyproject(text, path)
    return parse_requirements(text, path)


# ----------------------------------------------------------- version logic
_VERSION_RE = re.compile(r"\d+(?:\.\d+)*")


def analyse_spec(dep):
    """Set dep.pinned and dep.current_version from the spec text."""
    spec = dep.spec.strip()
    if dep.ecosystem == "pypi":
        m = re.match(r"^===?\s*([0-9][^,\s]*)$", spec)
        if m:
            dep.pinned, dep.current_version = True, m.group(1)
            return
        lower = re.search(r"(>=|~=|==|>)\s*([0-9][^,\s]*)", spec)
        dep.current_version = lower.group(2) if lower else None
    else:  # npm
        if spec.startswith(("git", "http", "file:", "link:", "workspace:", "npm:")) or "/" in spec:
            dep.note = "non-registry source"
            return
        if re.match(r"^v?\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$", spec):
            dep.pinned, dep.current_version = True, spec.lstrip("v")
            return
        m = _VERSION_RE.search(spec)
        dep.current_version = m.group(0) if m else None


def major(version):
    m = _VERSION_RE.match(version or "")
    return int(m.group(0).split(".")[0]) if m else None


def _vtuple(version):
    m = _VERSION_RE.search(version or "")
    return tuple(int(x) for x in m.group(0).split(".")) if m else None


def _cmp(a, b):
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def _clause_allows(op, ver, latest):
    """Does one version clause (e.g. '<3', '^4.17', '~=1.4') accept `latest`?"""
    if ver.endswith((".*", ".x", ".X")) or ver in ("*", "x", "X"):  # wildcard like 2.* / 1.x
        prefix = _vtuple(ver.rstrip("*xX."))
        if not prefix:
            return True
        return latest[:len(prefix)] == prefix if op in ("", "==", "=") else latest[:len(prefix)] != prefix
    v = _vtuple(ver)
    if v is None:
        return True  # cannot interpret -> do not accuse it of being outdated
    c = _cmp(latest, v)
    if op in ("", "==", "===", "="):
        return c == 0
    if op == "!=":
        return c != 0
    if op == ">=":
        return c >= 0
    if op == ">":
        return c > 0
    if op == "<=":
        return c <= 0
    if op == "<":
        return c < 0
    if op == "^":  # npm/poetry caret: same major (same minor if major is 0)
        keep = 2 if v[0] == 0 and len(v) > 1 else 1
        return c >= 0 and latest[:keep] == v[:keep]
    if op in ("~", "~>"):  # npm tilde: same major.minor
        return c >= 0 and latest[:2] == (v + (0,))[:2]
    if op == "~=":  # PEP 440 compatible release: ~=1.4.5 means >=1.4.5, ==1.4.*
        return c >= 0 and latest[:max(len(v) - 1, 1)] == v[:max(len(v) - 1, 1)]
    return True


def spec_allows(spec, latest_version, ecosystem):
    """True if the version spec accepts the latest release.

    Unknown/blank specs count as allowing it (we never invent a problem).
    """
    latest = _vtuple(latest_version)
    spec = (spec or "").strip()
    if latest is None or not spec or spec in ("*", "latest"):
        return True
    alternatives = spec.split("||") if ecosystem == "npm" else [spec]
    for alt in alternatives:
        clauses = re.findall(r"(===|==|!=|>=|<=|~=|~>|\^|~|>|<|=)?\s*v?([0-9][0-9A-Za-z.*+-]*|\*|x|X)",
                             alt.replace(",", " "))
        if all(_clause_allows(op, ver, latest) for op, ver in clauses):
            return True
    return False


# -------------------------------------------------------------- registries
def _fetch_latest(dep, session):
    """Return (latest_version, latest_release_datetime) or raise."""
    if dep.ecosystem == "pypi":
        r = session.get(f"https://pypi.org/pypi/{dep.name}/json", timeout=REGISTRY_TIMEOUT)
        if r.status_code == 404:
            return None, None
        r.raise_for_status()
        data = r.json()
        latest = data["info"]["version"]
        files = data.get("releases", {}).get(latest) or data.get("urls") or []
        times = [f.get("upload_time_iso_8601") for f in files if f.get("upload_time_iso_8601")]
        released = max((_parse_dt(t) for t in times), default=None)
        return latest, released
    # Full metadata (not the abbreviated form): only it has real per-version
    # release dates in "time". The abbreviated "modified" date changes whenever
    # ANY metadata changes, so it would make old packages look maintained.
    r = session.get(f"https://registry.npmjs.org/{dep.name.replace('/', '%2F')}",
                    timeout=REGISTRY_TIMEOUT)
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    data = r.json()
    tagged = (data.get("dist-tags") or {}).get("latest")
    # The 'latest' tag can lag behind (e.g. a 1.x patch published after 2.0.0
    # moves the tag back). Use the highest stable version instead.
    stable = [v for v in (data.get("versions") or {}) if re.fullmatch(r"\d+\.\d+\.\d+", v)]
    latest = max(stable + ([tagged] if tagged else []), key=lambda v: _vtuple(v) or (0,), default=tagged)
    times = data.get("time") if isinstance(data.get("time"), dict) else {}
    released = _parse_dt(times.get(latest)) if times.get(latest) else None
    return latest, released


def _parse_dt(text):
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def check_health(deps, now=None, session=None, fetch=None):
    """Fill in health fields for each dependency. Returns number of lookups skipped.

    `fetch` can be replaced in tests; by default it queries PyPI / npm.
    """
    now = now or datetime.now(timezone.utc)
    session = session or requests.Session()
    fetch = fetch or (lambda d: _fetch_latest(d, session))

    for d in deps:
        analyse_spec(d)

    unique = {}
    for d in deps:
        if d.note != "non-registry source":
            unique.setdefault((d.ecosystem, d.name.lower()), d)
    keys = list(unique)[:MAX_REGISTRY_LOOKUPS]
    skipped = max(0, len(unique) - MAX_REGISTRY_LOOKUPS)

    results = {}

    def lookup(key):
        try:
            return key, fetch(unique[key]), None
        except Exception as e:  # registry down, bad JSON, timeout...
            return key, (None, None), e.__class__.__name__

    with ThreadPoolExecutor(max_workers=8) as pool:
        for key, value, err in pool.map(lookup, keys):
            results[key] = (value, err)

    for d in deps:
        key = (d.ecosystem, d.name.lower())
        if d.note == "non-registry source":
            d.status = "not checked"
            continue
        if key not in results:
            d.status, d.note = "not checked", f"over the {MAX_REGISTRY_LOOKUPS}-lookup limit"
            continue
        (latest, released), err = results[key]
        if err:
            d.status, d.note = "not checked", f"registry error ({err})"
            continue
        if latest is None:
            d.status, d.note = "not checked", "not found on registry"
            continue
        d.latest, d.latest_release = latest, released
        if not d.pinned:
            d.issues.append("unpinned")
        if d.pinned:
            cur, lat = major(d.current_version), major(latest)
            if cur is not None and lat is not None and cur < lat:
                d.issues.append("outdated")  # pinned to an older major version
        elif not spec_allows(d.spec, latest, d.ecosystem):
            d.issues.append("outdated")      # range cannot install the latest release
        if released and (now - released).days > UNMAINTAINED_DAYS:
            d.issues.append("unmaintained")
        if released is None:
            d.note = "release date unknown"
        d.status = "warning" if d.issues else "healthy"
    return skipped


def summarize(deps):
    """Counts used by the report and the complexity score."""
    s = {"total": len(deps), "healthy": 0, "warning": 0, "not checked": 0,
         "unpinned": 0, "outdated": 0, "unmaintained": 0}
    for d in deps:
        s[d.status] += 1
        for issue in d.issues:
            s[issue] += 1
    s["checked"] = s["healthy"] + s["warning"]
    s["health_pct"] = round(100 * s["healthy"] / s["checked"]) if s["checked"] else None
    return s
