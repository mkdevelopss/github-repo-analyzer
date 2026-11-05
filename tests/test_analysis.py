"""Stages 3-7 tests: tech stack, structure, complexity, dependencies, activity (no network)."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analyzer import activity, complexity, dependencies, structure, techstack

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


def blob(path, size=1000):
    return {"path": path, "type": "blob", "size": size}


TREE = [
    {"path": "src", "type": "tree"}, {"path": "src/app", "type": "tree"},
    blob("src/app/main.py", 5000), blob("src/app/util.py", 200_000),
    {"path": "tests", "type": "tree"}, blob("tests/test_main.py"),
    {"path": "weirdname", "type": "tree"}, blob("weirdname/x.txt"),
    blob("requirements.txt"), blob("Dockerfile"), blob(".github/workflows/ci.yml"),
    blob("node_modules/pkg/package.json"),
]


class Structure(unittest.TestCase):
    def test_summary(self):
        st = structure.summarize(TREE)
        self.assertEqual(st["total_files"], 8)
        self.assertEqual(st["source_files"], 3)
        self.assertEqual(st["large_source_files"], 1)
        self.assertEqual(st["max_depth"], 2)  # src/app/main.py sits 2 folders deep
        folders = {f["name"]: f for f in st["folders"]}
        self.assertEqual(folders["src/"]["purpose"], "Main source code")
        self.assertFalse(folders["weirdname/"]["purpose_known"])
        self.assertIn("Not determined", folders["weirdname/"]["purpose"])

    def test_empty_repo(self):
        st = structure.summarize([])
        self.assertEqual((st["total_files"], st["max_depth"], st["folders"]), (0, 0, []))


class Dependencies(unittest.TestCase):
    def test_spec_allows_latest(self):
        allows = dependencies.spec_allows
        # (spec, latest, ecosystem, expected)
        cases = [
            (">=1.26,<3", "2.8.0", "pypi", True),     # the urllib3 case found in the real run
            (">=2,<4", "3.5.2", "pypi", True),
            ("<3", "3.0.1", "pypi", False),
            ("~=0.10.0", "0.10.4", "pypi", True),
            ("~=1.4", "2.0", "pypi", False),
            (">=1.5.6, !=1.5.7", "1.7.1", "pypi", True),
            ("==2.*", "3.1", "pypi", False),
            ("^4.17.0", "4.17.21", "npm", True),
            ("^4.17.0", "5.1.0", "npm", False),
            ("~1.2.0", "1.3.0", "npm", False),
            ("1.x || 2.x", "2.4.0", "npm", True),
            ("*", "9.0.0", "npm", True),
            ("", "9.0.0", "pypi", True),
            ("^13", "14.0.0", "pypi", False),          # poetry caret
        ]
        for spec, latest, eco, expected in cases:
            self.assertEqual(allows(spec, latest, eco), expected, msg=f"{spec} vs {latest}")

    def test_npm_uses_highest_stable_version_and_real_release_date(self):
        # Real-world case found while testing on expressjs/express ('accepts' package)
        class Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {"dist-tags": {"latest": "1.3.8"},
                        "versions": {"1.3.8": {}, "2.0.0": {}, "3.0.0-beta.1": {}},
                        "time": {"1.3.8": "2022-02-02T00:00:00Z", "2.0.0": "2024-08-31T00:00:00Z",
                                 "modified": "2025-05-14T00:00:00Z"}}

        class Session:
            def get(self, *a, **k): return Resp()

        dep = dependencies.Dependency("accepts", "^2.0.0", "npm", "package.json")
        latest, released = dependencies._fetch_latest(dep, Session())
        self.assertEqual(latest, "2.0.0")                      # not the lagging tag, not the beta
        self.assertEqual(released.date().isoformat(), "2024-08-31")  # not the 'modified' date

    def test_find_manifests_skips_node_modules(self):
        found, more = dependencies.find_manifests(structure.summarize(TREE)["paths"])
        self.assertEqual(found, [("requirements.txt", "pypi")])

    def test_parse_requirements(self):
        deps = dependencies.parse_requirements(
            "# comment\nrequests==2.31.0\nflask>=2.0  # web\nnumpy\n-r other.txt\n"
            "git+https://x/y.git\nuvicorn[standard]>=0.20; python_version>'3.8'\n", "requirements.txt")
        self.assertEqual([(d.name, d.spec) for d in deps],
                         [("requests", "==2.31.0"), ("flask", ">=2.0"), ("numpy", ""), ("uvicorn", ">=0.20")])

    def test_parse_pyproject_and_package_json(self):
        py = dependencies.parse_pyproject(
            '[project]\ndependencies=["httpx>=0.27"]\n[project.optional-dependencies]\ntest=["pytest==8.0.0"]\n'
            '[tool.poetry.dependencies]\npython="^3.10"\nrich={version="^13"}\n', "pyproject.toml")
        self.assertEqual([(d.name, d.spec, d.dev) for d in py],
                         [("httpx", ">=0.27", False), ("pytest", "==8.0.0", True), ("rich", "^13", False)])
        js = dependencies.parse_package_json(
            '{"dependencies":{"react":"18.2.0","lodash":"^4.17.0"},"devDependencies":{"jest":"~29.0.0"}}',
            "package.json")
        self.assertEqual([(d.name, d.dev) for d in js], [("react", False), ("lodash", False), ("jest", True)])

    def test_invalid_manifests_return_none(self):
        self.assertIsNone(dependencies.parse_package_json("{not json", "package.json"))
        self.assertIsNone(dependencies.parse_pyproject("[[[", "pyproject.toml"))

    def test_health_rules(self):
        D = dependencies.Dependency
        deps = [
            D("good", "==2.1.0", "pypi", "r.txt"),          # pinned, current, recent -> healthy
            D("loose", ">=2.0", "pypi", "r.txt"),            # unpinned
            D("old", "==1.0.0", "pypi", "r.txt"),            # outdated major
            D("dead", "==3.0.0", "pypi", "r.txt"),           # unmaintained
            D("ghost", "==1.0", "pypi", "r.txt"),            # not on registry
            D("broken", "==1.0", "pypi", "r.txt"),           # registry error
            D("gitdep", "github:me/x", "npm", "package.json"),  # non-registry
            D("react", "18.2.0", "npm", "package.json"),     # npm exact pin
            D("lodash", "^4.17.0", "npm", "package.json"),   # npm caret = unpinned
        ]
        recent, ancient = NOW - timedelta(days=30), NOW - timedelta(days=1000)
        registry = {"good": ("2.3.0", recent), "loose": ("2.5.0", recent), "old": ("2.0.0", recent),
                    "dead": ("3.0.0", ancient), "ghost": (None, None),
                    "react": ("18.3.1", recent), "lodash": ("4.17.21", recent)}

        def fake_fetch(d):
            if d.name == "broken":
                raise TimeoutError()
            return registry[d.name]

        dependencies.check_health(deps, now=NOW, fetch=fake_fetch)
        got = {d.name: (d.status, d.issues, d.note) for d in deps}
        self.assertEqual(got["good"], ("healthy", [], ""))
        self.assertEqual(got["loose"][1], ["unpinned"])
        self.assertEqual(got["old"][1], ["outdated"])
        self.assertEqual(got["dead"][1], ["unmaintained"])
        self.assertEqual(got["ghost"][:2], ("not checked", []))
        self.assertIn("registry error", got["broken"][2])
        self.assertEqual(got["gitdep"][0], "not checked")
        self.assertEqual(got["react"][0], "healthy")
        self.assertEqual(got["lodash"][1], ["unpinned"])
        s = dependencies.summarize(deps)
        self.assertEqual((s["healthy"], s["warning"], s["not checked"], s["health_pct"]), (2, 4, 3, 33))


class TechStack(unittest.TestCase):
    def test_every_item_has_evidence(self):
        st = structure.summarize(TREE)
        deps = dependencies.parse_requirements("flask==3.0\npytest==8.0\n", "requirements.txt")
        ts = techstack.detect({"Python": 9900, "Shell": 100, "Makefile": 10}, st["paths"], deps)
        self.assertEqual([l["name"] for l in ts["languages"]], ["Python", "Shell"])
        self.assertEqual([l["name"] for l in ts["other_languages"]], ["Makefile"])
        names = {x["name"] for x in ts["frameworks"] + ts["tools"]}
        self.assertTrue({"Flask", "pytest", "Docker", "GitHub Actions"} <= names)
        self.assertTrue(all(x["evidence"] for x in ts["frameworks"] + ts["tools"] + ts["languages"]))

    def test_nothing_invented_for_empty_input(self):
        ts = techstack.detect({}, [], [])
        self.assertEqual((ts["languages"], ts["frameworks"], ts["tools"]), ([], [], []))


class Complexity(unittest.TestCase):
    def test_bounds_and_bands(self):
        empty = structure.summarize([])
        self.assertEqual(complexity.score(empty, [], 0)["score"], 0)
        huge = {"source_files": 50_000, "source_bytes": 10**9, "max_depth": 30,
                "large_source_files": 50_000, "truncated": False}
        self.assertEqual(complexity.score(huge, [1] * 10, 500)["score"], 100)
        self.assertEqual([complexity.band(s) for s in (0, 24, 25, 50, 75, 100)],
                         ["Low", "Low", "Moderate", "High", "Very high", "Very high"])

    def test_points_add_up(self):
        cx = complexity.score(structure.summarize(TREE), [1, 2], 5)
        self.assertEqual(round(sum(r["points"] for r in cx["rows"])), cx["score"])


class Activity(unittest.TestCase):
    def commit(self, days_ago, login="alice"):
        d = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"author": {"login": login}, "commit": {"author": {"date": d, "name": login}}}

    def test_weekly_buckets(self):
        commits = [self.commit(0), self.commit(1), self.commit(8), self.commit(400)]
        counts, starts = activity.weekly_counts(commits, now=NOW)
        self.assertEqual((counts[-1], counts[-2], sum(counts)), (2, 1, 3))  # 400 days ago is excluded
        self.assertEqual(len(starts), activity.WEEKS)

    def test_summary_and_bots(self):
        commits = [self.commit(1), self.commit(2, "dependabot[bot]")]
        s = activity.summarize(commits, [{"login": "alice", "contributions": 50, "type": "User"}], False, now=NOW)
        self.assertEqual(s["total"], 2)
        self.assertTrue(any(x["bot"] for x in s["top_recent"]))
        self.assertEqual(s["top_all_time"][0]["commits"], 50)

    def test_no_commits_chart(self):
        self.assertEqual(activity.bar_chart([0] * 26, [NOW.date()] * 26), ["(no commits in this period)"])


if __name__ == "__main__":
    unittest.main()
