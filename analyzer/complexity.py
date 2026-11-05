"""Stage 5: one Code Complexity Score from 0 (simple) to 100 (very complex).

This is a STRUCTURAL complexity score: how big and spread out the codebase is
and how many moving parts it has. It does not read every function (that would
need downloading the whole repo), so it does not measure cyclomatic complexity.

Six signals, each scaled to 0..1 and capped, then weighted:

  Signal                 Weight  Reaches its max (1.0) at        Why it matters
  Source files             25    1,000 files (log scale)         more files = more to understand
  Code volume              20    10 MB of source (log scale)     more code = more to maintain
  Folder depth             15    10 levels deep                  deep nesting = harder navigation
  Languages                15    6+ languages (>=0.5% share)     each language = another toolchain
  Dependencies             15    100 declared packages           more external parts that can break
  Large source files       10    10% of source files > 100 KB    huge files are hard to review

Log scales are used for size because going from 10 to 100 files changes a
project far more than going from 1,000 to 1,090.

Score = sum(weight x signal).  Bands: 0-24 Low, 25-49 Moderate, 50-74 High, 75-100 Very high.
"""

import math

WEIGHTS = {
    "Source files": 25, "Code volume": 20, "Folder depth": 15,
    "Languages": 15, "Dependencies": 15, "Large source files": 10,
}


def _log_scale(value, cap):
    if value <= 0:
        return 0.0
    return min(math.log10(value + 1) / math.log10(cap + 1), 1.0)


def band(score):
    if score < 25:
        return "Low"
    if score < 50:
        return "Moderate"
    if score < 75:
        return "High"
    return "Very high"


def score(structure, languages_detected, dependency_count):
    n_src = structure["source_files"]
    src_kb = structure["source_bytes"] / 1000
    n_langs = len(languages_detected)
    large_share = structure["large_source_files"] / n_src if n_src else 0

    signals = [
        ("Source files", _log_scale(n_src, 1000), f"{n_src:,} source files"),
        ("Code volume", _log_scale(src_kb, 10_000), f"{src_kb:,.0f} KB of source code"),
        ("Folder depth", min(structure["max_depth"] / 10, 1.0), f"max depth {structure['max_depth']} levels"),
        ("Languages", min(max(n_langs - 1, 0) / 5, 1.0), f"{n_langs} language(s) with >=0.5% share"),
        ("Dependencies", min(dependency_count / 100, 1.0), f"{dependency_count} declared dependencies"),
        ("Large source files", min(large_share / 0.10, 1.0),
         f"{structure['large_source_files']} source file(s) over 100 KB"),
    ]
    rows = []
    total = 0.0
    for name, value, detail in signals:
        points = WEIGHTS[name] * value
        total += points
        rows.append({"signal": name, "detail": detail, "points": round(points, 1), "max": WEIGHTS[name]})
    final = round(total)
    top = sorted(rows, key=lambda r: -r["points"] / r["max"])[:2]
    return {
        "score": final,
        "band": band(final),
        "rows": rows,
        "main_reasons": [f"{r['signal']}: {r['detail']}" for r in top if r["points"] > 0],
        "partial": structure["truncated"],
    }
