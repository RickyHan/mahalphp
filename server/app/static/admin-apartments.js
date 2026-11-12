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
    renderUnlinked();
  }

  /** ★ 방인데 목록의 건물에 연결되지 않은 톡방 (목록 외 채팅방) */
  function renderUnlinked() {
    const rows = items.filter((a) => !a.listed && a.room_name).sort((x, y) => (y.last_at || "").localeCompare(x.last_at || ""));
    $("apt-unlinked-card").hidden = !rows.length;
    $("apt-unlinked-count").textContent = `${rows.length}개`;
    const body = $("apt-unlinked");
    body.replaceChildren();
    for (const a of rows) {
      const cell = el("td", {});
      const pick = el("button", { class: "btn btn-small btn-primary", type: "button", text: "건물 지정", onclick: () => openLinker(a, cell).catch(reportError) });
      cell.append(pick);
      body.append(
        el(
          "tr",
          {},
          el("td", {}, KC.roomChip(a.room_name, a.messages > 0)),
          el("td", { class: "num", text: a.messages.toLocaleString("ko-KR") }),
          el("td", { class: "muted small", text: a.last_at ? fmtDateTime(a.last_at).slice(5, 16) : "–" }),
          cell,
        ),
      );
    }
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
    const hit = (a) => !q || [a.name, a.address, a.room_name, ...(a.extra_rooms || [])].some((v) => (v || "").toLowerCase().includes(q));
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
  /** add=true: 건물에 채팅방을 '추가로' 연결 (기존 채팅방은 그대로) */
  async function openLinker(a, cell, add = false) {
    const data = await api(`/api/admin/apartments/${a.id}/candidates`);
    const rooms = data.kind === "rooms";
    // { value, label, note, linked }
    let options = rooms
      ? data.items.filter((r) => !r.current).map((r) => ({ value: r.room_name, label: r.room_name, note: r.linked_to ? `→ ${r.linked_to}` : "", linked: Boolean(r.linked_to), score: r.score }))
      : data.items.map((b) => ({
          value: String(b.id), label: b.name, score: b.score, linked: false, // 건물 고르기: 이미 방이 있어도 '추가로' 붙임
          note: b.linked_room ? `연결됨: ${b.linked_room}${b.extra_rooms ? ` 외 ${b.extra_rooms}` : ""} · 추가로 연결` : "",
        }));
    if (add) options = options.filter((o) => !o.linked); // 추가 연결: 다른 건물에 연결된 방은 제외
    options.sort((x, y) => x.linked - y.linked); // 서버가 비슷한 순으로 보냄. 이미 다른 곳에 연결된 것만 아래로
    if (rooms && a.room_name && !add) options = [{ value: "", label: "— 연결 해제 —", note: "", linked: false, score: 0, unlink: true }, ...options];

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

    const ok = el("button", { class: "btn btn-small btn-primary", type: "button", text: add ? "추가" : rooms ? "연결" : "지정" });
    const reset = () => (render(), renderUnlinked());
    const cancel = el("button", { class: "btn btn-small", type: "button", text: "취소", onclick: reset });
    const confirmPick = async () => {
      if (list.selectedIndex < 0) return reportError(new Error(rooms ? "연결할 채팅방을 고르십시오." : "붙일 건물을 고르십시오."));
      const v = list.value;
      try {
        if (rooms && add) {
          await send("POST", `/api/admin/apartments/${a.id}/rooms`, { room_name: v });
        } else if (rooms) {
          if (!v && !confirm(`'${a.name}' 의 채팅방 연결을 해제하시겠습니까?\n(직접 해제한 건물은 자동 매핑이 다시 연결하지 않습니다)`)) return;
          await send("PUT", `/api/admin/apartments/${a.id}/room`, { room_name: v || null });
        } else {
          // 목록 외 채팅방을 건물에 붙임: 건물에 이미 채팅방이 있으면 추가로(상가방 등), 없으면 기본 채팅방으로
          await send("POST", `/api/admin/apartments/${v}/rooms`, { room_name: a.room_name });
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
      } else if (e.key === "Escape") reset();
    });
    list.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        confirmPick();
      } else if (e.key === "Escape") reset();
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
    const extras = (a.extra_rooms || []).map((room) =>
      el(
        "span",
        { class: "extra-room" },
        KC.roomChip(room, true),
        el("button", {
          class: "extra-x", type: "button", title: "이 채팅방 연결 해제", "aria-label": `${room} 연결 해제`,
          onclick: async () => {
            if (!confirm(`'${a.name}' 에서 '${room}' 연결을 해제하시겠습니까?`)) return;
            await send("DELETE", `/api/admin/apartments/${a.id}/rooms?room_name=${encodeURIComponent(room)}`).catch(reportError);
            await loadApartments();
          },
        }, "×"),
      ),
    );
    const addBtn = a.listed && a.room_name
      ? el("button", { class: "link-btn", type: "button", text: "+ 방 추가", title: "세대방 외 상가방 등 채팅방을 더 연결", onclick: () => openLinker(a, roomCell, true).catch(reportError) })
      : null;
    roomCell.append(el("div", { class: "apt-room" }, a.room_name ? KC.roomChip(a.room_name, a.messages > 0) : null, ...extras, badge(a), linkBtn, addBtn));
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

  /** 고른 값에 따라 실제로 처리 요청이 갈 곳: 문자(건물×업체 담당자 → 연락처가 한 명뿐인 업체) 우선, 없으면 카톡방 */
  const refreshers = new Set(); // 담당자 번호를 바꾸면 같은 업체를 쓰는 다른 분류 칸도 다시 그린다
  function smsTargets(v) {
    if (v.contacts?.length) return { phones: v.contacts.map((c) => c.phone), from: "이 건물 담당자" };
    if (v.directory?.length === 1) return { phones: [v.directory[0].phone], from: "업체 대표 번호" };
    return { phones: [], from: "" };
  }

  function contactEditor(v, done) {
    const listId = `apv-dir-${v.id}`;
    const rows = (v.contacts?.length ? v.contacts : [{ phone: "", name: "" }]).map((c) => ({ ...c }));
    const box = el("div", { class: "av-contact-edit" });
    const draw = () => {
      box.replaceChildren(
        el("datalist", { id: listId }, ...(v.directory || []).map((c) => el("option", { value: c.phone, text: [c.device, c.region].filter(Boolean).join(" · ") }))),
        el("div", { class: "muted small", text: `${v.name} · 이 건물에서 문자 받을 담당자 (비우면 카톡방으로)` }),
        ...rows.map((r, i) => {
          const phone = el("input", { type: "tel", value: r.phone, list: listId, placeholder: "010-0000-0000", "aria-label": "담당자 번호" });
          const name = el("input", { type: "text", value: r.name, maxlength: "100", placeholder: "이름·메모 (선택)", "aria-label": "담당자 이름" });
          phone.addEventListener("input", () => (r.phone = phone.value));
          name.addEventListener("input", () => (r.name = name.value));
          const del = el("button", { class: "btn btn-small", type: "button", text: "삭제", onclick: () => (rows.splice(i, 1), draw()) });
          return el("div", { class: "av-contact-row" }, phone, name, del);
        }),
        el(
          "div",
          { class: "row-actions" },
          el("button", { class: "btn btn-small", type: "button", text: "+ 담당자", onclick: () => (rows.push({ phone: "", name: "" }), draw()) }),
          el("button", {
            class: "btn btn-small btn-primary",
            type: "button",
            text: "번호 저장",
            onclick: async () => {
              try {
                const r = await send("PUT", `/api/admin/apartments/${acc.id}/vendor-contacts/${v.id}`, { contacts: rows.filter((x) => x.phone.trim()) });
                v.contacts = r.contacts;
                done();
                refreshers.forEach((f) => f());
              } catch (err) {
                reportError(err);
              }
            },
          }),
          el("button", { class: "btn btn-small", type: "button", text: "취소", onclick: done }),
        ),
      );
    };
    draw();
    return box;
  }

  function target(row, cell) {
    if (row.value === "none") return el("span", { class: "av-target none" }, el("span", { class: "ms", text: "block" }), "보내지 않음");
    const list = (row.value === "default" ? row.defaults.map((d) => vendorById(d.id) || d) : [vendorById(Number(row.value.slice(2)))]).filter(Boolean);
    if (!list.length) return el("span", { class: "av-target none" }, el("span", { class: "ms", text: "info" }), "기본 업체 없음 — 보내지 않음");
    return el(
      "div",
      { class: "av-targets" },
      ...list.map((v) => {
        const { phones, from } = smsTargets(v);
        const edit = el("button", {
          class: "link-btn",
          type: "button",
          text: v.contacts?.length ? "담당자 변경" : "문자 담당자 지정",
          onclick: () => cell.replaceChildren(contactEditor(v, () => cell.replaceChildren(target(row, cell)))),
        });
        const sms = phones.length
          ? el("span", { class: "av-target sms", title: `${from} — 문자를 먼저 보내고, 실패하면 카톡방으로` }, el("span", { class: "ms", text: "sms" }), `문자 ${phones.join(", ")}`)
          : null;
        const kakao = v.room_name
          ? KC.roomChip(v.room_name, acc.vendors.find((x) => x.id === v.id)?.has_messages ?? true)
          : phones.length
            ? null
            : el("span", { class: "av-target warn" }, el("span", { class: "ms", text: "warning" }), `${v.name}: 번호·톡방 없음`);
        return el("div", { class: `av-channel${phones.length ? " has-sms" : ""}` }, sms, kakao, edit);
      }),
    );
  }

  function buildPanel() {
    refreshers.clear();
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
        el("option", { value: "default", "data-keep": "", text: defaultNames ? `기본 업체 따름 (${defaultNames})` : "기본 업체 따름 (없음)" }),
        el("option", { value: "none", "data-keep": "", text: "이 건물은 보내지 않음" }),
      );
      const group = el("optgroup", { label: "업체 지정" });
      for (const v of acc.vendors) group.append(el("option", { value: `v:${v.id}`, text: v.room_name ? `${v.name}  ·  ${v.room_name}` : `${v.name}  (톡방 없음)` }));
      select.append(group);
      select.value = row.value;
      const targetCell = el("div", { class: "av-target-cell" });
      const redraw = () => {
        if (!targetCell.querySelector(".av-contact-edit")) targetCell.replaceChildren(target(row, targetCell));
      };
      redraw();
      refreshers.add(redraw);
      // '보내지 않음' 바로 고르기 (목록이 길어 위쪽 항목이 잘 안 보이므로 따로 버튼)
      let before = row.value === "none" ? "default" : row.value;
      const noneBtn = el("button", { type: "button", class: "av-none-btn", "aria-pressed": String(row.value === "none") });
      const syncNone = () => {
        const on = row.value === "none";
        noneBtn.setAttribute("aria-pressed", String(on));
        noneBtn.replaceChildren(el("span", { class: "ms", "aria-hidden": "true", text: on ? "check" : "block" }), on ? "보내지 않음 해제" : "보내지 않음");
      };
      syncNone();
      const li = el(
        "li",
        { class: `av-row${row.enabled ? "" : " off"}${row.value !== "default" ? " custom" : ""}` },
        el("div", { class: "av-cat" }, el("span", { class: `dot c${color}`, "aria-hidden": "true" }), el("strong", { text: row.category }), row.enabled ? null : el("small", { text: "사용 안 함" })),
        el("div", { class: "av-pick" }, select, noneBtn),
        targetCell,
      );
      const apply = (value) => {
        if (row.value !== "none") before = row.value;
        row.value = value;
        select.value = value;
        targetCell.replaceChildren(target(row, targetCell));
        li.classList.toggle("custom", row.value !== "default");
        li.classList.toggle("none", row.value === "none");
        syncNone();
        updateDirty();
      };
      li.classList.toggle("none", row.value === "none");
      KC.searchable(select, "업체 검색");
      select.addEventListener("change", () => apply(select.value));
      noneBtn.addEventListener("click", () => apply(row.value === "none" ? before : "none"));
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

  // 다른 화면에서 업체를 추가·수정하면 열려 있는 건물의 업체 목록도 새로 (고르던 값은 그대로)
  async function refreshAccordionVendors() {
    if (!acc.id || !acc.detail) return;
    const id = acc.id;
    const data = await api(`/api/admin/apartments/${id}/vendors`);
    if (acc.id !== id) return;
    acc.vendors = data.vendors;
    const defaults = new Map(data.categories.map((c) => [c.category_id, c.defaults]));
    for (const r of acc.rows) if (defaults.has(r.category_id)) r.defaults = defaults.get(r.category_id);
    const old = acc.detail;
    acc.detail = buildPanel();
    old.replaceWith(acc.detail);
  }
  const prevTab = KC.onTab;
  KC.onTab = (tab) => {
    prevTab?.(tab);
    if (tab === "apartments") refreshAccordionVendors().catch(reportError);
  };
  const prevLive = KC.onLive;
  KC.onLive = (topics) => {
    const r = prevLive ? prevLive(topics) : null;
    if (KC.currentTab() === "apartments" && topics.includes("vendor")) refreshAccordionVendors().catch(reportError);
    return r;
  };

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
