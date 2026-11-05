"""Stage 7: commit activity per week + top contributors, drawn in the terminal.

- Commits: default branch, last 26 weeks (about 6 months), up to 1,000 commits.
- Weeks are counted backwards from today, so the last bar is the current week.
- The chart is plain Unicode block characters: no plotting library needed,
  and it works in any terminal that shows UTF-8.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone

WEEKS = 26
BLOCKS = " ▁▂▃▄▅▆▇█"
BOT_MARKERS = ("[bot]", "-bot", "dependabot", "renovate", "github-actions")


def _commit_date(c):
    info = c.get("commit", {})
    text = (info.get("author") or {}).get("date") or (info.get("committer") or {}).get("date")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _author(c):
    if c.get("author") and c["author"].get("login"):
        return c["author"]["login"]
    return ((c.get("commit") or {}).get("author") or {}).get("name") or "unknown"


def weekly_counts(commits, now=None):
    now = now or datetime.now(timezone.utc)
    counts = [0] * WEEKS
    for c in commits:
        d = _commit_date(c)
        if not d:
            continue
        age_weeks = (now - d).days // 7
        if 0 <= age_weeks < WEEKS:
            counts[WEEKS - 1 - age_weeks] += 1
    starts = [(now - timedelta(weeks=WEEKS - 1 - i)).date() for i in range(WEEKS)]
    return counts, starts


def bar_chart(counts, starts, height=8):
    """Return chart lines: vertical bars, y-axis labels, and month labels."""
    peak = max(counts) if counts else 0
    if peak == 0:
        return ["(no commits in this period)"]
    lines = []
    for level in range(height, 0, -1):
        row = []
        for c in counts:
            filled = c / peak * height
            if filled >= level:
                row.append("█")
            elif filled > level - 1:
                row.append(BLOCKS[max(1, int((filled - (level - 1)) * 8))])
            else:
                row.append(" ")
        label = f"{peak:>4} ┤" if level == height else "     │"  # bar height is relative to the peak
        lines.append(label + " ".join(row))
    lines.append("     └" + "──" * len(counts))
    # Month name (3 letters) under the first bar of each month; skip if it would overlap.
    label = [" "] * (2 * len(counts) + 2)
    last_month, next_free = None, 0
    for i, d in enumerate(starts):
        name = d.strftime("%b")
        if name != last_month and 2 * i >= next_free:
            label[2 * i:2 * i + 3] = list(name)
            next_free = 2 * i + 4
        last_month = name
    lines.append("      " + "".join(label).rstrip())
    return lines


def summarize(commits, contributors, capped, now=None):
    counts, starts = weekly_counts(commits, now)
    recent_authors = Counter(_author(c) for c in commits)
    top_recent = [
        {"name": a, "commits": n, "bot": any(b in a.lower() for b in BOT_MARKERS)}
        for a, n in recent_authors.most_common(5)
    ]
    top_all_time = [
        {"name": c.get("login") or c.get("name") or "anonymous", "commits": c.get("contributions", 0),
         "bot": (c.get("type") == "Bot") or any(b in (c.get("login") or "").lower() for b in BOT_MARKERS)}
        for c in contributors
    ]
    active_weeks = sum(1 for c in counts if c)
    return {
        "weekly": counts,
        "week_starts": starts,
        "chart": bar_chart(counts, starts),
        "total": sum(counts),
        "capped": capped,
        "active_weeks": active_weeks,
        "busiest_week": (starts[counts.index(max(counts))], max(counts)) if max(counts) else None,
        "top_recent": top_recent,
        "top_all_time": top_all_time,
    }
