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

  async function openEditor(id) { /* Task 9 */ st.current = await api(`/menus/${id}`); toast(st.current.name, "info"); }

  document.getElementById("menu-new-btn")?.addEventListener("click", createMenu);

  return { show, refreshList, openEditor, _st: st };
})();
