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
      list.append(el("tr", {}, el("td", { colspan: "6", class: "empty-cell", text: items.length ? "검색 결과가 없습니다." : "아직 등록된 아파트가 없습니다. 오른쪽 위 '다시 수집하기'를 누르십시오." })));
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
    const view = el("button", { class: "btn btn-small", type: "button", onclick: () => KC.openRoom(a.room_name), disabled: !a.messages }, el("span", { class: "ms", text: "forum" }), "대화 보기");
    tr.append(
      el("td", { class: "apt-name-cell" }, input),
      el("td", {}, el("span", { class: "room-chip", title: "원래 카톡 채팅방 이름" }, el("span", { class: "ms", "aria-hidden": "true", text: "chat" }), a.room_name)),
      el("td", { class: "num", text: a.messages.toLocaleString("ko-KR") }),
      el("td", { class: "num" }, el("span", { class: `count-pill${a.complaints ? " has" : ""}`, text: `${a.complaints}건` })),
      el("td", { class: "muted small", text: a.last_at ? fmtDateTime(a.last_at).slice(0, 16) : "대화 없음" }),
      el("td", { class: "right-col" }, el("div", { class: "row-actions" }, save, view, remove)),
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

  KC.loaders = Object.assign(KC.loaders || {}, { apartments: loadApartments });
  $("apt-sync").addEventListener("click", () => sync().catch(reportError));
  $("apt-search").addEventListener("input", render);
})();
