"""Stage 4: summarise the repository layout from the file tree.

Input: the recursive tree from ONE API call (paths, types, sizes).
Output: totals, extension counts, and one row per top-level folder with its
file count, size, main file types and purpose.

Purpose is taken from a list of widely used folder-name conventions. If a
folder name is not in that list we print "Not determined" rather than guess.
"""

import posixpath
from collections import Counter, defaultdict

SOURCE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt", ".kts",
    ".go", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php",
    ".swift", ".m", ".scala", ".dart", ".lua", ".r", ".jl", ".vue", ".svelte",
    ".sh", ".bash", ".ps1", ".pl", ".ex", ".exs", ".erl", ".hs", ".clj", ".sql",
}

FOLDER_PURPOSE = {
    "src": "Main source code", "lib": "Library source code", "app": "Application code",
    "apps": "Multiple applications (monorepo)", "packages": "Packages (monorepo)",
    "pkg": "Go packages", "cmd": "Command-line entry points", "internal": "Internal (private) packages",
    "api": "API definitions / handlers", "server": "Server-side code", "client": "Client-side code",
    "components": "UI components", "pages": "Page routes / views", "public": "Static public assets",
    "static": "Static assets", "assets": "Images, fonts and other assets", "styles": "Stylesheets",
    "test": "Tests", "tests": "Tests", "__tests__": "Tests", "spec": "Tests (specs)",
    "e2e": "End-to-end tests", "benchmarks": "Performance benchmarks", "bench": "Performance benchmarks",
    "docs": "Documentation", "doc": "Documentation", "examples": "Example usage",
    "example": "Example usage", "samples": "Example usage", "scripts": "Helper scripts",
    "tools": "Developer tools", "bin": "Executables / launch scripts", "config": "Configuration",
    "configs": "Configuration", "conf": "Configuration", ".github": "GitHub settings (CI workflows, templates)",
    ".circleci": "CircleCI configuration", "deploy": "Deployment files", "deployment": "Deployment files",
    "docker": "Docker files", "k8s": "Kubernetes manifests", "migrations": "Database migrations",
    "data": "Data files", "notebooks": "Jupyter notebooks", "vendor": "Bundled third-party code",
    "third_party": "Bundled third-party code", "build": "Build output / build scripts",
    "dist": "Build output", "ext": "Extensions", "include": "Header files", "fixtures": "Test fixtures",
    "templates": "Templates", "locales": "Translations", "i18n": "Translations",
    "plugins": "Plugins", "types": "Type definitions", "utils": "Utility code", "hooks": "Hooks",
}


def ext_of(path):
    base = posixpath.basename(path)
    if base.startswith(".") and base.count(".") == 1:
        return base  # dotfile such as .gitignore
    _, ext = posixpath.splitext(base)
    return ext.lower() or "(no extension)"


def summarize(tree, truncated=False):
    files = [e for e in tree if e.get("type") == "blob"]
    dirs = [e for e in tree if e.get("type") == "tree"]

    ext_counts = Counter(ext_of(f["path"]) for f in files)
    source_files = [f for f in files if ext_of(f["path"]) in SOURCE_EXTENSIONS]
    # depth = number of folders a file is inside (README.md -> 0, src/app/main.py -> 2)
    max_depth = max((f["path"].count("/") for f in files), default=0)

    groups = defaultdict(list)
    root_files = []
    for f in files:
        if "/" in f["path"]:
            groups[f["path"].split("/", 1)[0]].append(f)
        else:
            root_files.append(f)

    folders = []
    for name, items in groups.items():
        exts = Counter(ext_of(f["path"]) for f in items)
        purpose = FOLDER_PURPOSE.get(name.lower())
        folders.append({
            "name": name + "/",
            "files": len(items),
            "bytes": sum(f.get("size", 0) or 0 for f in items),
            "top_types": ", ".join(f"{e} ({c})" for e, c in exts.most_common(3)),
            "purpose": purpose or "Not determined (no standard folder-name convention)",
            "purpose_known": purpose is not None,
        })
    folders.sort(key=lambda r: -r["files"])

    return {
        "total_files": len(files),
        "total_dirs": len(dirs),
        "total_bytes": sum(f.get("size", 0) or 0 for f in files),
        "source_files": len(source_files),
        "source_bytes": sum(f.get("size", 0) or 0 for f in source_files),
        "large_source_files": sum(1 for f in source_files if (f.get("size") or 0) > 100_000),
        "max_depth": max_depth,
        "extensions": ext_counts.most_common(10),
        "root_files": sorted(f["path"] for f in root_files),
        "folders": folders,
        "truncated": truncated,
        "paths": [f["path"] for f in files],
    }
