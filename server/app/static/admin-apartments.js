"use strict";

// '건물 관리' 탭: 건물 목록 등록 · 채팅방 자동/직접 매핑 · 건물별 담당 업체. 서버 데이터는 텍스트 노드로만 넣는다(innerHTML 금지).
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

  let filter = "all";

  async function loadApartments() {
    const data = await api("/api/admin/apartments");
    items = data.items;
    const listed = items.filter((a) => a.listed);
    const linked = listed.filter((a) => a.room_name).length;
    const extra = items.length - listed.length;
    $("apt-kpis").replaceChildren(
      kpi("apartment", "blue", "등록 건물", listed.length, "곳"),
      kpi("link", "green", "채팅방 연결", linked, `/ ${listed.length}`),
      kpi("do_not_disturb_alt", "amber", "미연결 건물", listed.length - linked, "곳"),
      kpi("forum", "slate", "목록 외 채팅방", extra, "개"),
    );
    render();
  }

  const FILTERS = {
    all: () => true,
    linked: (a) => a.listed && a.room_name,
    unlinked: (a) => a.listed && !a.room_name,
    extra: (a) => !a.listed,
  };

  // ---- 열 제목 정렬 (누를 때마다 오름차순 ↔ 내림차순, 값이 없는 행은 항상 아래) -----------------------
  const sort = { key: null, dir: 1 };
  const SORT_KEYS = {
    name: (a) => a.name,
    room: (a) => a.room_name || null,
    vendor: (a) => (a.custom ? a.assigned * 1000 + a.custom : null),
    messages: (a) => (a.room_name ? a.messages : null),
    complaints: (a) => (a.room_name ? a.complaints : null),
    last: (a) => a.last_at || null,
  };
  const collator = new Intl.Collator("ko", { numeric: true, sensitivity: "base" });

  function sortItems(list) {
    if (!sort.key) return list;
    const get = SORT_KEYS[sort.key];
    return list
      .map((a, i) => ({ a, i, v: get(a) }))
      .sort((x, y) => {
        if (x.v === null || y.v === null) return x.v === y.v ? x.i - y.i : x.v === null ? 1 : -1;
        const c = typeof x.v === "number" ? x.v - y.v : collator.compare(x.v, y.v);
        return c ? c * sort.dir : x.i - y.i;
      })
      .map((x) => x.a);
  }

  function setSort(key) {
    if (sort.key === key) sort.dir = -sort.dir;
    else Object.assign(sort, { key, dir: ["messages", "complaints", "last"].includes(key) ? -1 : 1 }); // 숫자·날짜는 큰 값부터
    for (const b of document.querySelectorAll(".apt-table .th-sort")) {
      const on = b.dataset.sort === sort.key;
      b.closest("th").setAttribute("aria-sort", on ? (sort.dir > 0 ? "ascending" : "descending") : "none");
      b.querySelector(".sort-ind").textContent = on ? (sort.dir > 0 ? "▲" : "▼") : "";
    }
    render();
  }

  function render() {
    const q = $("apt-search").value.trim().toLowerCase();
    const hit = (a) => !q || [a.name, a.address, a.room_name].some((v) => (v || "").toLowerCase().includes(q));
    const shown = sortItems(items.filter((a) => FILTERS[filter](a) && hit(a)));
    $("apt-count").textContent = shown.length === items.length ? `${items.length}곳` : `${shown.length} / ${items.length}곳`;
    const list = $("apt-list");
    list.replaceChildren();
    if (!shown.length) {
      const msg = items.length ? "조건에 맞는 항목이 없습니다." : "아직 등록된 건물이 없습니다. 오른쪽 위 '건물 추가'로 등록하십시오.";
      list.append(el("tr", {}, el("td", { colspan: "7", class: "empty-cell", text: msg })));
      return;
    }
    for (const a of shown) {
      list.append(row(a));
      if (acc.id === a.id && acc.detail) list.append(acc.detail); // 다시 그려도 펼친 설정(입력 중인 값 포함) 유지
    }
  }

  function badge(a) {
    if (!a.listed) return el("span", { class: "map-badge extra", title: "건물 목록에 없는 별(★) 채팅방", text: "목록 외" });
    if (!a.room_name) return el("span", { class: "map-badge none", text: "미연결" });
    if (a.room_mode === "manual") return el("span", { class: "map-badge manual", text: "직접 연결" });
    return el("span", { class: "map-badge auto", text: "자동 연결" });
  }

  /**
   * 연결 편집: 목록 건물 → 채팅방 고르기, 목록 외 방 → 건물 고르기.
   * 검색 칸 + 목록. 이름이 비슷한 순으로 위에 오고, 이미 다른 곳에 연결된 항목은 아래에 둔다.
   */
  async function openLinker(a, cell) {
    const data = await api(`/api/admin/apartments/${a.id}/candidates`);
    const rooms = data.kind === "rooms";
    // { value, label, note, linked }
    let options = rooms
      ? data.items.filter((r) => !r.current).map((r) => ({ value: r.room_name, label: r.room_name, note: r.linked_to ? `→ ${r.linked_to}` : "", linked: Boolean(r.linked_to), score: r.score }))
      : data.items.map((b) => ({ value: String(b.id), label: b.name, note: b.linked_room ? `현재: ${b.linked_room}` : "", linked: Boolean(b.linked_room), score: b.score }));
    options.sort((x, y) => x.linked - y.linked); // 서버가 비슷한 순으로 보냄. 이미 다른 곳에 연결된 것만 아래로
    if (rooms && a.room_name) options = [{ value: "", label: "— 연결 해제 —", note: "", linked: false, score: 0, unlink: true }, ...options];

    const search = el("input", { type: "search", class: "link-search", placeholder: rooms ? "채팅방 이름 검색" : "건물 이름 검색", "aria-label": "검색", autocomplete: "off" });
    const list = el("select", { class: "link-list", size: "8", "aria-label": rooms ? `${a.name} 에 연결할 채팅방` : `${a.room_name} 을 붙일 건물` });
    const count = el("span", { class: "muted small link-count" });
    const fill = () => {
      const q = search.value.trim().toLowerCase().replace(/\s+/g, "");
      const shown = options.filter((o) => o.unlink || !q || o.label.toLowerCase().replace(/\s+/g, "").includes(q));
      list.replaceChildren(...shown.map((o) => el("option", { value: o.value, text: o.note ? `${o.label}   (${o.note})` : o.label })));
      const real = shown.filter((o) => !o.unlink).length;
      count.textContent = q ? `${real}개 찾음` : `${real}개 · 비슷한 이름 순`;
      if (list.options.length) list.selectedIndex = shown[0]?.unlink && shown.length > 1 ? 1 : 0;
    };
    fill();

    const ok = el("button", { class: "btn btn-small btn-primary", type: "button", text: rooms ? "연결" : "지정" });
    const cancel = el("button", { class: "btn btn-small", type: "button", text: "취소", onclick: () => render() });
    const confirmPick = async () => {
      if (list.selectedIndex < 0) return reportError(new Error(rooms ? "연결할 채팅방을 고르십시오." : "붙일 건물을 고르십시오."));
      const v = list.value;
      try {
        if (rooms) {
          if (!v && !confirm(`'${a.name}' 의 채팅방 연결을 해제하시겠습니까?\n(직접 해제한 건물은 자동 매핑이 다시 연결하지 않습니다)`)) return;
          await send("PUT", `/api/admin/apartments/${a.id}/room`, { room_name: v || null });
        } else {
          await send("PUT", `/api/admin/apartments/${v}/room`, { room_name: a.room_name });
        }
        await loadApartments();
      } catch (err) {
        reportError(err);
      }
    };
    ok.addEventListener("click", confirmPick);
    list.addEventListener("dblclick", confirmPick);
    search.addEventListener("input", fill);
    search.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        list.focus();
      } else if (e.key === "Enter") {
        e.preventDefault();
        confirmPick();
      } else if (e.key === "Escape") render();
    });
    list.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        confirmPick();
      } else if (e.key === "Escape") render();
    });
    cell.replaceChildren(el("div", { class: "link-edit" }, el("div", { class: "link-pick" }, el("div", { class: "link-search-row" }, search, count), list), el("div", { class: "link-actions" }, ok, cancel)));
    search.focus();
  }

  function row(a) {
    const input = el("input", { class: "apt-name", value: a.name, maxlength: "100", "aria-label": `${a.name} 건물 이름` });
    const save = el("button", { class: "btn btn-small btn-primary", type: "button", text: "저장", disabled: true });
    const tr = el("tr", { class: `${acc.id === a.id ? "acc-open" : ""}${a.listed ? "" : " extra-row"}`, "data-apt": a.id });
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
        const note = a.listed ? "(대화 데이터는 지워지지 않습니다)" : "(대화 데이터는 지워지지 않으며, 다시 수집하면 다시 보입니다)";
        if (!confirm(`'${a.name}' 을(를) 목록에서 삭제하시겠습니까?\n${note}`)) return;
        await send("DELETE", `/api/admin/apartments/${a.id}`).catch(reportError);
        await loadApartments();
      },
    });
    const roomCell = el("td", {});
    const linkBtn = el("button", {
      class: "link-btn", type: "button",
      text: a.listed ? (a.room_name ? "변경" : "채팅방 연결") : "건물 지정",
      onclick: () => openLinker(a, roomCell).catch(reportError),
    });
    roomCell.append(el("div", { class: "apt-room" }, a.room_name ? KC.roomChip(a.room_name, a.messages > 0) : null, badge(a), linkBtn));
    const hasRoom = Boolean(a.room_name);
    tr.append(
      el("td", { class: "apt-name-cell" }, input, a.address ? el("span", { class: "apt-addr", title: a.address, text: a.address }) : null),
      roomCell,
      el("td", {}, el("button", {
          class: `vendor-set-btn${a.custom ? " has" : ""}`, type: "button", "aria-expanded": String(acc.id === a.id), "aria-controls": `apt-acc-${a.id}`,
          onclick: () => toggleAccordion(a, tr).catch(reportError),
        },
        el("span", { class: "ms", "aria-hidden": "true", text: "domain" }),
        a.custom ? `업체 ${a.assigned}곳 지정${a.custom > a.assigned ? ` · 보내지 않음 ${a.custom - a.assigned}` : ""}` : "기본 업체 사용",
        el("span", { class: "ms vendor-set-go", "aria-hidden": "true", text: "expand_more" }))),
      el("td", { class: "num", text: hasRoom ? a.messages.toLocaleString("ko-KR") : "–" }),
      el("td", { class: "num" }, hasRoom ? el("span", { class: `count-pill${a.complaints ? " has" : ""}`, text: `${a.complaints}건` }) : "–"),
      el("td", { class: "muted small", text: a.last_at ? fmtDateTime(a.last_at).slice(0, 16) : hasRoom ? "대화 없음" : "–" }),
      el("td", { class: "right-col" }, el("div", { class: "row-actions" }, save, remove)),
    );
    return tr;
  }

  function showResult(lines, details) {
    const box = $("apt-result");
    box.hidden = false;
    box.classList.toggle("has-new", details.some(([, list]) => list.length));
    box.replaceChildren(el("span", { class: "ms", "aria-hidden": "true", text: "check_circle" }), lines.join(" · "));
    for (const [title, list] of details) {
      if (!list.length) continue;
      box.append(el("details", {}, el("summary", { text: `${title} ${list.length}건 보기` }), el("ul", {}, ...list.map((t) => el("li", { text: t })))));
    }
  }

  function summarize(r, head = []) {
    const mapped = r.mapped.map((m) => `${m.room_name} → ${m.name}`);
    const added = r.added.map((a) => a.room_name);
    showResult(
      [...head, `자동 연결 ${r.mapped.length}건`, `목록 외 채팅방 추가 ${r.added.length}건`, `수집된 채팅방 ${r.rooms}개`],
      [["자동 연결", mapped], ["목록 외 채팅방", added]],
    );
  }

  async function sync() {
    const btn = $("apt-sync");
    btn.disabled = true;
    try {
      summarize(await send("POST", "/api/admin/apartments/sync"));
      await loadApartments();
    } finally {
      btn.disabled = false;
    }
  }

  // ---- 건물 추가 (한 곳씩) ---------------------------------------------------------------------

  function toggleAdd(open) {
    $("apt-add").hidden = !open;
    $("apt-add-open").setAttribute("aria-expanded", String(open));
    if (open) $("apt-add-name").focus();
  }

  async function addBuilding(e) {
    e.preventDefault();
    const name = $("apt-add-name").value.trim();
    if (!name) return;
    const btn = $("apt-add-run");
    btn.disabled = true;
    try {
      const r = await send("POST", "/api/admin/apartments", { name, address: $("apt-add-address").value });
      const mine = r.mapped.find((m) => m.name === name);
      showResult([`'${name}' 을(를) 추가했습니다`, mine ? `채팅방 '${mine.room_name}' 에 자동 연결` : "비슷한 채팅방이 없어 미연결로 등록"], []);
      $("apt-add-name").value = "";
      $("apt-add-address").value = "";
      await loadApartments();
      $("apt-add-name").focus();
    } catch (err) {
      reportError(err);
    } finally {
      btn.disabled = !$("apt-add-name").value.trim();
    }
  }

  // ---- 건물별 담당 업체 (행 아래로 펼치는 아코디언, 한 번에 하나) ------------------------------------

  const acc = { id: null, apt: null, rows: [], vendors: [], initial: "", detail: null };

  function dirtyCount() {
    if (!acc.id) return 0;
    const init = JSON.parse(acc.initial);
    return acc.rows.filter((r, i) => r.value !== init[i]).length;
  }

  function closeAccordion(force = false) {
    if (!acc.id) return true;
    if (!force && dirtyCount() && !confirm(`'${acc.apt.name}' 의 업체 설정에 저장하지 않은 변경이 있습니다. 닫으시겠습니까?`)) return false;
    acc.detail?.remove();
    document.querySelector(`#apt-list tr[data-apt="${acc.id}"]`)?.classList.remove("acc-open");
    document.querySelector(`#apt-list tr[data-apt="${acc.id}"] .vendor-set-btn`)?.setAttribute("aria-expanded", "false");
    Object.assign(acc, { id: null, apt: null, rows: [], vendors: [], initial: "", detail: null });
    return true;
  }

  async function toggleAccordion(a, tr) {
    if (acc.id === a.id) {
      closeAccordion();
      return;
    }
    if (!closeAccordion()) return;
    const data = await api(`/api/admin/apartments/${a.id}/vendors`);
    Object.assign(acc, {
      id: a.id,
      apt: a,
      vendors: data.vendors,
      rows: data.categories.map((r) => ({ ...r, value: r.mode === "vendor" ? `v:${r.vendor_id}` : r.mode })),
    });
    acc.initial = JSON.stringify(acc.rows.map((r) => r.value));
    acc.detail = buildPanel();
    tr.after(acc.detail);
    tr.classList.add("acc-open");
    tr.querySelector(".vendor-set-btn")?.setAttribute("aria-expanded", "true");
    acc.detail.querySelector("select")?.focus({ preventScroll: true });
    acc.detail.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }

  function vendorById(id) {
    return acc.vendors.find((v) => v.id === id);
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
          ? KC.roomChip(v.room_name, acc.vendors.find((x) => x.id === v.id)?.has_messages ?? true)
          : el("span", { class: "av-target warn" }, el("span", { class: "ms", text: "warning" }), `${v.name}: 톡방 미지정`),
      ),
    );
  }

  function buildPanel() {
    const dirty = el("span", { class: "muted small acc-dirty" });
    const save = el("button", { class: "btn btn-primary btn-small", type: "button", text: "저장", disabled: true });
    const cancel = el("button", { class: "btn btn-small", type: "button", text: "닫기" });
    const updateDirty = () => {
      const n = dirtyCount();
      dirty.textContent = n ? `변경 ${n}건` : "";
      save.disabled = n === 0;
      cancel.textContent = n ? "취소" : "닫기";
    };
    const list = el("ul", { class: "acc-list" });
    let slot = 0;
    for (const row of acc.rows) {
      const color = row.protected ? 0 : ++slot > 8 ? 0 : slot;
      const select = el("select", { class: "av-select", "aria-label": `${row.category} 담당 업체` });
      const defaultNames = row.defaults.map((d) => d.name).join(", ");
      select.append(
        el("option", { value: "default", text: defaultNames ? `기본 업체 따름 (${defaultNames})` : "기본 업체 따름 (없음)" }),
        el("option", { value: "none", text: "이 건물은 보내지 않음" }),
      );
      const group = el("optgroup", { label: "업체 지정" });
      for (const v of acc.vendors) group.append(el("option", { value: `v:${v.id}`, text: v.room_name ? `${v.name}  ·  ${v.room_name}` : `${v.name}  (톡방 없음)` }));
      select.append(group);
      select.value = row.value;
      const targetCell = el("div", { class: "av-target-cell" }, target(row));
      const li = el(
        "li",
        { class: `av-row${row.enabled ? "" : " off"}${row.value !== "default" ? " custom" : ""}` },
        el("div", { class: "av-cat" }, el("span", { class: `dot c${color}`, "aria-hidden": "true" }), el("strong", { text: row.category }), row.enabled ? null : el("small", { text: "사용 안 함" })),
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
    save.addEventListener("click", async () => {
      const assignments = acc.rows.map((r) =>
        r.value.startsWith("v:") ? { category_id: r.category_id, mode: "vendor", vendor_id: Number(r.value.slice(2)) } : { category_id: r.category_id, mode: r.value },
      );
      save.disabled = true;
      try {
        await send("PUT", `/api/admin/apartments/${acc.id}/vendors`, { assignments });
        const id = acc.id;
        closeAccordion(true);
        await loadApartments();
        const tr = document.querySelector(`#apt-list tr[data-apt="${id}"]`);
        tr?.classList.add("saved");
        setTimeout(() => tr?.classList.remove("saved"), 1200);
      } catch (err) {
        reportError(err);
        updateDirty();
      }
    });
    cancel.addEventListener("click", () => closeAccordion());
    const panel = el(
      "div",
      { class: "acc-panel", id: `apt-acc-${acc.id}` },
      el(
        "div",
        { class: "acc-head" },
        el("span", { class: "ms", "aria-hidden": "true", text: "domain" }),
        el("div", {}, el("strong", { text: `${acc.apt.name} · 민원 분류별 담당 업체` }), el("p", { text: "분류마다 이 건물의 민원을 처리할 업체를 고르십시오. 고른 업체의 카톡방으로 처리 요청이 갑니다. '기본 업체'는 민원처리업체 관리에서 정한 담당 업체입니다." })),
      ),
      list,
      el("div", { class: "acc-foot" }, dirty, cancel, save),
    );
    // 패널 안의 톡방 이름을 누르면 대화 화면으로 이동 (저장 안 한 변경이 있으면 확인)
    panel.addEventListener(
      "click",
      (e) => {
        if (!e.target.closest("button.room-chip")) return;
        if (dirtyCount() && !confirm("저장하지 않은 변경이 있습니다. 대화 화면으로 이동할까요?")) {
          e.stopPropagation();
          e.preventDefault();
          return;
        }
        closeAccordion(true);
      },
      true,
    );
    panel.addEventListener("keydown", (e) => e.key === "Escape" && closeAccordion());
    return el("tr", { class: "apt-detail" }, el("td", { colspan: "7" }, panel));
  }

  KC.loaders = Object.assign(KC.loaders || {}, { apartments: loadApartments });

  $("apt-sync").addEventListener("click", () => sync().catch(reportError));
  $("apt-search").addEventListener("input", render);
  for (const b of document.querySelectorAll(".apt-filter .seg-btn")) {
    b.addEventListener("click", () => {
      filter = b.dataset.filter;
      for (const x of document.querySelectorAll(".apt-filter .seg-btn")) x.classList.toggle("on", x === b);
      render();
    });
  }
  for (const b of document.querySelectorAll(".apt-table .th-sort")) b.addEventListener("click", () => setSort(b.dataset.sort));
  $("apt-add-open").addEventListener("click", () => toggleAdd($("apt-add").hidden));
  $("apt-add-cancel").addEventListener("click", () => toggleAdd(false));
  $("apt-add-name").addEventListener("input", () => ($("apt-add-run").disabled = !$("apt-add-name").value.trim()));
  $("apt-add").addEventListener("submit", (e) => addBuilding(e).catch(reportError));
  $("apt-add").addEventListener("keydown", (e) => e.key === "Escape" && toggleAdd(false));
})();
