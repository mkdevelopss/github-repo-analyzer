"""GitHub Repository Analyzer CLI.

Usage:
    python analyze.py <github-repo-url> [--save report.txt]

Flow (each step is one module in analyzer/):
    1. url_parser    validate the URL -> owner/repo
    2. github_client fetch repo info, languages, file tree, manifests, commits, contributors
    3. techstack     languages + frameworks + tools, each with evidence
    4. structure     folder/file summary
    5. complexity    0-100 score with per-signal breakdown
    6. dependencies  parse manifests, check PyPI/npm, judge health
    7. activity      weekly commit chart + top contributors
    8. report        print everything (and optionally save it)

Every expected failure is an AnalyzerError: we print its message and exit
with that error's code, so the user never sees a raw traceback.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

from rich.console import Console

from analyzer import activity, complexity, dependencies, report, structure, techstack
from analyzer.errors import AnalyzerError
from analyzer.github_client import GitHubClient
from analyzer.url_parser import parse_repo_url


def build_parser():
    p = argparse.ArgumentParser(prog="analyze",
                                description="Analyse a public GitHub repository and print a report.")
    p.add_argument("url", help="Repository URL, e.g. https://github.com/psf/requests")
    p.add_argument("--save", metavar="FILE", help="also save the report as plain text to FILE")
    return p


def collect(ref, client, status):
    """Run every analysis stage and return the data the report needs."""
    status.update("Fetching repository info...")
    repo = client.repo(ref.owner, ref.repo)
    branch = repo.get("default_branch") or "main"

    status.update("Fetching languages and file tree...")
    langs = client.languages(ref.owner, ref.repo)
    tree, truncated = client.tree(ref.owner, ref.repo, branch)
    st = structure.summarize(tree, truncated)

    status.update("Reading dependency manifests...")
    manifests, more = dependencies.find_manifests(st["paths"])
    deps, unreadable = [], []
    for path, _eco in manifests:
        text = client.file_text(ref.owner, ref.repo, path, branch)
        if text is None:
            unreadable.append((path, "missing, binary or larger than 1 MB"))
            continue
        parsed = dependencies.parse_manifest(path, text)
        if parsed is None:
            unreadable.append((path, "invalid file format"))
            continue
        deps.extend(parsed)

    status.update(f"Checking {len(deps)} dependencies on PyPI / npm...")
    dependencies.check_health(deps)

    tech = techstack.detect(langs, st["paths"], deps)
    unique_deps = len({(d.ecosystem, d.name.lower()) for d in deps})
    cx = complexity.score(st, tech["languages"], unique_deps)

    status.update("Fetching commit history and contributors...")
    since = datetime.now(timezone.utc) - timedelta(weeks=activity.WEEKS)
    commits, capped = client.commits_since(ref.owner, ref.repo, since)
    contributors = client.contributors(ref.owner, ref.repo)
    act = activity.summarize(commits, contributors, capped)

    return {
        "ref": ref, "repo": repo, "tech": tech, "structure": st, "complexity": cx,
        "deps": {"manifests": manifests, "more_manifests": more, "unreadable": unreadable,
                 "deps": deps, "summary": dependencies.summarize(deps)},
        "activity": act, "generated": datetime.now(), "api_requests": client.requests_made,
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    console = Console(record=bool(args.save))
    try:
        ref = parse_repo_url(args.url)
        client = GitHubClient()
        with console.status(f"Analysing {ref.full_name}...") as status:
            data = collect(ref, client, status)
        report.render(console, data)
        if args.save:
            try:
                with open(args.save, "w", encoding="utf-8") as fh:
                    fh.write(console.export_text())
                console.print(f"[green]Report saved to {args.save}[/green]")
            except OSError as e:
                console.print(f"[bold red]Error:[/bold red] could not save report: {e.strerror}")
                return 6
        return 0
    except AnalyzerError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        return e.exit_code
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled by user.[/yellow]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
