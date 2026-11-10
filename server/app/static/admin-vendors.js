"use strict";

// 민원처리업체 탭: 업체 등록·수정·삭제, 담당 카톡 대화방 지정. 서버 데이터는 텍스트 노드로만 넣는다(innerHTML 금지).
(() => {
  const $ = (id) => document.getElementById(id);
  const KC = window.KC;
  const { api, el, reportError, fmtDateTime } = KC;
  const state = { items: [], rooms: [], categories: [], editing: null };

  async function send(method, path, body) {
    const res = await fetch(path, { method, credentials: "same-origin", headers: { Accept: "application/json" }, body });
    if (res.status === 401) location.href = "/admin/login";
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(", ") : data.detail;
      throw new Error(detail || `요청 실패 (HTTP ${res.status})`);
    }
    return data;
  }

  // ---- 목록 ------------------------------------------------------------------------------

  async function loadRooms() {
    const data = await api("/api/admin/rooms");
    state.rooms = data.items.map((r) => ({ name: r.room_name, group: r.is_group_chat, last: r.last_posted_at }));
  }

  async function loadCategories() {
    const data = await api("/api/admin/categories");
    state.categories = data.items;
  }

  async function loadVendors() {
    const [data, dispatch, unlinked] = await Promise.all([
      api("/api/admin/vendors"), api("/api/admin/vendor-dispatch", { limit: 50 }), api("/api/admin/vendors/unlinked-rooms"), loadRooms(), loadCategories(),
    ]);
    state.items = data.items;
    renderKpis(data.synced_at, data.reply_check);
    render();
    renderDispatch(dispatch);
    renderUnlinked(unlinked);
  }

  // ---- 연결 안 된 업체 톡방 ----------------------------------------------------------------------

  function renderUnlinked(data) {
    $("vd-unlinked-card").hidden = !data.items.length;
    $("vd-unlinked-count").textContent = `${data.items.length}개`;
    const list = $("vd-unlinked");
    list.replaceChildren();
    for (const r of data.items) {
      const select = el("select", { "aria-label": `${r.room_name} 에 연결할 업체` }, el("option", { value: "", text: data.vendors.length ? "업체를 고르십시오" : "톡방 없는 업체가 없습니다" }));
      if (r.suggested.length) select.append(el("optgroup", { label: "비슷한 이름" }, ...r.suggested.map((v) => el("option", { value: String(v.id), text: v.name }))));
      const others = data.vendors.filter((v) => !r.suggested.some((x) => x.id === v.id));
      if (others.length) select.append(el("optgroup", { label: "톡방 없는 업체 전체" }, ...others.map((v) => el("option", { value: String(v.id), text: v.name }))));
      if (r.suggested.length) select.value = String(r.suggested[0].id);
      const link = el("button", { class: "btn btn-small btn-primary", type: "button", text: "연결" });
      link.addEventListener("click", async () => {
        if (!select.value) return reportError(new Error("연결할 업체를 고르십시오."));
        link.disabled = true;
        try {
          await sendJson("POST", `/api/admin/vendors/${select.value}/room`, { room_name: r.room_name });
          await loadVendors();
        } catch (err) {
          link.disabled = false;
          reportError(err);
        }
      });
      const create = el("button", {
        class: "btn btn-small",
        type: "button",
        text: "새 업체로 추가",
        onclick: async () => {
          try {
            await sendJson("POST", "/api/admin/vendors/from-room", { room_name: r.room_name });
            await loadVendors();
          } catch (err) {
            reportError(err);
          }
        },
      });
      list.append(
        el(
          "tr",
          {},
          el("td", {}, KC.roomChip(r.room_name, true)),
          el("td", { class: "num", text: r.messages.toLocaleString("ko-KR") }),
          el("td", { class: "muted small", text: r.last_at ? fmtDateTime(r.last_at).slice(5, 16) : "–" }),
          el("td", {}, select),
          el("td", { class: "right-col" }, el("div", { class: "row-actions" }, link, create)),
        ),
      );
      KC.searchable(select, "업체 검색");
    }
  }

  // ---- 업체 카톡방 전달 이력 -------------------------------------------------------------------

  const DISPATCH = { pending: ["톡방 알림 대기", "wait"], sent: ["보냄", "ok"], failed: ["실패", "bad"], expired: ["만료", "bad"] };

  function renderDispatch(data) {
    const c = data.counts;
    $("vd-dispatch-counts").textContent = `보냄 ${c.sent || 0} · 대기 ${c.pending || 0} · 실패 ${(c.failed || 0) + (c.expired || 0)}`;
    const list = $("vd-dispatch");
    list.replaceChildren();
    if (!data.items.length) {
      list.append(el("li", { class: "empty-row", text: "아직 업체 톡방으로 보낸 처리 요청이 없습니다." }));
      return;
    }
    for (const d of data.items) {
      const [label, tone] = DISPATCH[d.status] || [d.status, "wait"];
      const retry =
        d.status === "failed" || d.status === "expired"
          ? el("button", {
              class: "btn btn-small",
              type: "button",
              onclick: () => send("POST", `/api/admin/vendor-dispatch/${d.id}/retry`).then(loadVendors).catch(reportError),
            }, el("span", { class: "ms", text: "refresh" }), "다시 보내기")
          : null;
      list.append(
        el(
          "li",
          { class: "dispatch-item" },
          el("span", { class: `status-badge ${tone}`, text: label }),
          el(
            "div",
            { class: "dispatch-main" },
            el("div", { class: "dispatch-title" }, el("strong", { text: d.vendor_name || "-" }), el("span", { class: "muted", text: " → " }), KC.roomChip(d.vendor_room)),
            el("div", { class: "muted small", text: `민원: ${d.complaint_room || "-"}${d.category ? ` · ${d.category}` : ""} · ${fmtDateTime(d.done_at || d.created_at).slice(0, 16)}` }),
            d.note && d.status !== "sent" ? el("div", { class: `small ${tone === "bad" ? "note-bad" : "muted"}`, text: d.note }) : null,
            el("details", {}, el("summary", { text: d.status === "pending" ? "보낼 문구" : "보낸 문구" }), el("pre", { class: "dispatch-text", text: d.text })),
            d.status === "pending" ? el("div", { class: "dispatch-ready" }, KC.readyBadge(d.reply_ready), KC.dispatchActions(d, loadVendors)) : null,
          ),
          retry,
        ),
      );
    }
  }

  function matches(v, q) {
    if (!q) return true;
    const phones = (v.contacts || []).flatMap((c) => [c.phone, c.phone.replace(/\D/g, ""), c.region]);
    return [v.name, v.phone, v.room_name, v.address, v.biz_no, ...(v.category_names || []), ...phones].some((x) => (x || "").toLowerCase().includes(q));
  }

  function kpi(icon, tone, label, value, unit) {
    return el(
      "div",
      { class: `kpi-card tone-${tone}` },
      el("div", { class: "kpi-text" }, el("span", { class: "kpi-label", text: label }), el("div", { class: "kpi-num" }, el("strong", { text: String(value) }), el("span", { text: unit }))),
      el("span", { class: "kpi-ico ms", "aria-hidden": "true", text: icon }),
    );
  }

  function renderKpis(syncedAt, check) {
    const total = state.items.length;
    const linked = state.items.filter((v) => v.room_name);
    const ready = linked.filter((v) => v.reply_ready === "ready").length;
    const withRoom = state.items.filter((v) => v.room_name).length;
    const withCat = state.items.filter((v) => (v.category_ids || []).length).length;
    $("vd-kpis").replaceChildren(
      kpi("domain", "blue", "등록 업체", total, "곳"),
      kpi("chat", "green", "처리 요청 톡방 연결", withRoom, `/ ${total}`),
      kpi("category", withCat < total ? "rose" : "indigo", "담당 카테고리 지정", withCat, `/ ${total}`),
      kpi("sync", "slate", "마지막 수집", syncedAt ? fmtDateTime(syncedAt).slice(5, 16) : "–", ""),
      check?.checked
        ? kpi("send", ready < linked.length ? "rose" : "green", "지금 바로 보낼 수 있는 톡방", ready, `/ ${linked.length}`)
        : kpi("send", "slate", "지금 바로 보낼 수 있는 톡방", "–", check?.reported_at ? `휴대폰 보고 ${fmtDateTime(check.reported_at).slice(5, 16)}` : "휴대폰 보고 없음"),
    );
  }

  function render() {
    const q = $("vd-search").value.trim().toLowerCase();
    const items = state.items.filter((v) => matches(v, q));
    $("vd-status").textContent = q ? `${items.length} / ${state.items.length}곳` : `${state.items.length}곳`;
    const list = $("vd-list");
    list.replaceChildren();
    if (!items.length) {
      list.append(el("tr", {}, el("td", { colspan: "7", class: "empty-cell", text: state.items.length ? "검색 결과가 없습니다." : "등록된 업체가 없습니다. 오른쪽 위 '다시 수집하기'를 누르십시오." })));
      return;
    }
    for (const v of items) list.append(vendorRow(v));
  }

  // ---- 담당자 연락처 (여러 명) ----------------------------------------------------------------------

  const telLink = (phone) => el("a", { class: "tel-link", href: `tel:${phone.replace(/[^0-9+]/g, "")}`, text: phone });
  const contactLine = (c) =>
    el("div", { class: "contact-line" }, telLink(c.phone), c.device ? el("span", { class: "contact-dev", text: c.device }) : null, c.region ? el("span", { class: "muted small", text: c.region }) : null);

  function contactsCell(v) {
    const box = el("div", { class: "contacts-cell" });
    const show = () => {
      const list = v.contacts || [];
      const rest = list.slice(1);
      box.replaceChildren(
        list.length ? contactLine(list[0]) : el("span", { class: "muted", text: "—" }),
        rest.length ? el("details", { class: "contact-more" }, el("summary", { text: `외 ${rest.length}명` }), ...rest.map(contactLine)) : null,
        el("button", { class: "link-btn", type: "button", text: list.length ? "편집" : "추가", onclick: edit }),
      );
    };
    const edit = () => {
      const area = el("textarea", { rows: String(Math.max(3, (v.contacts || []).length + 1)), class: "contacts-edit", "aria-label": "담당자 연락처" });
      area.value = (v.contacts || []).map((c) => [c.phone, c.device, c.region].join(" | ")).join("\n");
      const save = el("button", { class: "btn btn-small btn-primary", type: "button", text: "저장" });
      const cancel = el("button", { class: "btn btn-small", type: "button", text: "취소", onclick: show });
      save.addEventListener("click", async () => {
        const contacts = area.value.split("\n").map((line) => line.split("|").map((x) => x.trim())).filter((p) => p[0])
          .map(([phone, device = "", region = ""]) => ({ phone, device, region }));
        save.disabled = true;
        try {
          const r = await sendJson("PUT", `/api/admin/vendors/${v.id}/contacts`, { contacts });
          v.contacts = r.contacts;
          v.phone = r.contacts[0]?.phone || "";
          show();
        } catch (err) {
          reportError(err);
          save.disabled = false;
        }
      });
      box.replaceChildren(
        el("div", { class: "muted small", text: "한 줄에 한 명: 번호 | 기종 | 지역 (첫 줄이 대표 번호)" }),
        area,
        el("div", { class: "row-actions" }, save, cancel),
      );
      area.focus();
    };
    show();
    return box;
  }

  // 번호표(엑셀·CSV) 올리기: 먼저 미리보기 → 확인하면 반영
  async function importContacts(file, apply) {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`/api/admin/vendors/contacts/import?apply=${apply}`, { method: "POST", credentials: "same-origin", body: form });
    if (res.status === 401) location.href = "/admin/login";
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(", ") : data.detail) || `요청 실패 (HTTP ${res.status})`);
    return data;
  }

  function showImport(file, r) {
    const box = $("vd-result");
    box.hidden = false;
    box.classList.add("has-new");
    const rows = r.vendors.map((v) =>
      el(
        "li",
        {},
        el("strong", { text: v.name }),
        v.category ? el("span", { class: "chip-cat", text: v.category }) : null,
        el("span", {
          class: v.matched || (r.applied && !v.created) ? "muted" : "warn-text",
          text: r.applied ? (v.created ? " 새 업체로 추가" : ` → '${v.matched || v.name}' 에 반영`) : v.matched ? ` → 기존 '${v.matched}' 에 반영` : " → 없는 업체: 새로 추가",
        }),
        el("span", { class: "muted", text: ` · ${v.contacts.length}명 (${v.contacts.map((c) => c.phone).join(", ")})` }),
      ),
    );
    const actions = r.applied
      ? [el("button", { class: "btn btn-small", type: "button", text: "닫기", onclick: () => (box.hidden = true) })]
      : [
          el("button", {
            class: "btn btn-small btn-primary",
            type: "button",
            text: "이대로 반영",
            onclick: async () => {
              try {
                showImport(file, await importContacts(file, true));
                await loadVendors();
              } catch (err) {
                reportError(err);
              }
            },
          }),
          el("button", { class: "btn btn-small", type: "button", text: "취소", onclick: () => (box.hidden = true) }),
        ];
    box.replaceChildren(
      el(
        "div",
        { class: "import-preview" },
        el("p", {
          text: r.applied
            ? `반영했습니다: 업체 ${r.vendors.length}곳 · 연락처 ${r.contacts}개. 새로 추가된 업체는 톡방·담당 카테고리를 지정해 주십시오.`
            : `미리보기 (${file.name}): 업체 ${r.vendors.length}곳 · 연락처 ${r.contacts}개. 파일에 있는 업체의 연락처는 파일 내용으로 바뀝니다(다른 업체는 그대로).`,
        }),
        el("ul", {}, ...rows),
        el("div", { class: "row-actions" }, ...actions),
      ),
    );
  }

  function vendorRow(v) {
    const input = el("input", { class: "apt-name", value: v.name, maxlength: "100", "aria-label": "업체 이름" });
    const save = el("button", { class: "btn btn-small btn-primary", type: "button", text: "저장", disabled: true });
    const tr = el("tr", {});
    input.addEventListener("input", () => {
      const changed = input.value.trim() !== v.name;
      save.disabled = !changed || !input.value.trim();
      tr.classList.toggle("dirty", changed);
    });
    const doSave = async () => {
      if (save.disabled) return;
      try {
        const r = await sendJson("PATCH", `/api/admin/vendors/${v.id}/name`, { name: input.value });
        v.name = r.name;
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
    const roomCell = el("td", {});
    const showRoom = () => {
      roomCell.replaceChildren(
        el(
          "div",
          { class: "vd-room-cell" },
          v.room_name ? KC.roomChip(v.room_name, v.messages > 0) : el("span", { class: "warn-text", text: "미지정 — 요청 못 보냄" }),
          v.room_name ? KC.readyBadge(v.reply_ready) : null,
          el("button", { class: "link-btn", type: "button", text: v.room_name ? "변경" : "톡방 설정", onclick: () => openRoomPicker(v, roomCell, showRoom) }),
        ),
      );
    };
    showRoom();
    const tel = contactsCell(v);
    const doc = v.has_doc ? el("a", { class: "doc-link", href: `/api/admin/vendors/${v.id}/doc`, target: "_blank", rel: "noopener", title: "사업자등록증 보기" }, el("span", { class: "ms", text: "description" })) : null;
    tr.append(
      el("td", { class: "apt-name-cell" }, el("div", { class: "name-with-doc" }, input, doc)),
      roomCell,
      el("td", {}, (v.category_names || []).length
        ? el("div", { class: "cat-chips" }, ...v.category_names.map((n) => el("span", { class: "chip-cat", text: n })))
        : el("span", { class: "warn-text", text: "미지정" })),
      el("td", {}, tel),
      el("td", { class: "num", text: (v.messages || 0).toLocaleString("ko-KR") }),
      el("td", { class: "muted small", text: v.last_at ? fmtDateTime(v.last_at).slice(0, 16) : "대화 없음" }),
      el(
        "td",
        { class: "right-col" },
        el(
          "div",
          { class: "row-actions" },
          save,
          el("button", { class: "btn btn-small", type: "button", onclick: () => openForm(v) }, el("span", { class: "ms", text: "edit" }), "상세"),
          el("button", { class: "btn btn-small btn-danger", type: "button", text: "삭제", onclick: () => remove(v).catch(reportError) }),
        ),
      ),
    );
    return tr;
  }

  // ---- 목록에서 바로 처리 요청 톡방 설정 ----------------------------------------------------------

  const normName = (t) => (t || "").replace(/[●⚫⬤⏺🔴🟠🟡🟢🔵🟣🟤★☆\s()（）㈜]|\(주\)|주식회사/g, "").toLowerCase();
  const VENDOR_ROOM = /^\s*[●⚫⬤⏺🔴🟠🟡🟢🔵🟣🟤]/u;

  /** 톡방 고르기: 이름 비슷한 방 → 업체 톡방(●) → 다른 채팅방 순, 다른 업체에 연결된 방은 고를 수 없음. 위에 검색 칸. */
  function openRoomPicker(v, cell, restore) {
    const usedBy = new Map(state.items.filter((x) => x.room_name && x.id !== v.id).map((x) => [x.room_name, x.name]));
    const key = normName(v.name);
    const similar = [], circle = [], other = [], taken = [];
    for (const r of state.rooms) {
      if (r.name === v.room_name) continue;
      if (usedBy.has(r.name)) taken.push(r);
      else if (key.length >= 2 && (normName(r.name).includes(key) || (normName(r.name).length >= 2 && key.includes(normName(r.name))))) similar.push(r);
      else if (VENDOR_ROOM.test(r.name)) circle.push(r);
      else other.push(r);
    }
    const opt = (r, disabled) => el("option", { value: r.name, disabled, text: disabled ? `${r.name}   (현재: ${usedBy.get(r.name)})` : r.name });
    const select = el("select", { class: "vd-room-select", "aria-label": `${v.name} 처리 요청 톡방` });
    select.append(el("option", { value: "", text: v.room_name ? `현재: ${v.room_name}` : "톡방을 고르십시오" }));
    if (v.room_name) select.append(el("option", { value: "__none", "data-keep": "", text: "— 연결 해제 (요청 보내지 않음) —" }));
    for (const [label, rows, disabled] of [["이름이 비슷한 방", similar, false], ["업체 톡방 (●)", circle, false], ["다른 채팅방", other, false], ["다른 업체에 연결된 방", taken, true]]) {
      if (rows.length) select.append(el("optgroup", { label }, ...rows.map((r) => opt(r, disabled))));
    }
    if (similar.length) select.value = similar[0].name;
    const ok = el("button", { class: "btn btn-small btn-primary", type: "button", text: "저장" });
    const cancel = el("button", { class: "btn btn-small", type: "button", text: "취소", onclick: restore });
    ok.addEventListener("click", async () => {
      const value = select.value;
      if (!value) return reportError(new Error("톡방을 고르십시오."));
      ok.disabled = true;
      try {
        if (value === "__none") {
          if (!confirm(`'${v.name}' 의 톡방 연결을 해제하시겠습니까? 이 업체로는 처리 요청을 보내지 않게 됩니다.`)) {
            ok.disabled = false;
            return;
          }
          await sendJson("DELETE", `/api/admin/vendors/${v.id}/room`);
        } else {
          await sendJson("POST", `/api/admin/vendors/${v.id}/room`, { room_name: value });
        }
        await loadVendors();
      } catch (err) {
        ok.disabled = false;
        reportError(err);
      }
    });
    const box = el("div", { class: "vd-room-edit" }, select, el("div", { class: "vd-room-actions" }, ok, cancel));
    cell.replaceChildren(box);
    KC.searchable(select, "카톡 대화방 검색");
    box.querySelector(".sel-search")?.focus();
    box.addEventListener("keydown", (e) => e.key === "Escape" && restore());
  }

  async function sendJson(method, path, body) {
    const res = await fetch(path, { method, credentials: "same-origin", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: body ? JSON.stringify(body) : undefined });
    if (res.status === 401) location.href = "/admin/login";
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(", ") : data.detail) || `요청 실패 (HTTP ${res.status})`);
    return data;
  }

  async function sync() {
    const btn = $("vd-sync");
    btn.disabled = true;
    try {
      const r = await sendJson("POST", "/api/admin/vendors/sync");
      const box = $("vd-result");
      const parts = [];
      if (r.added.length) parts.push(`새 업체 ${r.added.length}곳 추가: ${r.added.map((a) => a.name).join(", ")}`);
      const exact = r.linked.filter((a) => a.exact);
      const similar = r.linked.filter((a) => !a.exact);
      if (exact.length) parts.push(`같은 이름의 업체에 톡방 연결 ${exact.length}곳: ${exact.map((a) => a.name).join(", ")}`);
      if (similar.length) parts.push(`비슷한 이름으로 연결 ${similar.length}곳(확인 바랍니다): ${similar.map((a) => `${a.room_name} → ${a.name}`).join(", ")}`);
      if (r.ambiguous?.length) parts.push(`후보가 여럿이라 남긴 톡방 ${r.ambiguous.length}개(업체 수정에서 직접 연결): ${r.ambiguous.join(", ")}`);
      box.hidden = false;
      box.classList.toggle("has-new", parts.length > 0);
      box.replaceChildren(
        el("span", { class: "ms", "aria-hidden": "true", text: parts.length ? "new_releases" : "check_circle" }),
        parts.length ? parts.join(" · ") : `새로 추가할 업체가 없습니다. (채팅방 ${r.rooms}개 중 ● 로 시작하는 방 ${r.matched}개, 모두 등록됨)`,
      );
      await loadVendors();
    } finally {
      btn.disabled = false;
    }
  }

  // ---- 등록·수정 폼 ---------------------------------------------------------------------------

  function fillRoomSelect(current) {
    const sel = $("vd-room");
    sel.replaceChildren(el("option", { value: "", text: "(지정 안 함)" }));
    const names = state.rooms.map((r) => r.name);
    if (current && !names.includes(current)) sel.append(el("option", { value: current, text: `${current} (목록에 없음)` }));
    for (const r of state.rooms) {
      sel.append(el("option", { value: r.name, text: `${r.group ? "👥" : "👤"} ${r.name}${r.last ? ` · 최근 ${fmtDateTime(r.last).slice(5, 16)}` : ""}` }));
    }
    sel.value = current || "";
  }

  /** 기본 담당 카테고리: 여러 개를 체크 (전체 선택·해제 포함) */
  function fillCategorySelect(current) {
    const box = $("vd-category");
    const chosen = new Set((current || []).map(String));
    const checks = state.categories.map((c) => {
      const input = el("input", { type: "checkbox", value: String(c.id) });
      input.checked = chosen.has(String(c.id));
      return el("label", { class: `cat-check${c.enabled ? "" : " off"}` }, input, el("span", { text: c.enabled ? c.name : `${c.name} (사용 안 함)` }));
    });
    const all = el("button", {
      class: "link-btn", type: "button", text: "전체 선택",
      onclick: () => {
        const inputs = [...box.querySelectorAll("input[type=checkbox]")];
        const on = inputs.some((i) => !i.checked);
        for (const i of inputs) i.checked = on;
      },
    });
    box.replaceChildren(...checks, all);
  }

  function chosenCategories() {
    return [...$("vd-category").querySelectorAll("input[type=checkbox]:checked")].map((i) => i.value);
  }

  function openForm(v = null) {
    state.editing = v;
    $("vd-form-title").textContent = v ? `업체 수정 — ${v.name}` : "업체 등록";
    $("vd-name").value = v?.name || "";
    $("vd-biz").value = v?.biz_no || "";
    $("vd-phone").value = v?.phone || "";
    $("vd-address").value = v?.address || "";
    $("vd-doc").value = "";
    $("vd-doc-name").textContent = "선택된 파일 없음";
    $("vd-doc-remove").checked = false;
    $("vd-doc-current").textContent = v?.has_doc ? "현재 등록증 있음 — 새 파일을 고르면 교체됩니다." : "";
    $("vd-doc-remove-wrap").hidden = !v?.has_doc;
    $("vd-error").textContent = "";
    fillRoomSelect(v?.room_name || "");
    fillCategorySelect(v?.category_ids || []);
    $("vd-form").hidden = false;
    $("vd-form").scrollIntoView({ block: "nearest" });
    $("vd-name").focus();
  }

  function closeForm() {
    state.editing = null;
    $("vd-form").hidden = true;
  }

  function validate() {
    const name = $("vd-name").value.trim();
    const biz = $("vd-biz").value.trim();
    const phone = $("vd-phone").value.trim();
    if (!name) return ["vd-name", "업체명을 입력하십시오."];
    if (biz && !/^\d{3}-?\d{2}-?\d{5}$/.test(biz)) return ["vd-biz", "사업자등록번호는 10자리 숫자입니다 (예: 123-45-67890)."];
    if (phone && !/^[0-9+\-() ]{0,20}$/.test(phone)) return ["vd-phone", "전화번호는 숫자와 - 만 입력하십시오."];
    const file = $("vd-doc").files[0];
    if (file && file.size > 10 * 1024 * 1024) return ["vd-doc", "사업자등록증 파일은 10MB 이하여야 합니다."];
    return null;
  }

  async function save(e) {
    e.preventDefault();
    const problem = validate();
    if (problem) {
      $("vd-error").textContent = problem[1];
      $(problem[0]).focus();
      return;
    }
    const form = new FormData();
    form.append("name", $("vd-name").value.trim());
    form.append("biz_no", $("vd-biz").value.trim());
    form.append("phone", $("vd-phone").value.trim());
    form.append("address", $("vd-address").value.trim());
    form.append("room_name", $("vd-room").value);
    form.append("category_ids", chosenCategories().join(","));
    const file = $("vd-doc").files[0];
    if (file) form.append("doc", file);
    const editing = state.editing;
    if (editing && $("vd-doc-remove").checked && !file) form.append("remove_doc", "true");
    $("vd-save").disabled = true;
    try {
      await send(editing ? "PUT" : "POST", editing ? `/api/admin/vendors/${editing.id}` : "/api/admin/vendors", form);
      closeForm();
      await loadVendors();
    } catch (err) {
      $("vd-error").textContent = err.message;
    } finally {
      $("vd-save").disabled = false;
    }
  }

  async function remove(v) {
    if (!confirm(`'${v.name}' 업체를 삭제하시겠습니까?`)) return;
    await send("DELETE", `/api/admin/vendors/${v.id}`);
    if (state.editing?.id === v.id) closeForm();
    await loadVendors();
  }

  // ---- 연결 -------------------------------------------------------------------------------

  KC.searchable($("vd-room"), "카톡 대화방 검색");
  KC.loaders = Object.assign(KC.loaders || {}, { vendors: loadVendors });
  $("vd-sync").addEventListener("click", () => sync().catch(reportError));
  $("vd-import").addEventListener("click", () => $("vd-import-file").click());
  $("vd-import-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try {
      showImport(file, await importContacts(file, false));
    } catch (err) {
      reportError(err);
    }
  });
  $("vd-doc").addEventListener("change", () => {
    const f = $("vd-doc").files[0];
    $("vd-doc-name").textContent = f ? `${f.name} (${Math.ceil(f.size / 1024)}KB)` : "선택된 파일 없음";
  });
  $("vd-new").addEventListener("click", () => Promise.all([loadRooms(), loadCategories()]).then(() => openForm()).catch(reportError));
  $("vd-cancel").addEventListener("click", closeForm);
  $("vd-form").addEventListener("submit", (e) => save(e).catch(reportError));
  $("vd-search").addEventListener("input", render);
  $("vd-biz").addEventListener("blur", () => {
    const d = $("vd-biz").value.replace(/\D/g, "");
    if (d.length === 10) $("vd-biz").value = `${d.slice(0, 3)}-${d.slice(3, 5)}-${d.slice(5)}`;
  });
})();
