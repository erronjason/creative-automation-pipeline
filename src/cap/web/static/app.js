(() => {
  "use strict";
  const CFG = window.__CAP__ || { mode: "report", manifests: [], imageBase: "" };
  const LIVE = CFG.mode === "live";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const h = (tag, attrs = {}, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "class") el.className = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null) el.append(kid instanceof Node ? kid : document.createTextNode(kid));
    return el;
  };

  const state = {
    manifests: CFG.manifests.slice(),
    current: null,
    filters: { product: "all", ratio: "all", locale: "all", status: "all" },
    visible: [],
    lbIndex: -1,
  };

  // ---------------------------------------------------------------------------------------------
  // API (live mode only)
  const api = async (path, opts = {}) => {
    const res = await fetch(path, { headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {}, ...opts });
    const body = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
    if (!res.ok) throw new Error(body?.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : res.statusText);
    return body;
  };

  const imgUrl = (m, rel) => {
    const base = (CFG.imageBase || "").replaceAll("{campaign}", m.campaign_id);
    return base + rel + (LIVE ? `?t=${encodeURIComponent(m.run_id)}` : "");
  };

  // ---------------------------------------------------------------------------------------------
  // Campaign view
  function setCampaigns(list, selectId) {
    state.manifests = list;
    const sel = $("campaignSelect");
    sel.replaceChildren(...list.map((m) => h("option", { value: m.campaign_id }, `${m.campaign_name} (${m.campaign_id})`)));
    sel.disabled = list.length < 2;
    const pick = list.find((m) => m.campaign_id === selectId) || list[0] || null;
    if (pick) sel.value = pick.campaign_id;
    showCampaign(pick);
  }

  function showCampaign(m) {
    state.current = m;
    $("empty").hidden = !!m;
    $("campaign").hidden = !m;
    if (!m) return;
    $("cBrand").textContent = `${m.brand} · ${m.region}`;
    $("cName").textContent = m.campaign_name;
    $("cMessage").textContent = `“${m.message}”`;
    const when = new Date(m.finished_at || m.started_at);
    $("cMeta").textContent = `Audience: ${m.audience} · ${m.provider}/${m.model} · reframe ${m.reframe_strategy} · translator ${m.translator} · run ${m.run_id} · ${isNaN(when) ? "" : when.toLocaleString()}`;

    const base = (CFG.imageBase || "").replaceAll("{campaign}", m.campaign_id);
    $("downloads").replaceChildren(
      ...[["manifest.json", "Manifest"], ["variants.csv", "Variants CSV"], ["events.jsonl", "Run log"]].map(([f, label]) =>
        h("a", { href: base + f, download: f, target: "_blank", rel: "noopener" }, label)));

    const s = m.stats;
    const stat = (v, l) => h("div", { class: "stat" }, h("div", { class: "v" }, v), h("div", { class: "l" }, l));
    const psf = h("span");
    psf.innerHTML = `<span class="pass">${s.passed}</span> / <span class="warn">${s.warned}</span> / <span class="fail">${s.failed}</span>`;
    $("stats").replaceChildren(
      stat(String(s.variants), `variants (${m.products.length} products × ${m.aspect_ratios.length} ratios × ${m.locales.length} locales)`),
      stat(psf, "pass / warn / fail"),
      stat(`${s.heroes_reused} / ${s.heroes_generated}`, "heroes reused / generated"),
      stat(String(s.genai_calls), `GenAI calls · ${s.cache_hits} cache hits`),
      stat(`$${s.est_cost_usd.toFixed(2)}`, `${s.cost_billed ? "billed" : "est."} spend · $${s.est_saved_usd.toFixed(2)} saved by cache`),
      stat(`${s.duration_s.toFixed(1)}s`, "wall-clock"),
      stat(String(m.variants.filter((v) => v.review.state === "approved").length), "approved"),
    );
    renderFilters();
    renderGrid();
  }

  function renderFilters() {
    const m = state.current;
    const groups = [
      ["product", "Product", m.products.map((p) => [p.id, p.name])],
      ["ratio", "Ratio", m.aspect_ratios.map((r) => [r, r])],
      ["locale", "Locale", m.locales.map((l) => [l, l])],
      ["status", "Status", ["pass", "warn", "fail"].map((x) => [x, x])],
    ];
    for (const [k] of groups) if (state.filters[k] !== "all" && !groups.find((g) => g[0] === k)[2].some(([v]) => v === state.filters[k])) state.filters[k] = "all";
    $("filters").replaceChildren(...groups.map(([key, label, opts]) =>
      h("div", { class: "fgroup", role: "group", "aria-label": label }, h("span", {}, label),
        ...[["all", "All"], ...opts].map(([v, text]) =>
          h("button", { class: "chip", "aria-pressed": String(state.filters[key] === v), onclick: () => { state.filters[key] = v; renderFilters(); renderGrid(); } }, text)))));
  }

  function renderGrid() {
    const m = state.current, f = state.filters;
    const match = (v) => (f.product === "all" || v.product_id === f.product) && (f.ratio === "all" || v.ratio === f.ratio) &&
      (f.locale === "all" || v.locale === f.locale) && (f.status === "all" || v.status === f.status);
    state.visible = m.variants.filter(match);
    const wrap = $("products");
    wrap.replaceChildren();
    for (const p of m.products) {
      const vs = state.visible.filter((v) => v.product_id === p.id);
      if (!vs.length) continue;
      const srcLabel = { reused: "reused approved asset", generated: "hero generated", cached: "hero from cache" }[p.asset_source] || p.asset_source;
      const head = h("div", { class: "product-head" },
        h("h2", {}, p.name), h("span", { class: `src ${p.asset_source}` }, srcLabel),
        h("details", {}, h("summary", {}, "Source hero & prompt"),
          h("p", {}, h("a", { href: imgUrl(m, p.hero_path), target: "_blank", rel: "noopener" }, "Open source hero"), " · ", p.detail),
          p.prompt ? h("p", {}, h("code", {}, p.prompt)) : null));
      const sec = h("section", { class: "product" }, head);
      for (const r of m.aspect_ratios) {
        const row = vs.filter((v) => v.ratio === r);
        if (!row.length) continue;
        const method = row[0].reframe?.method;
        sec.append(h("div", { class: "ratio-label" }, `${r} · ${row[0].width}×${row[0].height} · ${method}`));
        sec.append(h("div", { class: "ratio-row" }, ...row.map(card)));
      }
      wrap.append(sec);
    }
    if (!wrap.children.length) wrap.append(h("p", { class: "hint" }, "No variants match these filters."));
  }

  function card(v) {
    const m = state.current;
    return h("button", { class: "card", onclick: () => openLightbox(state.visible.indexOf(v)), "aria-label": `${v.product_id} ${v.ratio} ${v.locale} ${v.status}` },
      h("img", { src: imgUrl(m, v.path), loading: "lazy", alt: `${v.copy_text.message} (${v.locale}, ${v.ratio})`, width: Math.round(240 * v.width / v.height), height: 240 }),
      h("div", { class: "foot" },
        h("span", { class: "loc" }, `${v.locale}${v.market ? " · " + v.market : ""}`),
        v.review.state !== "pending" ? h("span", { class: `rv ${v.review.state}` }, v.review.state === "approved" ? "✓ approved" : "✕ rejected") : null,
        h("span", { class: `pill ${v.status}` }, v.status)));
  }

  // ---------------------------------------------------------------------------------------------
  // Lightbox
  function openLightbox(i) {
    if (i < 0 || i >= state.visible.length) return;
    state.lbIndex = i;
    const v = state.visible[i], m = state.current;
    $("lbImg").src = imgUrl(m, v.path);
    $("lbImg").alt = v.copy_text.message;
    $("lbTitle").textContent = `${v.product_id} · ${v.ratio} · ${v.locale}`;
    $("lbStatus").replaceChildren(h("span", { class: `pill ${v.status}` }, v.status), " ",
      h("span", { class: `rv ${v.review.state}` }, `review: ${v.review.state}${v.review.note ? " · " + v.review.note : ""}`));
    const kv = [["Message", v.copy_text.message], ["CTA", v.copy_text.cta || "none"], ["Disclaimer", v.copy_text.disclaimer || "none"],
      ["Copy source", v.copy_source], ["Reframe", `${v.reframe.method}: ${v.reframe.detail}`], ["Size", `${v.width}×${v.height}`], ["File", v.path]];
    $("lbCopy").replaceChildren(...kv.flatMap(([k, val]) => [h("dt", {}, k), h("dd", {}, val)]));
    $("lbChecks").replaceChildren(...v.checks.map((c) =>
      h("li", {}, h("span", { class: `pill ${c.status}` }, c.status), h("span", {}, h("span", { class: "cid" }, c.id), h("span", { class: "cd" }, c.detail)))));
    $("lbReview").hidden = !LIVE;
    $("lbNote").value = v.review.note || "";
    $("lightbox").hidden = false;
    $("lbClose").focus();
  }
  const closeLightbox = () => { $("lightbox").hidden = true; state.lbIndex = -1; };

  async function review(decision) {
    if (!LIVE || state.lbIndex < 0) return;
    const v = state.visible[state.lbIndex], m = state.current;
    try {
      const updated = await api(`/api/campaigns/${m.campaign_id}/review`, { method: "POST", body: JSON.stringify({ variant_id: v.id, state: decision, note: $("lbNote").value }) });
      Object.assign(v.review, updated.review);
      renderGrid();
      showCampaignStatsOnly();
      const next = state.lbIndex + 1 < state.visible.length ? state.lbIndex + 1 : state.lbIndex;
      openLightbox(next);
    } catch (e) { alertInline(`Review failed: ${e.message}`); }
  }
  function showCampaignStatsOnly() { const f = { ...state.filters }; showCampaign(state.current); state.filters = f; renderFilters(); renderGrid(); }

  $("lbClose").onclick = closeLightbox;
  $("lightbox").addEventListener("click", (e) => { if (e.target.id === "lightbox") closeLightbox(); });
  $("lbApprove").onclick = () => review("approved");
  $("lbReject").onclick = () => review("rejected");
  document.addEventListener("keydown", (e) => {
    if ($("lightbox").hidden || e.target.tagName === "TEXTAREA") return;
    if (e.ctrlKey || e.metaKey || e.altKey) return; // Ctrl+R (reload) and Ctrl+A (select all) are not review decisions
    if (e.key === "Escape") closeLightbox();
    else if (e.key === "ArrowRight") openLightbox(state.lbIndex + 1);
    else if (e.key === "ArrowLeft") openLightbox(state.lbIndex - 1);
    else if (LIVE && e.key.toLowerCase() === "a") review("approved");
    else if (LIVE && e.key.toLowerCase() === "r") review("rejected");
  });
  $("campaignSelect").onchange = (e) => showCampaign(state.manifests.find((m) => m.campaign_id === e.target.value));

  // ---------------------------------------------------------------------------------------------
  // Live mode: run controls
  function alertInline(msg, ok = false) {
    $("validation").replaceChildren(h("div", { class: ok ? "ok" : "err" }, msg));
  }
  function logEvent(ev) {
    const t = new Date(ev.ts * 1000).toLocaleTimeString([], { hour12: false });
    const li = h("li", { class: ev.level }, `${t} `, h("b", {}, ev.stage), ` ${ev.message}`);
    $("log").append(li);
    li.scrollIntoView({ block: "nearest" });
  }

  async function loadBriefText() {
    const file = $("briefSelect").value;
    if (!file) return;
    const r = await api(`/api/briefs/${encodeURIComponent(file)}`);
    $("briefText").value = r.text;
    $("briefText").dataset.original = r.text;
    await validate(true);
  }

  const edited = () => $("briefText").value !== $("briefText").dataset.original;

  async function validate(quiet = false) {
    try {
      const r = await api("/api/validate", { method: "POST", body: JSON.stringify({ file: $("briefSelect").value, text: $("briefText").value }) });
      renderAssets(r.assets || []);
      if (r.ok) {
        const legal = (r.legal || []).filter((x) => x.status !== "pass");
        const box = [h("div", { class: "ok" }, `✓ ${r.summary}`)];
        for (const l of legal) box.push(h("div", { class: l.status === "fail" ? "err" : "" }, `${l.status.toUpperCase()} ${l.locale}: ${l.detail}`));
        $("validation").replaceChildren(...box);
      } else {
        alertInline(r.errors.join("\n"));
      }
      return r.ok;
    } catch (e) { if (!quiet) alertInline(e.message); return false; }
  }

  function renderAssets(items) {
    $("assetList").replaceChildren(...items.map((a) => {
      const input = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", onchange: (e) => upload(a.product_id, e.target.files[0]) });
      return h("li", {},
        h("span", { class: "thumb", style: a.url ? `background-image:url('${a.url}?t=${Date.now()}')` : "" }),
        h("span", { class: "name" }, a.name, h("small", {}, a.found ? `reuse: ${a.key}` : "missing → will generate")),
        h("label", { class: "upload" }, a.found ? "Replace" : "Upload", input));
    }));
  }

  async function upload(productId, file) {
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    try { await api(`/api/assets/${encodeURIComponent(productId)}`, { method: "POST", body: fd }); await validate(); }
    catch (e) { alertInline(`Upload failed: ${e.message}`); }
  }

  async function run() {
    if (!(await validate())) return;
    $("runBtn").disabled = true;
    $("runBtn").textContent = "Generating…";
    $("log").replaceChildren();
    try {
      const body = { file: $("briefSelect").value, provider: $("providerSelect").value, reframe: $("reframeSelect").value, translator: $("translatorSelect").value };
      if (edited()) body.text = $("briefText").value;
      const { run_id } = await api("/api/runs", { method: "POST", body: JSON.stringify(body) });
      let since = 0;
      for (;;) {
        await new Promise((r) => setTimeout(r, 600));
        const st = await api(`/api/runs/${run_id}?since=${since}`);
        st.events.forEach(logEvent);
        since = st.next;
        if (st.status === "done") {
          const camps = await api("/api/campaigns");
          setCampaigns(camps, st.campaign_id);
          break;
        }
        if (st.status === "error") { alertInline(`Run failed: ${st.error}`); break; }
      }
    } catch (e) { alertInline(e.message); }
    finally { $("runBtn").disabled = false; $("runBtn").textContent = "Generate variants"; }
  }

  async function initLive() {
    $("layout").classList.add("live");
    $("sidebar").hidden = false;
    $("modeBadge").textContent = "Live · localhost";
    $("modeBadge").classList.add("live");
    const st = await api("/api/state");
    $("briefSelect").replaceChildren(...st.briefs.map((b) => h("option", { value: b.file }, `${b.file} · ${b.name}`)));
    $("providerSelect").replaceChildren(...st.providers.map((p) =>
      h("option", { value: p.name, disabled: !p.ready, title: p.detail }, `${p.name}${p.ready ? "" : " (not configured)"}`)));
    $("providerSelect").value = st.default_provider;
    $("briefSelect").onchange = loadBriefText;
    $("validateBtn").onclick = () => validate();
    $("runBtn").onclick = run;
    setCampaigns(st.campaigns);
    const match = st.briefs.find((b) => b.campaign_id === state.current?.campaign_id && !b.file.includes(".edited."));
    if (match) $("briefSelect").value = match.file;
    await loadBriefText();
  }

  // ---------------------------------------------------------------------------------------------
  if (LIVE) initLive().catch((e) => alertInline(e.message));
  else {
    $("modeBadge").textContent = CFG.mode === "showcase" ? "Static showcase · read-only" : "Run report · read-only";
    setCampaigns(state.manifests);
  }
})();
