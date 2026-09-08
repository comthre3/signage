/* Menus section (Plan A). Depends on api(), toast(), confirmDialog(), escHtml(), escAttr(), Khan.t() from app.js. */
const Menus = (() => {
  const st = { menus: [], current: null, templates: [] };

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

  async function save() {
    try {
      st.current = await api(`/menus/${st.current.id}`, { method: "PUT", body: JSON.stringify(payload()) });
      toast(Khan.t("menus.editor.saved", "Menu saved."), "success");
      renderEditor();
      return st.current;
    } catch (err) { toast(err.message, "error"); return null; }
  }

  async function renderBoards() { await save(); } // Task 10 replaces this

  document.getElementById("menu-new-btn")?.addEventListener("click", createMenu);

  return { show, refreshList, openEditor, save, renderBoards, _st: st };
})();
