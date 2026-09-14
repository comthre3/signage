/* Menus section (Plan A). Depends on api(), toast(), confirmDialog(), escHtml(), escAttr(), Khan.t() from app.js. */
const Menus = (() => {
  const st = { menus: [], current: null, templates: [] };
  let rendering = false; // Task 10 fix: in-flight guard so double-clicking Render can't start two jobs.
  let playlistCreated = false; // Task 10 fix: dispatch #menu-playlist-btn on state instead of double-binding it.

  // app.js's formatDate is private to the ApiKeys module, so define our own.
  function formatDate(iso) {
    if (!iso) return "";
    const locale = document.documentElement.lang === "ar" ? "ar-KW" : "en-GB";
    return new Date(iso).toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
  }

  async function show() {
    document.getElementById("menu-editor").classList.add("hidden");
    document.getElementById("menu-renders").classList.add("hidden");
    document.getElementById("menus-list").classList.remove("hidden");
    await refreshList();
  }

  async function refreshList() {
    try {
      const body = await api("/menus");
      st.menus = body.items || [];
      renderList();
    } catch (err) {
      toast(Khan.t("menus.error.fetch", "Failed to load menus."), "error");
    }
  }

  function renderList() {
    const el = document.getElementById("menus-list");
    el.innerHTML = "";
    if (!st.menus.length) {
      const p = document.createElement("p");
      p.className = "empty-state";
      p.textContent = Khan.t("menus.empty", "No menus yet. Create one to get started.");
      el.appendChild(p);
      return;
    }
    st.menus.forEach((m) => {
      const card = document.createElement("div");
      card.className = "menu-card";
      const rendered = m.last_rendered_at
        ? Khan.t("menus.card.rendered", "Rendered {when}").replace("{when}", formatDate(m.last_rendered_at))
        : Khan.t("menus.card.never_rendered", "Not rendered yet");
      card.innerHTML = `
        <h3>${escHtml(m.name)}</h3>
        <div class="menu-card-meta">
          <span class="badge">${escHtml(m.template)}</span>
          <span class="muted">${escHtml(Khan.t("menus.card.items", "{n} items").replace("{n}", m.item_count))}</span>
          <span class="muted">${escHtml(rendered)}</span>
        </div>
        <div class="menu-card-actions">
          <button class="btn" data-edit="${escAttr(m.id)}">${escHtml(Khan.t("menus.card.edit", "Edit"))}</button>
          <button class="btn btn-ghost delete-btn" data-delete="${escAttr(m.id)}">${escHtml(Khan.t("menus.card.delete", "Delete"))}</button>
        </div>`;
      card.querySelector("[data-edit]").addEventListener("click", () => openEditor(m.id));
      card.querySelector("[data-delete]").addEventListener("click", () => removeMenu(m.id));
      el.appendChild(card);
    });
  }

  async function createMenu() {
    const name = prompt(Khan.t("menus.new_prompt_name", "Menu name"));
    if (!name || !name.trim()) return;
    try {
      const menu = await api("/menus", { method: "POST", body: JSON.stringify({ name: name.trim(), template: "dark-classic" }) });
      await openEditor(menu.id);
    } catch (err) { toast(err.message, "error"); }
  }

  async function removeMenu(id) {
    const ok = await confirmDialog({
      message: Khan.t("menus.confirm_delete", "Delete this menu? Rendered boards stay in your media library."),
      confirmLabel: Khan.t("menus.card.delete", "Delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/menus/${id}`, { method: "DELETE" });
      await refreshList();
    } catch (err) { toast(err.message, "error"); }
  }

  const BADGES = ["new", "spicy", "vegan", "halal", "popular"];

  async function openEditor(id) {
    try {
      const [menu, tpls] = await Promise.all([api(`/menus/${id}`), api("/menus/templates")]);
      st.current = menu; st.templates = tpls.items || [];
    } catch (err) { toast(err.message, "error"); return; }
    document.getElementById("menus-list").classList.add("hidden");
    document.getElementById("menu-renders").classList.add("hidden");
    const ed = document.getElementById("menu-editor");
    ed.classList.remove("hidden");
    renderEditor();
  }

  function field(labelKey, fallback, value, attrs = "") {
    return `<label class="field"><span>${escHtml(Khan.t(labelKey, fallback))}</span><input ${attrs} value="${escAttr(value ?? "")}" /></label>`;
  }

  function renderEditor() {
    const m = st.current, b = m.brand;
    const ed = document.getElementById("menu-editor");
    ed.innerHTML = `
      <button class="btn btn-ghost" id="menu-back">${escHtml(Khan.t("menus.editor.back", "← All menus"))}</button>
      <div class="menu-editor-grid">
        <section class="panel-sub">
          ${field("menus.editor.name", "Menu name", m.name, 'data-f="name" maxlength="120"')}
          <h3>${escHtml(Khan.t("menus.editor.brand", "Brand"))}</h3>
          ${field("menus.editor.brand_name_en", "Business name (English)", b.name_en, 'data-b="name_en" maxlength="120"')}
          ${field("menus.editor.brand_name_ar", "Business name (Arabic)", b.name_ar, 'data-b="name_ar" dir="rtl" maxlength="120"')}
          ${field("menus.editor.tagline_en", "Tagline (English)", b.tagline_en, 'data-b="tagline_en" maxlength="160"')}
          ${field("menus.editor.tagline_ar", "Tagline (Arabic)", b.tagline_ar, 'data-b="tagline_ar" dir="rtl" maxlength="160"')}
          <div class="colour-row">
            ${field("menus.editor.primary", "Primary colour", b.primary, 'type="color" data-b="primary"')}
            ${field("menus.editor.accent", "Accent colour", b.accent, 'type="color" data-b="accent"')}
            ${field("menus.editor.background", "Background", b.background, 'type="color" data-b="background"')}
          </div>
          <div class="field"><span>${escHtml(Khan.t("menus.editor.logo", "Logo"))}</span>
            <div class="logo-row">
              <span id="menu-logo-preview">${b.logo_media_id ? `#${b.logo_media_id}` : "—"}</span>
              <button class="btn" id="menu-logo-pick">${escHtml(Khan.t("menus.editor.logo_pick", "Choose from media"))}</button>
              <button class="btn btn-ghost" id="menu-logo-clear">${escHtml(Khan.t("menus.editor.logo_clear", "Remove"))}</button>
            </div></div>
          <h3>${escHtml(Khan.t("menus.editor.template", "Template"))}</h3>
          <div class="template-picker">${st.templates.map((t) => `
            <button class="template-card tpl-${t.id}${t.id === m.template ? " selected" : ""}" data-tpl="${t.id}"
                    style="--p:${escAttr(b.primary)};--a:${escAttr(b.accent)};--bg:${escAttr(b.background)}">
              <span class="tpl-preview"><i></i><i></i><i></i></span>
              <span>${escHtml(document.documentElement.lang === "ar" ? t.name_ar : t.name_en)}</span>
            </button>`).join("")}</div>
        </section>
        <section class="panel-sub">
          <div class="row-between"><h3>${escHtml(Khan.t("menus.editor.categories", "Categories"))}</h3>
            <button class="btn" id="menu-add-cat">${escHtml(Khan.t("menus.editor.add_category", "+ Category"))}</button></div>
          <div id="menu-cats">${m.categories.map((c, ci) => catHtml(c, ci)).join("")}</div>
        </section>
      </div>
      <section class="panel-sub menu-preview-pane">
        <div class="row-between">
          <h3>${escHtml(Khan.t("menus.editor.preview", "Preview"))}</h3>
          <div class="menu-preview-controls">
            <select id="menu-preview-lang" aria-label="${escAttr(Khan.t("menus.editor.preview_lang", "Preview language"))}">
              <option value="en">EN</option>
              <option value="ar">AR</option>
              <option value="bi">EN + AR</option>
            </select>
            <select id="menu-preview-aspect" aria-label="${escAttr(Khan.t("menus.editor.preview_aspect", "Preview aspect"))}">
              <option value="16:9">16:9</option>
              <option value="9:16">9:16</option>
            </select>
            <button class="btn btn-ghost" id="menu-preview-refresh">${escHtml(Khan.t("menus.editor.preview_refresh", "Refresh"))}</button>
          </div>
        </div>
        <p class="helper-text" data-i18n="menus.editor.preview_hint">${escHtml(
          Khan.t("menus.editor.preview_hint",
                 "Live preview — no rendering needed. Save to update it."))}</p>
        <div class="menu-preview-stage" id="menu-preview-stage">
          <iframe id="menu-preview-frame" title="${escAttr(Khan.t("menus.editor.preview", "Preview"))}"></iframe>
        </div>
      </section>
      <div class="editor-actions">
        <button class="btn btn-primary" id="menu-save">${escHtml(Khan.t("menus.editor.save", "Save"))}</button>
        <button class="btn" id="menu-render">${escHtml(Khan.t("menus.editor.render", "Render boards"))}</button>
      </div>`;
    bindEditor();
  }

  function catHtml(c, ci) {
    return `<div class="menu-cat" data-ci="${ci}">
      <div class="menu-cat-head">
        <input data-c="name_en" placeholder="${escAttr(Khan.t("menus.editor.category_en", "Category (English)"))}" value="${escAttr(c.name_en)}" maxlength="120" />
        <input data-c="name_ar" dir="rtl" placeholder="${escAttr(Khan.t("menus.editor.category_ar", "Category (Arabic)"))}" value="${escAttr(c.name_ar || "")}" maxlength="120" />
        <button class="btn btn-ghost" data-cat-up title="${escAttr(Khan.t("menus.editor.move_up", "Move up"))}">↑</button>
        <button class="btn btn-ghost" data-cat-down title="${escAttr(Khan.t("menus.editor.move_down", "Move down"))}">↓</button>
        <button class="btn btn-ghost delete-btn" data-cat-remove>${escHtml(Khan.t("menus.editor.remove", "Remove"))}</button>
      </div>
      <table class="menu-items">
        <thead><tr>
          <th>${escHtml(Khan.t("menus.editor.col.name_en", "Name (EN)"))}</th><th>${escHtml(Khan.t("menus.editor.col.name_ar", "Name (AR)"))}</th>
          <th>${escHtml(Khan.t("menus.editor.col.desc_en", "Description (EN)"))}</th><th>${escHtml(Khan.t("menus.editor.col.desc_ar", "Description (AR)"))}</th>
          <th>${escHtml(Khan.t("menus.editor.col.price", "Price"))}</th><th>${escHtml(Khan.t("menus.editor.col.note", "Note"))}</th>
          <th>${escHtml(Khan.t("menus.editor.col.badges", "Badges"))}</th><th>${escHtml(Khan.t("menus.editor.col.available", "Available"))}</th><th></th>
        </tr></thead>
        <tbody>${c.items.map((it, ii) => itemHtml(it, ii)).join("")}</tbody>
      </table>
      <button class="btn" data-add-item>${escHtml(Khan.t("menus.editor.add_item", "+ Item"))}</button>
    </div>`;
  }

  function itemHtml(it, ii) {
    return `<tr data-ii="${ii}">
      <td><input data-i="name_en" value="${escAttr(it.name_en)}" maxlength="120" /></td>
      <td><input data-i="name_ar" dir="rtl" value="${escAttr(it.name_ar || "")}" maxlength="120" /></td>
      <td><input data-i="description_en" value="${escAttr(it.description_en || "")}" maxlength="300" /></td>
      <td><input data-i="description_ar" dir="rtl" value="${escAttr(it.description_ar || "")}" maxlength="300" /></td>
      <td><input data-i="price" type="number" step="0.001" min="0" value="${escAttr(it.price ?? "")}" class="price-input" /></td>
      <td><input data-i="price_note" value="${escAttr(it.price_note || "")}" maxlength="60" /></td>
      <td class="badges-cell">${BADGES.map((b) => `<label><input type="checkbox" data-badge="${b}"${it.badges.includes(b) ? " checked" : ""}/>${escHtml(Khan.t(`menus.badge.${b}`, b))}</label>`).join("")}</td>
      <td><input type="checkbox" data-i="is_available"${it.is_available ? " checked" : ""} /></td>
      <td><button class="btn btn-ghost delete-btn" data-item-remove>×</button></td>
    </tr>`;
  }

  function bindEditor() {
    const m = st.current, ed = document.getElementById("menu-editor");
    ed.querySelector("#menu-back").addEventListener("click", show);
    ed.querySelector('[data-f="name"]').addEventListener("input", (e) => { m.name = e.target.value; });
    ed.querySelectorAll("[data-b]").forEach((inp) => inp.addEventListener("input", (e) => {
      m.brand[e.target.dataset.b] = e.target.value === "" ? null : e.target.value;
      if (["primary", "accent", "background"].includes(e.target.dataset.b)) {
        ed.querySelectorAll(".template-card").forEach((c) => c.style.setProperty(`--${e.target.dataset.b === "primary" ? "p" : e.target.dataset.b === "accent" ? "a" : "bg"}`, e.target.value));
      }
    }));
    ed.querySelector("#menu-logo-pick").addEventListener("click", async () => {
      let picks;
      try { picks = await MediaPicker.open({ allowedTypes: ["image"] }); }   // rejects with {cancelled:true} on cancel
      catch (_) { return; }
      if (picks && picks.length) { m.brand.logo_media_id = picks[0].media_id; ed.querySelector("#menu-logo-preview").textContent = `#${picks[0].media_id}`; }
    });
    ed.querySelector("#menu-logo-clear").addEventListener("click", () => { m.brand.logo_media_id = null; ed.querySelector("#menu-logo-preview").textContent = "—"; });
    ed.querySelectorAll("[data-tpl]").forEach((btn) => btn.addEventListener("click", () => {
      m.template = btn.dataset.tpl;
      ed.querySelectorAll(".template-card").forEach((c) => c.classList.toggle("selected", c.dataset.tpl === m.template));
    }));
    ed.querySelector("#menu-add-cat").addEventListener("click", () => { m.categories.push({ name_en: "", name_ar: "", items: [] }); renderEditor(); });
    ed.querySelectorAll(".menu-cat").forEach((catEl) => {
      const ci = Number(catEl.dataset.ci), c = m.categories[ci];
      catEl.querySelectorAll("[data-c]").forEach((inp) => inp.addEventListener("input", (e) => { c[e.target.dataset.c] = e.target.value; }));
      catEl.querySelector("[data-cat-remove]").addEventListener("click", () => { m.categories.splice(ci, 1); renderEditor(); });
      catEl.querySelector("[data-cat-up]").addEventListener("click", () => { if (ci > 0) { [m.categories[ci - 1], m.categories[ci]] = [m.categories[ci], m.categories[ci - 1]]; renderEditor(); } });
      catEl.querySelector("[data-cat-down]").addEventListener("click", () => { if (ci < m.categories.length - 1) { [m.categories[ci + 1], m.categories[ci]] = [m.categories[ci], m.categories[ci + 1]]; renderEditor(); } });
      catEl.querySelector("[data-add-item]").addEventListener("click", () => { c.items.push({ name_en: "", name_ar: "", price: null, badges: [], is_available: true }); renderEditor(); });
      catEl.querySelectorAll("tr[data-ii]").forEach((row) => {
        const it = c.items[Number(row.dataset.ii)];
        row.querySelectorAll("[data-i]").forEach((inp) => inp.addEventListener("input", (e) => {
          const k = e.target.dataset.i;
          if (k === "is_available") it[k] = e.target.checked;
          else if (k === "price") it[k] = e.target.value === "" ? null : e.target.value;
          else it[k] = e.target.value === "" ? null : e.target.value;
        }));
        row.querySelectorAll("[data-badge]").forEach((cb) => cb.addEventListener("change", (e) => {
          const b = e.target.dataset.badge;
          it.badges = e.target.checked ? [...new Set([...it.badges, b])] : it.badges.filter((x) => x !== b);
        }));
        row.querySelector("[data-item-remove]").addEventListener("click", () => { c.items.splice(Number(row.dataset.ii), 1); renderEditor(); });
      });
    });
    ed.querySelector("#menu-save").addEventListener("click", save);
    ed.querySelector("#menu-preview-refresh")?.addEventListener("click", loadPreview);
    ed.querySelector("#menu-preview-lang")?.addEventListener("change", loadPreview);
    ed.querySelector("#menu-preview-aspect")?.addEventListener("change", loadPreview);
    loadPreview();
    ed.querySelector("#menu-render").addEventListener("click", () => Menus.renderBoards()); // Task 10
  }

  function payload() {
    const m = st.current;
    return {
      name: m.name, template: m.template, brand: m.brand,
      categories: m.categories.map((c) => ({
        id: c.id, name_en: c.name_en, name_ar: c.name_ar || "",
        items: c.items.map((it) => ({
          id: it.id, name_en: it.name_en, name_ar: it.name_ar || "", description_en: it.description_en || null,
          description_ar: it.description_ar || null, price: it.price === "" ? null : it.price,
          price_note: it.price_note || null, badges: it.badges || [], is_available: it.is_available !== false,
        })),
      })),
    };
  }

  async function loadPreview() {
    const frame = document.getElementById("menu-preview-frame");
    const stage = document.getElementById("menu-preview-stage");
    if (!frame || !st.current) return;
    const lang   = document.getElementById("menu-preview-lang")?.value   || "en";
    const aspect = document.getElementById("menu-preview-aspect")?.value || "16:9";
    stage.classList.add("loading");
    try {
      // srcdoc rather than pointing src at the API: the document is fully
      // self-contained (inlined CSS, logo as a data URI), so this needs no
      // frame-src entry and no cross-origin image loads.
      const html = await apiText(
        `/menus/${st.current.id}/preview?language=${encodeURIComponent(lang)}` +
        `&aspect=${encodeURIComponent(aspect)}`);
      frame.srcdoc = html;
      const [w, h] = aspect === "9:16" ? [1080, 1920] : [1920, 1080];
      stage.style.aspectRatio = `${w} / ${h}`;
      // Portrait at full panel width is ~1900px tall -- taller than a laptop
      // screen, which buries the Save button beneath the preview. Size it from
      // the viewport height instead, so either orientation fits on screen.
      stage.style.width = aspect === "9:16"
        ? `min(100%, calc(70vh * ${w} / ${h}))`
        : "100%";
      frame.dataset.w = w;
      frame.dataset.h = h;
      frame.onload = () => scalePreview();
    } catch (err) {
      frame.srcdoc = `<p style="font:14px system-ui;padding:12px;color:#a33">${
        escHtml(err.message || "Preview failed")}</p>`;
    } finally {
      stage.classList.remove("loading");
    }
  }

  function scalePreview() {
    const frame = document.getElementById("menu-preview-frame");
    const stage = document.getElementById("menu-preview-stage");
    if (!frame || !stage || !frame.contentDocument) return;
    const w = Number(frame.dataset.w || 1920);
    const scale = stage.clientWidth / w;
    const root = frame.contentDocument.documentElement;
    root.style.transformOrigin = "0 0";
    root.style.transform = `scale(${scale})`;
  }

  window.addEventListener("resize", () => { try { scalePreview(); } catch (_) {} });

  async function save() {
    try {
      st.current = await api(`/menus/${st.current.id}`, { method: "PUT", body: JSON.stringify(payload()) });
      loadPreview();   // an edit the author cannot see is not much of an edit
      toast(Khan.t("menus.editor.saved", "Menu saved."), "success");
      renderEditor();
      return st.current;
    } catch (err) { toast(err.message, "error"); return null; }
  }

  function renderOptionsHtml() {
    const cb = (name, val, key, fb, checked) => `<label><input type="checkbox" name="${name}" value="${val}"${checked ? " checked" : ""}/> ${escHtml(Khan.t(key, fb))}</label>`;
    return `<div class="render-options">
      <strong>${escHtml(Khan.t("menus.render.options", "What to render"))}</strong>
      <div>${cb("lang", "en", "menus.render.lang.en", "English", true)}${cb("lang", "ar", "menus.render.lang.ar", "Arabic", true)}${cb("lang", "bi", "menus.render.lang.bi", "Bilingual", false)}</div>
      <div>${cb("aspect", "16:9", "menus.render.aspect.landscape", "Landscape 16:9", true)}${cb("aspect", "9:16", "menus.render.aspect.portrait", "Portrait 9:16", false)}</div>
      <div>${cb("kind", "board", "menus.render.kind.board", "Full menu", true)}${cb("kind", "category", "menus.render.kind.category", "One board per category", false)}${cb("kind", "promo", "menus.render.kind.promo", "Promos (new / popular items)", false)}</div>
      <button class="btn btn-primary" id="menu-render-go">${escHtml(Khan.t("menus.render.go", "Render"))}</button>
    </div>`;
  }

  async function renderBoards() {
    const saved = await save();
    if (!saved) return;
    showRenders();
  }

  async function showRenders() {
    document.getElementById("menu-editor").classList.add("hidden");
    const box = document.getElementById("menu-renders");
    box.classList.remove("hidden");
    box.innerHTML = `<button class="btn btn-ghost" id="menu-renders-back">${escHtml(Khan.t("menus.render.back", "← Editor"))}</button>
      <h3>${escHtml(Khan.t("menus.render.title", "Boards"))}</h3>${renderOptionsHtml()}
      <p id="menu-renders-status" class="muted"></p><div id="menu-renders-grid" class="renders-grid"></div>
      <div class="editor-actions"><button class="btn btn-primary" id="menu-playlist-btn">${escHtml(Khan.t("menus.render.playlist", "Create playlist"))}</button></div>`;
    box.querySelector("#menu-renders-back").addEventListener("click", () => { box.classList.add("hidden"); document.getElementById("menu-editor").classList.remove("hidden"); });
    box.querySelector("#menu-render-go").addEventListener("click", startRender);
    playlistCreated = false;
    box.querySelector("#menu-playlist-btn").addEventListener("click", async () => {
      if (playlistCreated) { showSection("playlists"); return; }
      await createPlaylist();
    });
    await refreshRenders();
  }

  function picked(name) { return [...document.querySelectorAll(`#menu-renders input[name="${name}"]:checked`)].map((i) => i.value); }

  async function startRender() {
    if (rendering) return;
    rendering = true;
    const btn = document.getElementById("menu-render-go");
    if (btn) btn.disabled = true;
    try {
      const body = { languages: picked("lang"), aspects: picked("aspect"), kinds: picked("kind") };
      const r = await api(`/menus/${st.current.id}/render`, { method: "POST", body: JSON.stringify(body) });
      document.getElementById("menu-renders-status").textContent = Khan.t("menus.render.queued", "Rendering {n} boards…").replace("{n}", r.queued);
      await pollRenders();
    } catch (err) {
      toast(err.message, "error");
    } finally {
      rendering = false;
      if (btn) btn.disabled = false;
    }
  }

  async function pollRenders() {
    for (let i = 0; i < 45; i++) {
      const items = await refreshRenders();
      if (items === null) {
        // Transient API failure: keep polling instead of declaring success or giving up.
        await new Promise((res) => setTimeout(res, 2000));
        continue;
      }
      if (!items.some((x) => x.status === "pending")) {
        document.getElementById("menu-renders-status").textContent = Khan.t("menus.render.done", "Boards ready.");
        return;
      }
      await new Promise((res) => setTimeout(res, 2000));
    }
  }

  async function refreshRenders() {
    const grid = document.getElementById("menu-renders-grid");
    let items = [];
    try { items = (await api(`/menus/${st.current.id}/renders`)).items || []; } catch (err) { toast(err.message, "error"); return null; }
    grid.innerHTML = items.length ? "" : `<p class="empty-state">${escHtml(Khan.t("menus.render.empty", "No boards yet."))}</p>`;
    const stale = st.current.last_rendered_at && st.current.updated_at > st.current.last_rendered_at;
    document.getElementById("menu-renders-stale")?.remove();
    if (stale) grid.insertAdjacentHTML("beforebegin", `<p id="menu-renders-stale" class="muted stale-note">${escHtml(Khan.t("menus.render.stale", "The menu changed after these boards were rendered."))}</p>`);
    items.forEach((x) => {
      const card = document.createElement("div");
      card.className = `render-card status-${x.status}`;
      card.innerHTML = `${x.url ? `<img src="${escAttr(API_BASE + x.url)}" alt="" loading="lazy" />` : `<div class="render-ph">${x.status === "failed" ? escHtml(Khan.t("menus.render.failed", "Failed")) : "…"}</div>`}
        <div class="render-meta"><span class="badge">${escHtml(x.kind)}</span><span class="badge">${escHtml(x.language.toUpperCase())}</span><span class="badge">${escHtml(x.aspect)}</span>
        ${x.url ? `<a class="btn btn-ghost" href="${escAttr(API_BASE + x.url)}" download>${escHtml(Khan.t("menus.render.download", "Download"))}</a>` : ""}</div>
        ${x.error ? `<p class="muted">${escHtml(x.error)}</p>` : ""}`;
      grid.appendChild(card);
    });
    return items;
  }

  async function createPlaylist() {
    try {
      const pl = await api(`/menus/${st.current.id}/playlist`, { method: "POST" });
      st.current.playlist_id = pl.id;
      toast(Khan.t("menus.render.playlist_done", 'Playlist "{name}" updated.').replace("{name}", pl.name), "success");
      playlistCreated = true;
      document.getElementById("menu-playlist-btn").textContent = Khan.t("menus.render.playlist_open", "Open playlist");
    } catch (err) { toast(err.message, "error"); }
  }

  document.getElementById("menu-new-btn")?.addEventListener("click", createMenu);

  return { show, refreshList, openEditor, save, renderBoards, showRenders, _st: st };
})();
