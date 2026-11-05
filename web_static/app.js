/* Repo Analyzer web page. All numbers come from /api/analyze, which runs the same code as the CLI. */
(function () {
  const $ = (s, el = document) => el.querySelector(s);
  const { esc, fmt, store, toast, download, copy } = UI;
  const view = $("#view-report"), cmpView = $("#view-compare");
  const LANG_COLORS = ["#5E8B7E", "#C7A17A", "#22333B", "#8FB3A7", "#A07F5B", "#6D7F86", "#B8CBC4"];
  const EXAMPLES = ["psf/requests", "expressjs/express", "pallets/flask", "octocat/Hello-World"];
  let current = null, depFilter = "all", depQuery = "", folderSort = "files";

  /* ---------- data ---------- */
  async function analyze(url, refresh) {
    const r = await fetch(`api/analyze?url=${encodeURIComponent(url)}${refresh ? "&refresh=1" : ""}`);
    const body = await r.json().catch(() => ({ error: `Server returned HTTP ${r.status}` }));
    if (!r.ok) throw Object.assign(new Error(body.error || `HTTP ${r.status}`), { kind: body.kind });
    return body;
  }
  async function refreshRate() {
    try {
      const r = await (await fetch("api/rate")).json();
      const el = $("#rate");
      if (r.unknown) { el.textContent = ""; return; }
      el.textContent = `API ${fmt(r.remaining)}/${fmt(r.limit)}`;
      el.classList.toggle("low", r.remaining < 20);
      el.title = r.authenticated ? "GitHub API calls left this hour (token set)" : "GitHub API calls left this hour. Set GITHUB_TOKEN for 5,000.";
    } catch (e) { /* rate display is optional */ }
  }
  function remember(name) {
    const h = store.get("history", []).filter((x) => x !== name);
    h.unshift(name); store.set("history", h.slice(0, 8));
  }

  /* ---------- routing ---------- */
  function route() {
    const isCmp = location.hash === "#compare";
    view.hidden = isCmp; cmpView.hidden = !isCmp;
    $("#nav-report").toggleAttribute("aria-current", !isCmp);
    $("#nav-compare").toggleAttribute("aria-current", isCmp);
    if (isCmp) renderCompare(); else if (!current) {
      const q = new URLSearchParams(location.search).get("repo");
      if (q) run(q); else renderHome();
    }
  }
  window.addEventListener("hashchange", route);

  $("#form").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("#url").value.trim();
    if (!v) { $("#url").focus(); return; }
    if (location.hash === "#compare") location.hash = "#report";
    run(v);
  });

  async function run(url, refresh) {
    $("#url").value = url;
    renderLoading(url);
    try {
      const data = await analyze(url, refresh);
      current = data;
      remember(data.repo.full_name);
      history.replaceState(null, "", `?repo=${encodeURIComponent(data.repo.full_name)}${location.hash}`);
      renderReport(data);
    } catch (err) { renderError(url, err); }
    refreshRate();
  }

  /* ---------- home / loading / error ---------- */
  function renderHome() {
    const hist = store.get("history", []);
    view.innerHTML = `
      <div class="hero">
        <span class="eyebrow">Track B · GitHub Repository Analyzer</span>
        <h1>Understand any public repository in seconds</h1>
        <p>Paste a GitHub link to see its tech stack, folder layout, a complexity score, dependency health and recent activity. Every value shows its evidence; anything that can't be measured says so.</p>
        <div class="chips" aria-label="Examples">${EXAMPLES.map((x) => `<button class="chip" data-run="${esc(x)}">${esc(x)}</button>`).join("")}</div>
      </div>
      ${hist.length ? `<div class="card"><div class="card-head"><h2>Recent</h2><button class="btn btn-ghost btn-sm" data-clear-history>Clear</button></div>
        <div class="chips">${hist.map((x) => `<button class="chip" data-run="${esc(x)}">${esc(x)}</button>`).join("")}</div></div>` : ""}
      <div class="feature-grid">
        ${[["Tech stack", "Languages by bytes, plus frameworks and tools found in manifests and config files."],
           ["Complexity score", "0–100 from six measured signals, with the points each one adds."],
           ["Dependency health", "Pinned, current and maintained, checked live against PyPI and npm."],
           ["Compare", "Put two repositories side by side on the same measures."]]
          .map(([t, d]) => `<div class="card"><h3>${t}</h3><p class="muted small">${d}</p></div>`).join("")}
      </div>`;
  }
  function renderLoading(url) {
    view.innerHTML = `
      <div class="card loading" aria-live="polite">
        <ol class="steps"><li class="active"><span class="ring"></span>Analysing ${esc(url)}</li></ol>
        <p class="muted small">Fetching repository data from GitHub (usually 5–20 API calls) and checking dependencies on PyPI and npm. Large repositories can take 20–40 seconds.</p>
      </div>
      <div class="grid-cards">${"<div class='skeleton' style='height:92px'></div>".repeat(5)}</div>
      <div class="skeleton" style="height:260px"></div>`;
  }
  function renderError(url, err) {
    const hints = {
      RateLimitError: "Set a GITHUB_TOKEN before starting the server to raise the limit from 60 to 5,000 calls per hour.",
      RepoNotFoundError: "Check the spelling. Private repositories can't be analysed without access.",
      InvalidURLError: "Use a link like https://github.com/owner/repo or just owner/repo.",
      NetworkError: "Check your internet connection and try again.",
    };
    view.innerHTML = `
      <div class="alert bad" role="alert"><div><b>Couldn't analyse ${esc(url)}</b><p>${esc(err.message)}</p>
      ${hints[err.kind] ? `<p class="small" style="margin-top:6px">${esc(hints[err.kind])}</p>` : ""}</div></div>
      <div><button class="btn btn-ghost" data-retry="${esc(url)}">Try again</button></div>`;
  }

  /* ---------- report ---------- */
  function highlights(d) {
    const out = [], s = d.deps.summary, t = d.tech, a = d.activity;
    if (d.repo.archived) out.push(["bad", "Archived", "The owners marked this repository read-only."]);
    if (s.total) {
      if (s.unmaintained) out.push(["warn", "Deps", `${s.unmaintained} of ${s.total} dependencies had no release in 2+ years.`]);
      if (s.outdated) out.push(["warn", "Deps", `${s.outdated} dependenc${s.outdated === 1 ? "y is" : "ies are"} behind the latest major version.`]);
      if (s.unpinned) out.push(["accent", "Deps", `${s.unpinned} of ${s.total} dependencies use version ranges instead of exact pins.`]);
    } else out.push(["accent", "Deps", "No supported dependency manifest was found."]);
    const ci = t.tools.filter((x) => x.category === "CI/CD");
    out.push(ci.length ? ["good", "CI", `Continuous integration found: ${ci.map((x) => x.name).join(", ")}.`]
                       : ["warn", "CI", "No CI configuration file was found."]);
    if (t.tools.some((x) => x.category === "Testing") || t.frameworks.some((x) => x.category === "Testing"))
      out.push(["good", "Tests", "A testing tool is configured."]);
    if (a.total === 0) out.push(["warn", "Activity", `No commits in the last 26 weeks${d.repo.pushed ? `; last push ${d.repo.pushed}` : ""}.`]);
    else out.push(["good", "Activity", `${a.total}${a.capped ? "+" : ""} commits in 26 weeks, active in ${a.active_weeks} of 26 weeks.`]);
    if (d.structure.truncated) out.push(["warn", "Files", "GitHub truncated the file tree, so counts are a lower bound."]);
    return out;
  }

  function renderReport(d) {
    const r = d.repo, cx = d.complexity, s = d.deps.summary, a = d.activity;
    const health = s.health_pct == null ? "—" : `${s.health_pct}%`;
    view.innerHTML = `
      <div class="repo-head">
        <div class="title">
          <span class="path">github.com / ${esc(r.full_name.replace("/", " / "))}</span>
          <h1>${esc(r.full_name)}</h1>
          <p class="muted">${r.description ? esc(r.description) : "<i>No description (Not determined)</i>"}</p>
          <div class="chips" style="gap:6px">
            ${pill(r.license || "Licence: not determined")}${pill("branch " + (r.default_branch || "—"))}
            ${r.pushed ? pill("last push " + r.pushed) : ""}${r.archived ? `<span class="pill bad">Archived</span>` : ""}
            ${(r.topics || []).slice(0, 5).map((x) => `<span class="pill accent">${esc(x)}</span>`).join("")}
          </div>
        </div>
        <div class="actions">
          <a class="btn btn-ghost btn-sm" href="${esc(r.url)}" target="_blank" rel="noopener">Open on GitHub</a>
          <button class="btn btn-ghost btn-sm" data-copy-link>Copy link</button>
          <div class="menu"><button class="btn btn-dark btn-sm" data-menu aria-expanded="false">Export ▾</button>
            <div class="menu-list" hidden><button data-export="md">Markdown report</button><button data-export="json">JSON data</button><button data-export="csv">Dependencies CSV</button></div></div>
          <button class="btn btn-ghost btn-sm" data-retry="${esc(r.full_name)}" data-refresh>Re-run</button>
        </div>
      </div>
      <p class="muted small">${d.cached ? `Cached result from ${Math.round(d.cached_age_s / 60)} min ago · ` : ""}Generated ${esc(d.generated.slice(0, 16).replace("T", " "))} · ${d.api_requests} GitHub API calls${d.seconds ? ` · ${d.seconds}s` : ""}</p>

      <div class="grid-cards">
        ${stat("Stars", fmt(r.stars))}${stat("Forks", fmt(r.forks))}
        ${stat("Complexity", `${cx.score}<small style="font-size:16px"> / 100</small>`, cx.band, true)}
        ${stat("Dependency health", health, s.total ? `${s.healthy} of ${s.checked} checked` : "no manifest")}
        ${stat("Commits · 26 wks", fmt(a.total) + (a.capped ? "+" : ""), `${a.active_weeks}/26 weeks active`)}
      </div>

      <div class="card"><div class="card-head"><h2>At a glance</h2><span class="muted small">Derived only from the measurements below</span></div>
        <ul class="highlights">${highlights(d).map(([k, tag, txt]) => `<li><span class="pill ${k}">${tag}</span><span>${esc(txt)}</span></li>`).join("")}</ul></div>

      <div class="row">
        <div class="card" style="flex: 3 1 520px">${techCard(d.tech)}</div>
        <div class="card" style="flex: 2 1 380px">${complexityCard(cx)}</div>
      </div>
      <div class="card" id="structure">${structureCard(d.structure)}</div>
      <div class="card" id="deps">${depsCard(d.deps)}</div>
      <div class="row">
        <div class="card chart" style="flex: 3 1 520px">${activityCard(a)}</div>
        <div class="card" style="flex: 2 1 320px">${peopleCard(a)}</div>
      </div>`;
  }
  const pill = (t) => `<span class="pill">${esc(t)}</span>`;
  const stat = (label, val, sub, dark) =>
    `<div class="stat${dark ? " dark" : ""}"><span class="eyebrow">${label}</span><b>${val}</b>${sub ? `<small>${esc(sub)}</small>` : ""}</div>`;

  function techCard(t) {
    const langs = t.languages;
    const bar = langs.map((l, i) => `<i style="width:${l.percent}%;background:${LANG_COLORS[i % LANG_COLORS.length]}" title="${esc(l.name)} ${l.percent}%"></i>`).join("");
    const items = [...t.frameworks, ...t.tools];
    return `<div class="card-head"><h2>Tech stack</h2><span class="muted small">${items.length} frameworks and tools</span></div>
      ${langs.length ? `<div class="lang-bar" role="img" aria-label="Language share">${bar}</div>
        <div class="legend">${langs.map((l, i) => `<span><i style="background:${LANG_COLORS[i % LANG_COLORS.length]}"></i>${esc(l.name)} <b class="mono">${l.percent}%</b></span>`).join("")}
        ${t.other_languages.length ? `<span class="muted">+ ${t.other_languages.map((o) => esc(o.name)).join(", ")} (&lt;0.5%)</span>` : ""}</div>`
        : `<p class="muted">Languages: Not determined (GitHub reports no language data).</p>`}
      ${items.length ? `<div class="table-box"><table><thead><tr><th>Name</th><th>Category</th><th>Evidence</th></tr></thead><tbody>
        ${items.map((x) => `<tr><td><b>${esc(x.name)}</b></td><td><span class="pill">${esc(x.category)}</span></td><td class="mono small muted">${esc(x.evidence)}</td></tr>`).join("")}
        </tbody></table></div>` : `<p class="muted">No known framework dependencies or tool configuration files found.</p>`}`;
  }

  function complexityCard(cx) {
    const C = 2 * Math.PI * 54, color = { Low: "#5E8B7E", Moderate: "#C7A17A", High: "#B5733F", "Very high": "#A1442F" }[cx.band];
    return `<div class="card-head"><h2>Complexity score</h2><span class="pill">${esc(cx.band)}</span></div>
      <div class="score-wrap">
        <div class="gauge"><svg width="132" height="132" viewBox="0 0 132 132" aria-hidden="true">
          <circle cx="66" cy="66" r="54" fill="none" stroke="var(--sunk)" stroke-width="14"/>
          <circle cx="66" cy="66" r="54" fill="none" stroke="${color}" stroke-width="14" stroke-linecap="round" stroke-dasharray="${(cx.score / 100) * C} ${C}"/></svg>
          <b aria-label="Score ${cx.score} of 100">${cx.score}</b></div>
        <p class="muted small" style="flex:1 1 160px">Main reasons: ${cx.main_reasons.map(esc).join("; ") || "small, simple repository"}.${cx.partial ? " Based on a truncated file tree." : ""}</p>
      </div>
      ${cx.rows.map((r) => `<div class="signal"><span>${esc(r.signal)}</span>
        <div class="meter"><i style="width:${(r.points / r.max) * 100}%"></i></div><span class="mono" style="text-align:right">${r.points}/${r.max}</span>
        <span class="why">${esc(r.detail)}</span></div>`).join("")}
      <details><summary>How the score is calculated</summary><p class="small muted" style="margin-top:8px">Each signal is scaled to 0–1 and capped (file count and code volume on a log scale), then multiplied by its weight. Bands: 0–24 Low, 25–49 Moderate, 50–74 High, 75–100 Very high. It measures size and spread, not per-function logic.</p></details>`;
  }

  function structureCard(st) {
    const folders = [...st.folders].sort((a, b) => folderSort === "size" ? b.bytes - a.bytes : folderSort === "name" ? a.name.localeCompare(b.name) : b.files - a.files);
    return `<div class="card-head"><h2>File structure</h2><span class="muted small">${fmt(st.total_files)} files · ${fmt(st.total_dirs)} folders · ${kb(st.total_bytes)} · ${fmt(st.source_files)} source files · depth ${st.max_depth}</span></div>
      ${st.truncated ? `<div class="alert warn small">GitHub truncated the file tree for this very large repository; counts are a lower bound.</div>` : ""}
      <div class="ext-chips" aria-label="Most common file types">${st.extensions.map(([e, c]) => `<span>${esc(e)} · ${c}</span>`).join("")}</div>
      <div class="table-box"><table><thead><tr>
        <th><button data-sort="name">Folder</button></th><th class="num"><button data-sort="files">Files${folderSort === "files" ? " ↓" : ""}</button></th>
        <th class="num"><button data-sort="size">Size${folderSort === "size" ? " ↓" : ""}</button></th><th>Main file types</th><th>Purpose</th></tr></thead><tbody>
        ${folders.slice(0, 25).map((f) => `<tr><td class="mono"><b>${esc(f.name)}</b></td><td class="num">${fmt(f.files)}</td><td class="num">${kb(f.bytes)}</td><td class="mono small">${esc(f.top_types)}</td>
          <td>${f.purpose_known ? esc(f.purpose) : `<span class="muted">${esc(f.purpose)}</span>`}</td></tr>`).join("")}
        <tr><td class="mono"><b>(root)</b></td><td class="num">${st.root_files.length}</td><td></td><td class="mono small" colspan="2">${esc(st.root_files.slice(0, 8).join(", "))}${st.root_files.length > 8 ? " …" : ""}</td></tr>
      </tbody></table></div>`;
  }
  const kb = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${(n / 1e3).toFixed(1)} KB`;

  function depsCard(dep) {
    const s = dep.summary;
    if (!dep.items.length) {
      return `<div class="card-head"><h2>Dependency health</h2></div>
        <div class="empty">${dep.manifests.length ? "Manifest files were found, but they declare no dependencies." : "No supported dependency manifest found (requirements*.txt, pyproject.toml, package.json). Dependency health: Not determined."}</div>`;
    }
    const q = depQuery.toLowerCase();
    const rows = dep.items.filter((x) => (depFilter === "all" || x.status === depFilter) && (!q || x.name.toLowerCase().includes(q)))
      .sort((a, b) => ({ warning: 0, healthy: 1, "not checked": 2 }[a.status] - { warning: 0, healthy: 1, "not checked": 2 }[b.status]) || a.name.localeCompare(b.name));
    const cls = { healthy: "good", warning: "warn", "not checked": "" };
    const btn = (k, label, n) => `<button data-dep-filter="${k}" aria-pressed="${depFilter === k}">${label} <span class="mono">${n}</span></button>`;
    return `<div class="card-head"><h2>Dependency health</h2><span class="muted small">Read: ${dep.manifests.map(esc).join(", ")}</span></div>
      ${dep.unreadable.map((u) => `<div class="alert warn small">Could not read ${esc(u.path)}: ${esc(u.reason)}</div>`).join("")}
      <div class="chips" style="gap:6px"><span class="pill">${s.total} dependencies</span><span class="pill warn">${s.unpinned} unpinned</span><span class="pill warn">${s.outdated} outdated</span><span class="pill warn">${s.unmaintained} unmaintained</span></div>
      <div class="dep-tools">
        <div class="seg" role="group" aria-label="Filter dependencies">${btn("all", "All", s.total)}${btn("warning", "Warnings", s.warning)}${btn("healthy", "Healthy", s.healthy)}${btn("not checked", "Not checked", s["not checked"])}</div>
        <label class="sr" for="dep-q">Search dependencies</label><input id="dep-q" class="input" placeholder="Search packages" value="${esc(depQuery)}">
      </div>
      <div class="table-box"><table><thead><tr><th>Package</th><th>Spec</th><th>Latest</th><th>Last release</th><th>Status</th><th>Issues / note</th></tr></thead><tbody>
        ${rows.map((x) => `<tr><td><b>${esc(x.name)}</b>${x.dev ? ` <span class="pill">dev</span>` : ""}<div class="muted small mono">${esc(x.manifest)}</div></td>
          <td class="mono small">${esc(x.spec || "(any)")}</td><td class="mono small">${esc(x.latest || "—")}</td><td class="mono small">${esc((x.released || "—").slice(0, 10))}</td>
          <td><span class="pill ${cls[x.status]}"><span class="dot"></span>${esc(x.status)}</span></td>
          <td class="small">${x.issues.length ? x.issues.map((i) => `<span class="pill warn">${esc(i)}</span>`).join(" ") : esc(x.note)}</td></tr>`).join("")
          || `<tr><td colspan="6" class="muted">No dependencies match this filter.</td></tr>`}
      </tbody></table></div>
      <p class="muted small">Healthy = pinned to an exact version, on the latest major version, and released within the last 2 years. Latest versions come from PyPI / npm.</p>`;
  }

  function activityCard(a) {
    const W = 780, H = 200, pad = 26, n = a.weekly.length, bw = (W - pad) / n, max = Math.max(1, ...a.weekly);
    let bars = "", labels = "", last = "";
    a.weekly.forEach((c, i) => {
      const h = c ? Math.max(4, (c / max) * (H - 40)) : 2, x = pad + i * bw + 2, y = H - 22 - h;
      const isPeak = c === max && c > 0;
      bars += `<rect x="${x}" y="${y}" width="${bw - 4}" height="${h}" rx="3" fill="${isPeak ? "#C7A17A" : c ? "#5E8B7E" : "var(--sunk)"}"><title>Week of ${a.week_starts[i]}: ${c} commit${c === 1 ? "" : "s"}</title></rect>`;
      const m = new Date(a.week_starts[i] + "T00:00:00").toLocaleString("en", { month: "short" });
      if (m !== last) { labels += `<text class="axis" x="${x}" y="${H - 6}">${m}</text>`; last = m; }
    });
    return `<div class="card-head"><h2>Commits per week</h2><span class="muted small">Last 26 weeks · default branch</span></div>
      ${a.total ? `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Commits per week, peak ${max}">
        <text class="axis" x="0" y="14">${max}</text><line x1="${pad}" x2="${W}" y1="${H - 22}" y2="${H - 22}" stroke="var(--line)"/>${bars}${labels}</svg>
        <p class="muted small">${fmt(a.total)}${a.capped ? "+ (stopped at 1,000)" : ""} commits · active ${a.active_weeks} of 26 weeks${a.busiest_week ? ` · busiest week from ${a.busiest_week[0]} (${a.busiest_week[1]} commits, in sand)` : ""}</p>`
        : `<div class="empty">No commits in the last 26 weeks.</div>`}`;
  }

  function peopleCard(a) {
    const list = (arr, title) => {
      if (!arr.length) return "";
      const top = arr[0].commits || 1;
      return `<div class="people"><h3>${title}</h3>${arr.map((p) => `<div class="person"><div style="min-width:0"><div class="name">${esc(p.name)}${p.bot ? ` <span class="pill warn">bot</span>` : ""}</div>
        <div class="meter" style="height:6px;margin-top:4px"><i class="${p.bot ? "sand" : ""}" style="width:${(p.commits / top) * 100}%"></i></div></div><span class="mono" style="text-align:right">${fmt(p.commits)}</span></div>`).join("")}</div>`;
    };
    return `<h2>Contributors</h2>${list(a.top_recent, "Most active · 26 weeks")}${list(a.top_all_time, "All time")}
      ${!a.top_recent.length && !a.top_all_time.length ? `<p class="muted">No contributor data (Not determined).</p>` : ""}`;
  }

  /* ---------- exports ---------- */
  function markdown(d) {
    const r = d.repo, s = d.deps.summary, cx = d.complexity, a = d.activity;
    const L = [`# ${r.full_name}`, "", r.description || "_No description_", "",
      `- Stars ${fmt(r.stars)} · Forks ${fmt(r.forks)} · Licence ${r.license || "Not determined"} · Default branch ${r.default_branch || "—"}`,
      `- Generated ${d.generated} with ${d.api_requests} GitHub API calls`, "", "## Tech stack", "",
      ...d.tech.languages.map((l) => `- ${l.name}: ${l.percent}% (${l.evidence})`),
      ...[...d.tech.frameworks, ...d.tech.tools].map((x) => `- ${x.name} (${x.category}): ${x.evidence}`), "",
      `## Complexity: ${cx.score}/100 (${cx.band})`, "", "| Signal | Measured | Points |", "|---|---|---|",
      ...cx.rows.map((x) => `| ${x.signal} | ${x.detail} | ${x.points}/${x.max} |`), "",
      "## File structure", "", `${d.structure.total_files} files, ${d.structure.total_dirs} folders, max depth ${d.structure.max_depth}`, "",
      "| Folder | Files | Purpose |", "|---|---|---|", ...d.structure.folders.map((f) => `| ${f.name} | ${f.files} | ${f.purpose} |`), "",
      "## Dependency health", "", s.total ? `${s.total} dependencies: ${s.healthy} healthy, ${s.warning} with warnings, ${s["not checked"]} not checked` : "No supported manifest found.", "",
      ...(d.deps.items.length ? ["| Package | Spec | Latest | Status | Issues |", "|---|---|---|---|---|",
        ...d.deps.items.map((x) => `| ${x.name} | ${x.spec || "(any)"} | ${x.latest || "—"} | ${x.status} | ${x.issues.join(", ") || x.note} |`)] : []), "",
      "## Activity (26 weeks)", "", `${a.total} commits, active ${a.active_weeks}/26 weeks`, "",
      ...a.top_all_time.map((p) => `- ${p.name}: ${p.commits} commits (all time)`)];
    return L.join("\n");
  }
  function depsCsv(d) {
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    return ["package,spec,latest,released,status,issues,manifest",
      ...d.deps.items.map((x) => [x.name, x.spec, x.latest, (x.released || "").slice(0, 10), x.status, x.issues.join(" "), x.manifest].map(q).join(","))].join("\n");
  }

  /* ---------- compare ---------- */
  function renderCompare(results) {
    const saved = store.get("compare", ["psf/requests", "pallets/flask"]);
    cmpView.innerHTML = `
      <div class="hero" style="padding-top:12px"><span class="eyebrow">Compare</span><h1>Two repositories, same measures</h1></div>
      <form class="card cmp-form" id="cmp-form">
        <div class="field"><label for="c1">First repository</label><input id="c1" class="input mono" value="${esc(saved[0])}"></div>
        <div class="field"><label for="c2">Second repository</label><input id="c2" class="input mono" value="${esc(saved[1])}"></div>
        <button class="btn btn-primary" type="submit">Compare</button>
      </form>
      <div id="cmp-out">${results || `<div class="empty">Enter two repositories and press Compare.</div>`}</div>`;
    $("#cmp-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const a = $("#c1").value.trim(), b = $("#c2").value.trim();
      store.set("compare", [a, b]);
      $("#cmp-out").innerHTML = `<div class="card loading"><ol class="steps"><li class="active"><span class="ring"></span>Analysing both repositories…</li></ol></div>`;
      const res = await Promise.allSettled([analyze(a), analyze(b)]);
      refreshRate();
      const bad = res.find((x) => x.status === "rejected");
      if (bad) { $("#cmp-out").innerHTML = `<div class="alert bad" role="alert">${esc(bad.reason.message)}</div>`; return; }
      $("#cmp-out").innerHTML = compareTable(res[0].value, res[1].value);
    });
  }
  function compareTable(x, y) {
    const rows = [
      ["Stars", x.repo.stars, y.repo.stars, "high"], ["Forks", x.repo.forks, y.repo.forks, "high"],
      ["Complexity (0–100)", x.complexity.score, y.complexity.score, null],
      ["Dependency health %", x.deps.summary.health_pct, y.deps.summary.health_pct, "high"],
      ["Dependencies", x.deps.summary.total, y.deps.summary.total, null],
      ["Unmaintained deps", x.deps.summary.unmaintained, y.deps.summary.unmaintained, "low"],
      ["Commits, 26 weeks", x.activity.total, y.activity.total, "high"],
      ["Active weeks", x.activity.active_weeks, y.activity.active_weeks, "high"],
      ["Source files", x.structure.source_files, y.structure.source_files, null],
      ["Main language", x.tech.languages[0]?.name || "—", y.tech.languages[0]?.name || "—", null],
      ["Licence", x.repo.license || "—", y.repo.license || "—", null],
      ["CI", x.tech.tools.filter((t) => t.category === "CI/CD").map((t) => t.name).join(", ") || "none found",
             y.tech.tools.filter((t) => t.category === "CI/CD").map((t) => t.name).join(", ") || "none found", null],
    ];
    const win = (a, b, dir, side) => {
      if (!dir || typeof a !== "number" || typeof b !== "number" || a === b) return "";
      return (dir === "high" ? (side ? b > a : a > b) : (side ? b < a : a < b)) ? "win" : "";
    };
    return `<div class="card"><div class="table-box"><table class="cmp"><thead><tr><th>Measure</th><th>${esc(x.repo.full_name)}</th><th>${esc(y.repo.full_name)}</th></tr></thead><tbody>
      ${rows.map(([k, a, b, dir]) => `<tr><td>${k}</td><td class="${win(a, b, dir, 0)}">${esc(fmt(a))}</td><td class="${win(a, b, dir, 1)}">${esc(fmt(b))}</td></tr>`).join("")}
      </tbody></table></div><p class="muted small">Bold marks the better value only where "better" is clear (stars, dependency health, fewer unmaintained dependencies, more activity). Complexity is not marked: simpler is not automatically better.</p></div>`;
  }

  /* ---------- events ---------- */
  document.addEventListener("click", (e) => {
    const t = e.target.closest("button, a");
    if (!t) { document.querySelectorAll(".menu-list").forEach((m) => (m.hidden = true)); return; }
    if (t.dataset.run) { if (location.hash === "#compare") location.hash = "#report"; run(t.dataset.run); }
    else if (t.dataset.retry) run(t.dataset.retry, t.hasAttribute("data-refresh"));
    else if (t.hasAttribute("data-clear-history")) { store.set("history", []); renderHome(); }
    else if (t.hasAttribute("data-copy-link")) copy(location.href);
    else if (t.hasAttribute("data-menu")) { const l = t.nextElementSibling; l.hidden = !l.hidden; t.setAttribute("aria-expanded", String(!l.hidden)); }
    else if (t.dataset.export && current) {
      const n = current.repo.full_name.replace("/", "_");
      if (t.dataset.export === "md") download(`${n}_report.md`, markdown(current), "text/markdown");
      if (t.dataset.export === "json") download(`${n}_report.json`, JSON.stringify(current, null, 2), "application/json");
      if (t.dataset.export === "csv") download(`${n}_dependencies.csv`, depsCsv(current), "text/csv");
      t.parentElement.hidden = true;
    }
    else if (t.dataset.depFilter) { depFilter = t.dataset.depFilter; $("#deps").innerHTML = depsCard(current.deps); }
    else if (t.dataset.sort) { folderSort = t.dataset.sort; $("#structure").innerHTML = structureCard(current.structure); }
  });
  document.addEventListener("input", (e) => {
    if (e.target.id === "dep-q") {
      depQuery = e.target.value; const pos = e.target.selectionStart;
      $("#deps").innerHTML = depsCard(current.deps);
      const el = $("#dep-q"); el.focus(); el.setSelectionRange(pos, pos);
    }
  });

  route();
  refreshRate();
})();
