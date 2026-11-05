"""Stage 8: print the full report with rich (tables, colours, panels).

Every value shown comes from the analysis results. Missing values print as
"Not determined" with a reason, never as a guess.
"""

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

ND = "[dim]Not determined[/dim]"


def _v(value, fmt="{}"):
    return ND if value in (None, "") else fmt.format(value)


def _n(count, word):
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _kb(n):
    return f"{n / 1_000_000:.1f} MB" if n >= 1_000_000 else f"{n / 1000:.1f} KB"


def header(console, repo, ref):
    t = Table.grid(padding=(0, 2))
    t.add_column(style="bold cyan")
    t.add_column()
    lic = (repo.get("license") or {}).get("spdx_id") if repo.get("license") else None
    t.add_row("Repository", f"[bold]{ref.full_name}[/bold]  {ref.html_url}")
    t.add_row("Description", _v(repo.get("description")))
    t.add_row("Stars / Forks", f"{_v(repo.get('stargazers_count'), '{:,}')} / {_v(repo.get('forks_count'), '{:,}')}")
    t.add_row("License", _v(lic if lic and lic != "NOASSERTION" else None))
    t.add_row("Default branch", _v(repo.get("default_branch")))
    t.add_row("Created / Last push", f"{_v((repo.get('created_at') or '')[:10])} / {_v((repo.get('pushed_at') or '')[:10])}")
    if repo.get("archived"):
        t.add_row("Status", "[yellow]ARCHIVED (read-only, no longer maintained by its owners)[/yellow]")
    console.print(Panel(t, title="[bold]GitHub Repository Analyzer[/bold]", border_style="cyan"))


def tech_stack(console, ts):
    lang = Table(box=box.SIMPLE_HEAD, title="Languages", title_justify="left")
    lang.add_column("Language")
    lang.add_column("Share", justify="right")
    lang.add_column("", min_width=20)
    lang.add_column("Evidence", style="dim")
    for l in ts["languages"]:
        bar = "█" * max(1, round(l["percent"] / 5))
        lang.add_row(l["name"], f"{l['percent']:.1f}%", f"[green]{bar}[/green]", l["evidence"])
    if ts["other_languages"]:
        lang.add_row("[dim]other[/dim]", "<0.5% each", "", ", ".join(o["name"] for o in ts["other_languages"]))
    if not ts["languages"]:
        lang.add_row(ND, "", "", "GitHub reports no language data (empty or non-code repo)")

    items = Table(box=box.SIMPLE_HEAD, title="Frameworks, libraries & tools", title_justify="left")
    items.add_column("Name", style="bold")
    items.add_column("Category")
    items.add_column("Evidence", style="dim")
    for f in ts["frameworks"] + ts["tools"]:
        items.add_row(f["name"], f["category"], f["evidence"])
    if not (ts["frameworks"] or ts["tools"]):
        items.add_row(ND, "", "No known framework dependencies or tool config files found")
    console.print(Panel(Group(lang, items), title="1. Tech stack detected", border_style="blue"))


def file_structure(console, st):
    head = Text()
    head.append(f"{_n(st['total_files'], 'file')} in {_n(st['total_dirs'], 'folder')}, "
                f"{_kb(st['total_bytes'])} total; {_n(st['source_files'], 'source-code file')}; "
                f"max folder depth {st['max_depth']}\n")
    head.append("Most common file types: " + ", ".join(f"{e} ({c})" for e, c in st["extensions"][:8]))
    if st["truncated"]:
        head.append("\nNote: GitHub truncated the file tree (very large repo); counts are a lower bound.",
                    style="yellow")

    t = Table(box=box.SIMPLE_HEAD)
    t.add_column("Folder", style="bold")
    t.add_column("Files", justify="right")
    t.add_column("Size", justify="right")
    t.add_column("Main file types")
    t.add_column("Purpose")
    for f in st["folders"][:15]:
        purpose = f["purpose"] if f["purpose_known"] else f"[dim]{f['purpose']}[/dim]"
        t.add_row(f["name"], f"{f['files']:,}", _kb(f["bytes"]), f["top_types"], purpose)
    if len(st["folders"]) > 15:
        t.add_row(f"[dim]+{len(st['folders']) - 15} more folders[/dim]", "", "", "", "")
    root = st["root_files"]
    t.add_row("(root files)", f"{len(root)}", "", ", ".join(root[:6]) + (" ..." if len(root) > 6 else ""),
              "Project-level config & docs")
    console.print(Panel(Group(head, t), title="2. File structure summary", border_style="blue"))


def complexity(console, cx):
    color = {"Low": "green", "Moderate": "yellow", "High": "dark_orange", "Very high": "red"}[cx["band"]]
    filled = round(cx["score"] / 5)
    head = Text.from_markup(
        f"Score: [bold {color}]{cx['score']}/100 ({cx['band']})[/bold {color}]  "
        f"[{color}]{'█' * filled}[/{color}][dim]{'░' * (20 - filled)}[/dim]\n"
        f"Main reasons: {'; '.join(cx['main_reasons']) or 'small, simple repository'}"
    )
    t = Table(box=box.SIMPLE_HEAD)
    t.add_column("Signal")
    t.add_column("Measured")
    t.add_column("Points", justify="right")
    for r in cx["rows"]:
        t.add_row(r["signal"], r["detail"], f"{r['points']:.1f} / {r['max']}")
    note = Text("How it is calculated: each signal is scaled to 0-1 and capped (source files and code "
                "volume on a log scale), then multiplied by its weight. Bands: 0-24 Low, 25-49 Moderate, "
                "50-74 High, 75-100 Very high. This measures structural size and spread, not per-function "
                "(cyclomatic) complexity.", style="dim")
    if cx["partial"]:
        note.append("\nBased on a truncated file tree, so the true score may be higher.", style="yellow")
    console.print(Panel(Group(head, t, note), title="3. Code complexity score", border_style="blue"))


STATUS_STYLE = {"healthy": "[green]healthy[/green]", "warning": "[yellow]warning[/yellow]",
                "not checked": "[dim]not checked[/dim]"}


def dependency_health(console, dep):
    parts = []
    if dep["manifests"]:
        parts.append(Text("Manifests read: " + ", ".join(m for m, _ in dep["manifests"])))
    for path, reason in dep["unreadable"]:
        parts.append(Text(f"Could not read {path}: {reason}", style="yellow"))
    if dep["more_manifests"]:
        parts.append(Text(f"{dep['more_manifests']} more manifest file(s) not read (limit of 6).", style="dim"))

    if not dep["deps"]:
        msg = ("No supported dependency manifest found (requirements*.txt, pyproject.toml, package.json). "
               "Dependency health: Not determined." if not dep["manifests"]
               else "Manifest files found, but they declare no dependencies.")
        parts.append(Text(msg, style="yellow"))
        console.print(Panel(Group(*parts), title="4. Dependency health", border_style="blue"))
        return

    s = dep["summary"]
    pct = f"{s['health_pct']}% healthy" if s["health_pct"] is not None else "health not determined"
    parts.append(Text.from_markup(
        f"[bold]{s['total']}[/bold] dependencies: [green]{s['healthy']} healthy[/green], "
        f"[yellow]{s['warning']} with warnings[/yellow], [dim]{s['not checked']} not checked[/dim] "
        f"-> [bold]{pct}[/bold] of those checked\n"
        f"Issues: {s['unpinned']} unpinned, {s['outdated']} outdated (behind a major version), "
        f"{s['unmaintained']} unmaintained (no release in 2+ years)"))

    t = Table(box=box.SIMPLE_HEAD)
    for col in ("Package", "Spec", "Latest", "Last release", "Status", "Issues / note"):
        t.add_column(col)
    order = {"warning": 0, "healthy": 1, "not checked": 2}
    rows = sorted(dep["deps"], key=lambda d: (order[d.status], d.dev, d.name.lower()))
    for d in rows[:30]:
        name = d.name + (" [dim](dev)[/dim]" if d.dev else "")
        released = d.latest_release.strftime("%Y-%m-%d") if d.latest_release else "-"
        t.add_row(name, d.spec or "[dim](any)[/dim]", d.latest or "-", released,
                  STATUS_STYLE[d.status], ", ".join(d.issues) or d.note)
    if len(rows) > 30:
        t.add_row(f"[dim]+{len(rows) - 30} more[/dim]", "", "", "", "", "")
    parts.append(t)
    parts.append(Text("Healthy = pinned to an exact version AND on the latest major version AND "
                      "released within the last 2 years. Latest versions come from PyPI / npm.", style="dim"))
    console.print(Panel(Group(*parts), title="4. Dependency health", border_style="blue"))


def activity(console, act, last_push=None):
    parts = []
    total = f"{act['total']:,}" + ("+ (stopped at 1,000)" if act["capped"] else "")
    parts.append(Text(f"Commits per week, last 26 weeks (default branch): {total} commits, "
                      f"active in {act['active_weeks']}/26 weeks"))
    parts.append(Text("\n".join(act["chart"]), style="green"))
    if act["total"] == 0:
        when = (last_push or "")[:10] or "Not determined"
        parts.append(Text(f"No commits in the last 26 weeks. Last push to the repository: {when}.",
                          style="yellow"))
    if act["busiest_week"]:
        d, n = act["busiest_week"]
        parts.append(Text(f"Busiest week: starting {d:%Y-%m-%d} with {n} commits", style="dim"))

    t = Table(box=box.SIMPLE_HEAD)
    t.add_column("Top contributors (all time)")
    t.add_column("Commits", justify="right")
    t.add_column("Most active (last 26 weeks)")
    t.add_column("Commits", justify="right")
    a, r = act["top_all_time"], act["top_recent"]
    for i in range(max(len(a), len(r))):
        def cell(lst):
            if i >= len(lst):
                return "", ""
            x = lst[i]
            bar = "▇" * max(1, round(10 * x["commits"] / lst[0]["commits"])) if lst[0]["commits"] else ""
            return x["name"] + (" [dim](bot)[/dim]" if x["bot"] else ""), f"{x['commits']:,} [cyan]{bar}[/cyan]"
        t.add_row(*cell(a), *cell(r))
    if not a and not r:
        t.add_row(ND, "", "No contributor data (empty repository)", "")
    parts.append(t)
    console.print(Panel(Group(*parts), title="5. Contribution activity", border_style="blue"))


def render(console: Console, data):
    header(console, data["repo"], data["ref"])
    tech_stack(console, data["tech"])
    file_structure(console, data["structure"])
    complexity(console, data["complexity"])
    dependency_health(console, data["deps"])
    activity(console, data["activity"], data["repo"].get("pushed_at"))
    console.print(f"[dim]Generated {data['generated']:%Y-%m-%d %H:%M} | GitHub API requests used: "
                  f"{data['api_requests']}[/dim]")
