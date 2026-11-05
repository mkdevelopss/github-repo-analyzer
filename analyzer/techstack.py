"""Stage 3: detect languages, frameworks and tools - each with its evidence.

Three evidence sources, from strongest to weakest:
  1. GitHub's language byte counts (Linguist)          -> languages
  2. Dependencies declared in manifest files            -> frameworks / libraries
  3. Well-known configuration files in the file tree    -> tools
Nothing is reported without one of these pieces of evidence.
"""

import re

MIN_LANGUAGE_SHARE = 0.5  # % - smaller shares are listed as 'other'

# dependency name (lower-case) -> (display name, category)
FRAMEWORK_DEPS = {
    # Python
    "django": ("Django", "Web framework"), "flask": ("Flask", "Web framework"),
    "fastapi": ("FastAPI", "Web framework"), "tornado": ("Tornado", "Web framework"),
    "starlette": ("Starlette", "Web framework"), "streamlit": ("Streamlit", "App framework"),
    "numpy": ("NumPy", "Data / ML"), "pandas": ("pandas", "Data / ML"),
    "scikit-learn": ("scikit-learn", "Data / ML"), "torch": ("PyTorch", "Data / ML"),
    "tensorflow": ("TensorFlow", "Data / ML"), "transformers": ("Hugging Face Transformers", "Data / ML"),
    "sqlalchemy": ("SQLAlchemy", "Database"), "celery": ("Celery", "Task queue"),
    "pytest": ("pytest", "Testing"), "requests": ("requests", "HTTP client"),
    "pydantic": ("Pydantic", "Data validation"), "click": ("Click", "CLI"),
    "sphinx": ("Sphinx", "Documentation"), "mkdocs": ("MkDocs", "Documentation"),
    # JavaScript / TypeScript
    "react": ("React", "UI framework"), "vue": ("Vue", "UI framework"),
    "@angular/core": ("Angular", "UI framework"), "svelte": ("Svelte", "UI framework"),
    "next": ("Next.js", "Web framework"), "nuxt": ("Nuxt", "Web framework"),
    "express": ("Express", "Web framework"), "koa": ("Koa", "Web framework"),
    "@nestjs/core": ("NestJS", "Web framework"), "fastify": ("Fastify", "Web framework"),
    "typescript": ("TypeScript compiler", "Language tooling"), "jest": ("Jest", "Testing"),
    "mocha": ("Mocha", "Testing"), "vitest": ("Vitest", "Testing"),
    "webpack": ("webpack", "Build tool"), "vite": ("Vite", "Build tool"),
    "eslint": ("ESLint", "Linting"), "prettier": ("Prettier", "Formatting"),
    "tailwindcss": ("Tailwind CSS", "Styling"), "mongoose": ("Mongoose", "Database"),
    "prisma": ("Prisma", "Database"), "electron": ("Electron", "Desktop framework"),
}

# regex on file path -> (tool, category)
TOOL_FILES = [
    (r"(^|/)Dockerfile[^/]*$", ("Docker", "Containers")),
    (r"(^|/)(docker-)?compose[^/]*\.ya?ml$", ("Docker Compose", "Containers")),
    (r"^\.github/workflows/[^/]+\.ya?ml$", ("GitHub Actions", "CI/CD")),
    (r"^\.gitlab-ci\.yml$", ("GitLab CI", "CI/CD")),
    (r"^\.travis\.yml$", ("Travis CI", "CI/CD")),
    (r"^\.circleci/", ("CircleCI", "CI/CD")),
    (r"^Jenkinsfile$", ("Jenkins", "CI/CD")),
    (r"^azure-pipelines\.ya?ml$", ("Azure Pipelines", "CI/CD")),
    (r"(^|/)Makefile$", ("Make", "Build tool")),
    (r"^tox\.ini$", ("tox", "Testing")),
    (r"^(pytest\.ini|conftest\.py)$", ("pytest config", "Testing")),
    (r"^setup\.py$", ("setuptools", "Packaging")),
    (r"^pyproject\.toml$", ("pyproject.toml packaging", "Packaging")),
    (r"^poetry\.lock$", ("Poetry", "Packaging")),
    (r"^Pipfile$", ("Pipenv", "Packaging")),
    (r"^package-lock\.json$", ("npm", "Package manager")),
    (r"^yarn\.lock$", ("Yarn", "Package manager")),
    (r"^pnpm-lock\.yaml$", ("pnpm", "Package manager")),
    (r"^tsconfig[^/]*\.json$", ("TypeScript config", "Language tooling")),
    (r"^\.eslintrc[^/]*$|^eslint\.config\.[cm]?js$", ("ESLint config", "Linting")),
    (r"^\.prettierrc[^/]*$", ("Prettier config", "Formatting")),
    (r"^\.pre-commit-config\.yaml$", ("pre-commit", "Code quality")),
    (r"^go\.mod$", ("Go modules", "Package manager")),
    (r"^Cargo\.toml$", ("Cargo", "Package manager")),
    (r"^pom\.xml$", ("Maven", "Build tool")),
    (r"^build\.gradle(\.kts)?$", ("Gradle", "Build tool")),
    (r"^Gemfile$", ("Bundler", "Package manager")),
    (r"^composer\.json$", ("Composer", "Package manager")),
    (r"^\.readthedocs\.ya?ml$", ("Read the Docs", "Documentation")),
    (r"^mkdocs\.ya?ml$", ("MkDocs config", "Documentation")),
    (r"(^|/)terraform/|\.tf$", ("Terraform", "Infrastructure")),
    (r"^vercel\.json$", ("Vercel", "Hosting")),
    (r"^netlify\.toml$", ("Netlify", "Hosting")),
]
_TOOL_RES = [(re.compile(p), v) for p, v in TOOL_FILES]


def detect(languages, tree_paths, dependencies):
    """Return dict with 'languages', 'frameworks', 'tools' lists; every item has evidence."""
    total = sum(languages.values()) or 0
    langs, other = [], []
    for name, size in sorted(languages.items(), key=lambda kv: -kv[1]):
        pct = 100 * size / total if total else 0
        item = {"name": name, "percent": round(pct, 1), "evidence": f"{size:,} bytes (GitHub Linguist)"}
        (langs if pct >= MIN_LANGUAGE_SHARE else other).append(item)

    frameworks = {}
    for d in dependencies:
        key = d.name.lower()
        if key in FRAMEWORK_DEPS:
            name, cat = FRAMEWORK_DEPS[key]
            kind = "devDependency" if d.dev and d.ecosystem == "npm" else "dependency"
            frameworks.setdefault(name, {"name": name, "category": cat,
                                         "evidence": f"{kind} '{d.name}' in {d.manifest}"})

    tools = {}
    for path in tree_paths:
        for rx, (name, cat) in _TOOL_RES:
            if rx.search(path) and name not in tools:
                tools[name] = {"name": name, "category": cat, "evidence": f"file {path}"}
    workflows = sum(1 for p in tree_paths if _TOOL_RES[2][0].search(p))
    if "GitHub Actions" in tools and workflows > 1:
        tools["GitHub Actions"]["evidence"] = f"{workflows} workflow files in .github/workflows/"

    return {
        "languages": langs,
        "other_languages": other,
        "frameworks": sorted(frameworks.values(), key=lambda x: (x["category"], x["name"])),
        "tools": sorted(tools.values(), key=lambda x: (x["category"], x["name"])),
    }
