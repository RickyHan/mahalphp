"use strict";

// '아파트 관리' 탭: 별 하나로 시작하는 채팅방을 아파트로 모아 이름 관리. 서버 데이터는 텍스트 노드로만 넣는다(innerHTML 금지).
(() => {
  const $ = (id) => document.getElementById(id);
  const KC = window.KC;
  const { api, el, reportError, fmtDateTime } = KC;
  let items = [];

  async function send(method, path, body) {
    const res = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) location.href = "/admin/login";
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(", ") : data.detail;
      throw new Error(detail || `요청 실패 (HTTP ${res.status})`);
    }
    return data;
  }

  function kpi(icon, tone, label, value, unit) {
    return el(
      "div",
      { class: `kpi-card tone-${tone}` },
      el("div", { class: "kpi-text" }, el("span", { class: "kpi-label", text: label }), el("div", { class: "kpi-num" }, el("strong", { text: String(value) }), el("span", { text: unit }))),
      el("span", { class: "kpi-ico ms", "aria-hidden": "true", text: icon }),
    );
  }

  async function loadApartments() {
    const data = await api("/api/admin/apartments");
    items = data.items;
    const active = items.filter((a) => a.messages > 0).length;
    $("apt-kpis").replaceChildren(
      kpi("apartment", "blue", "관리 중인 아파트", items.length, "곳"),
      kpi("forum", "green", "대화가 있는 아파트 방", active, `/ ${items.length}`),
      kpi("assignment", "rose", "아파트 방 민원", items.reduce((n, a) => n + a.complaints, 0), "건"),
      kpi("sync", "slate", "마지막 수집", data.synced_at ? fmtDateTime(data.synced_at).slice(5, 16) : "–", ""),
    );
    render();
  }

  function render() {
    const q = $("apt-search").value.trim().toLowerCase();
    const shown = items.filter((a) => !q || a.name.toLowerCase().includes(q) || a.room_name.toLowerCase().includes(q));
    $("apt-count").textContent = q ? `${shown.length} / ${items.length}곳` : `${items.length}곳`;
    const list = $("apt-list");
    list.replaceChildren();
    if (!shown.length) {
      list.append(el("tr", {}, el("td", { colspan: "7", class: "empty-cell", text: items.length ? "검색 결과가 없습니다." : "아직 등록된 아파트가 없습니다. 오른쪽 위 '다시 수집하기'를 누르십시오." })));
      return;
    }
    for (const a of shown) list.append(row(a));
  }

  function row(a) {
    const input = el("input", { class: "apt-name", value: a.name, maxlength: "100", "aria-label": `${a.room_name} 아파트 이름` });
    const save = el("button", { class: "btn btn-small btn-primary", type: "button", text: "저장", disabled: true });
    const tr = el("tr", {});
    input.addEventListener("input", () => {
      const changed = input.value.trim() !== a.name;
      save.disabled = !changed || !input.value.trim();
      tr.classList.toggle("dirty", changed);
    });
    const doSave = async () => {
      if (save.disabled) return;
      try {
        const r = await send("PUT", `/api/admin/apartments/${a.id}`, { name: input.value });
        a.name = r.name;
        input.value = r.name;
        save.disabled = true;
        tr.classList.remove("dirty");
        tr.classList.add("saved");
        setTimeout(() => tr.classList.remove("saved"), 1200);
      } catch (err) {
        reportError(err);
      }
    };
    save.addEventListener("click", doSave);
    input.addEventListener("keydown", (e) => e.key === "Enter" && doSave());
    const remove = el("button", {
      class: "btn btn-small btn-danger",
      type: "button",
      text: "삭제",
      onclick: async () => {
        if (!confirm(`'${a.name}' 을(를) 아파트 목록에서 삭제하시겠습니까?\n(대화 데이터는 지워지지 않으며, 다시 수집하면 다시 추가됩니다)`)) return;
        await send("DELETE", `/api/admin/apartments/${a.id}`).catch(reportError);
        await loadApartments();
      },
    });
    tr.append(
      el("td", { class: "apt-name-cell" }, input),
      el("td", {}, KC.roomChip(a.room_name, a.messages > 0)),
      el("td", {}, el("button", { class: `vendor-set-btn${a.custom ? " has" : ""}`, type: "button", onclick: () => openDrawer(a).catch(reportError) },
        el("span", { class: "ms", "aria-hidden": "true", text: "domain" }),
        a.custom ? `업체 ${a.assigned}곳 지정${a.custom > a.assigned ? ` · 보내지 않음 ${a.custom - a.assigned}` : ""}` : "기본 업체 사용",
        el("span", { class: "ms vendor-set-go", "aria-hidden": "true", text: "settings" }))),
      el("td", { class: "num", text: a.messages.toLocaleString("ko-KR") }),
      el("td", { class: "num" }, el("span", { class: `count-pill${a.complaints ? " has" : ""}`, text: `${a.complaints}건` })),
      el("td", { class: "muted small", text: a.last_at ? fmtDateTime(a.last_at).slice(0, 16) : "대화 없음" }),
      el("td", { class: "right-col" }, el("div", { class: "row-actions" }, save, remove)),
    );
    return tr;
  }

  async function sync() {
    const btn = $("apt-sync");
    btn.disabled = true;
    try {
      const r = await send("POST", "/api/admin/apartments/sync");
      const box = $("apt-result");
      box.hidden = false;
      box.replaceChildren(
        el("span", { class: "ms", "aria-hidden": "true", text: r.added.length ? "new_releases" : "check_circle" }),
        r.added.length
          ? `새 아파트 ${r.added.length}곳을 추가했습니다: ${r.added.map((a) => a.name).join(", ")}`
          : `새로 추가할 아파트가 없습니다. (채팅방 ${r.rooms}개 중 별 하나로 시작하는 방 ${r.matched}개, 모두 등록됨)`,
      );
      box.classList.toggle("has-new", r.added.length > 0);
      await loadApartments();
    } finally {
      btn.disabled = false;
    }
  }

  // ---- 아파트별 담당 업체 (오른쪽 창) ------------------------------------------------------------

  const drawer = { apt: null, rows: [], vendors: [], initial: "" };

  async function openDrawer(a) {
    const data = await api(`/api/admin/apartments/${a.id}/vendors`);
    drawer.apt = a;
    drawer.vendors = data.vendors;
    drawer.rows = data.categories.map((r) => ({ ...r, value: r.mode === "vendor" ? `v:${r.vendor_id}` : r.mode }));
    drawer.initial = JSON.stringify(drawer.rows.map((r) => r.value));
    $("av-title").textContent = a.name;
    $("av-room").replaceChildren(KC.roomChip(a.room_name, a.messages > 0));
    renderDrawer();
    $("av-scrim").hidden = false;
    $("av-drawer").hidden = false;
    requestAnimationFrame(() => $("av-drawer").classList.add("open"));
    $("av-list").querySelector("select")?.focus();
  }

  function closeDrawer(force = false) {
    if (!force && dirtyCount() && !confirm("저장하지 않은 변경이 있습니다. 닫으시겠습니까?")) return;
    $("av-drawer").classList.remove("open");
    $("av-scrim").hidden = true;
    setTimeout(() => ($("av-drawer").hidden = true), 180);
  }

  function dirtyCount() {
    if (!drawer.apt) return 0;
    const init = JSON.parse(drawer.initial);
    return drawer.rows.filter((r, i) => r.value !== init[i]).length;
  }

  function vendorById(id) {
    return drawer.vendors.find((v) => v.id === id);
  }

  /** 고른 값에 따라 실제로 처리 요청이 갈 곳 */
  function target(row) {
    if (row.value === "none") return el("span", { class: "av-target none" }, el("span", { class: "ms", text: "block" }), "보내지 않음");
    const list = row.value === "default" ? row.defaults : [vendorById(Number(row.value.slice(2)))].filter(Boolean);
    if (!list.length) return el("span", { class: "av-target none" }, el("span", { class: "ms", text: "info" }), "기본 업체 없음 — 보내지 않음");
    return el(
      "div",
      { class: "av-targets" },
      ...list.map((v) =>
        v.room_name
          ? KC.roomChip(v.room_name, drawer.vendors.find((x) => x.id === v.id)?.has_messages ?? true)
          : el("span", { class: "av-target warn" }, el("span", { class: "ms", text: "warning" }), `${v.name}: 톡방 미지정`),
      ),
    );
  }

  function renderDrawer() {
    const list = $("av-list");
    list.replaceChildren();
    let slot = 0;
    for (const row of drawer.rows) {
      const color = row.protected ? 0 : ++slot > 8 ? 0 : slot;
      const select = el("select", { class: "av-select", "aria-label": `${row.category} 담당 업체` });
      const defaultNames = row.defaults.map((d) => d.name).join(", ");
      select.append(
        el("option", { value: "default", text: defaultNames ? `기본 업체 따름 (${defaultNames})` : "기본 업체 따름 (없음)" }),
        el("option", { value: "none", text: "이 아파트는 보내지 않음" }),
      );
      const group = el("optgroup", { label: "업체 지정" });
      for (const v of drawer.vendors) group.append(el("option", { value: `v:${v.id}`, text: v.room_name ? `${v.name}  ·  ${v.room_name}` : `${v.name}  (톡방 없음)` }));
      select.append(group);
      select.value = row.value;
      const targetCell = el("div", { class: "av-target-cell" }, target(row));
      const li = el(
        "li",
        { class: `av-row${row.enabled ? "" : " off"}${row.value !== "default" ? " custom" : ""}` },
        el("div", { class: "av-cat" }, el("span", { class: `dot c${color}`, "aria-hidden": "true" }), el("strong", { text: row.category }), row.enabled ? null : el("small", { text: "분류 사용 안 함" })),
        select,
        targetCell,
      );
      select.addEventListener("change", () => {
        row.value = select.value;
        targetCell.replaceChildren(target(row));
        li.classList.toggle("custom", row.value !== "default");
        updateDirty();
      });
      list.append(li);
    }
    updateDirty();
  }

  function updateDirty() {
    const n = dirtyCount();
    $("av-dirty").textContent = n ? `변경 ${n}건` : "";
    $("av-save").disabled = n === 0;
  }

  async function saveDrawer() {
    const assignments = drawer.rows.map((r) =>
      r.value.startsWith("v:") ? { category_id: r.category_id, mode: "vendor", vendor_id: Number(r.value.slice(2)) } : { category_id: r.category_id, mode: r.value },
    );
    $("av-save").disabled = true;
    try {
      await send("PUT", `/api/admin/apartments/${drawer.apt.id}/vendors`, { assignments });
      drawer.initial = JSON.stringify(drawer.rows.map((r) => r.value));
      closeDrawer(true);
      await loadApartments();
    } catch (err) {
      reportError(err);
      updateDirty();
    }
  }

  KC.loaders = Object.assign(KC.loaders || {}, { apartments: loadApartments });
  $("av-close").addEventListener("click", () => closeDrawer());
  $("av-cancel").addEventListener("click", () => closeDrawer());
  $("av-scrim").addEventListener("click", () => closeDrawer());
  $("av-save").addEventListener("click", () => saveDrawer().catch(reportError));
  // 창 안의 톡방 이름을 누르면 창을 닫고 그 방 대화로 이동 (저장 안 한 변경이 있으면 확인)
  $("av-drawer").addEventListener(
    "click",
    (e) => {
      if (!e.target.closest("button.room-chip")) return;
      if (dirtyCount() && !confirm("저장하지 않은 변경이 있습니다. 대화 화면으로 이동할까요?")) {
        e.stopPropagation();
        e.preventDefault();
        return;
      }
      closeDrawer(true);
    },
    true,
  );
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("av-drawer").hidden) closeDrawer();
  });
  $("apt-sync").addEventListener("click", () => sync().catch(reportError));
  $("apt-search").addEventListener("input", render);
})();
