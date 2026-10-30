"use strict";

// 민원처리업체 탭: 업체 등록·수정·삭제, 담당 카톡 대화방 지정. 서버 데이터는 텍스트 노드로만 넣는다(innerHTML 금지).
(() => {
  const $ = (id) => document.getElementById(id);
  const KC = window.KC;
  const { api, el, reportError, fmtDateTime } = KC;
  const state = { items: [], rooms: [], editing: null };

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

  async function loadVendors() {
    const [data] = await Promise.all([api("/api/admin/vendors"), loadRooms()]);
    state.items = data.items;
    render();
  }

  function matches(v, q) {
    if (!q) return true;
    return [v.name, v.phone, v.room_name, v.address, v.biz_no].some((x) => (x || "").toLowerCase().includes(q));
  }

  function render() {
    const q = $("vd-search").value.trim().toLowerCase();
    const items = state.items.filter((v) => matches(v, q));
    const roomNames = new Set(state.rooms.map((r) => r.name));
    $("vd-status").textContent = `등록된 업체 ${state.items.length}곳${q ? ` · 검색 결과 ${items.length}곳` : ""}`;
    const list = $("vd-list");
    list.replaceChildren();
    if (!items.length) {
      list.append(el("li", { class: "empty-row", text: state.items.length ? "검색 결과가 없습니다." : "등록된 업체가 없습니다. 오른쪽 위 '+ 업체 등록'으로 추가하십시오." }));
      return;
    }
    for (const v of items) list.append(vendorCard(v, roomNames));
  }

  function vendorCard(v, roomNames) {
    const row = (k, value) => [el("dt", { text: k }), el("dd", {}, value || el("span", { class: "muted", text: "—" }))];
    let room = null;
    if (v.room_name) {
      const missing = !roomNames.has(v.room_name);
      room = el("span", {
        class: `vd-room${missing ? " vd-room-missing" : ""}`,
        title: missing ? "수집된 대화방 목록에 없는 방입니다 (방 이름이 바뀌었을 수 있음)" : "",
        text: `💬 ${v.room_name}`,
      });
    }
    const doc = v.has_doc
      ? el("a", { href: `/api/admin/vendors/${v.id}/doc`, target: "_blank", rel: "noopener", text: "📄 보기" })
      : null;
    const tel = v.phone ? el("a", { href: `tel:${v.phone.replace(/[^0-9+]/g, "")}`, text: v.phone }) : null;
    const goRoom =
      v.room_name && roomNames.has(v.room_name)
        ? el("button", { class: "btn btn-small", type: "button", text: "대화 보기", onclick: () => KC.openRoom(v.room_name) })
        : null;
    return el(
      "li",
      { class: "vd-card" },
      el("div", { class: "vd-card-head" }, el("strong", { text: v.name })),
      el(
        "dl",
        {},
        ...row("담당 대화방", room),
        ...row("전화번호", tel),
        ...row("사업자번호", v.biz_no),
        ...row("주소", v.address),
        ...row("사업자등록증", doc),
      ),
      el("p", { class: "muted small", text: `등록 ${fmtDateTime(v.created_at).slice(0, 16)}${v.updated_at !== v.created_at ? ` · 수정 ${fmtDateTime(v.updated_at).slice(0, 16)}` : ""}` }),
      el(
        "div",
        { class: "vd-card-actions" },
        el("button", { class: "btn btn-small", type: "button", text: "수정", onclick: () => openForm(v) }),
        goRoom,
        el("button", { class: "btn btn-small btn-danger", type: "button", text: "삭제", onclick: () => remove(v).catch(reportError) }),
      ),
    );
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

  function openForm(v = null) {
    state.editing = v;
    $("vd-form-title").textContent = v ? `업체 수정 — ${v.name}` : "업체 등록";
    $("vd-name").value = v?.name || "";
    $("vd-biz").value = v?.biz_no || "";
    $("vd-phone").value = v?.phone || "";
    $("vd-address").value = v?.address || "";
    $("vd-doc").value = "";
    $("vd-doc-remove").checked = false;
    $("vd-doc-current").textContent = v?.has_doc ? "현재 등록증 있음 — 새 파일을 고르면 교체됩니다." : "";
    $("vd-doc-remove-wrap").hidden = !v?.has_doc;
    $("vd-error").textContent = "";
    fillRoomSelect(v?.room_name || "");
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

  KC.loaders = Object.assign(KC.loaders || {}, { vendors: loadVendors });
  $("vd-new").addEventListener("click", () => loadRooms().then(() => openForm()).catch(reportError));
  $("vd-cancel").addEventListener("click", closeForm);
  $("vd-form").addEventListener("submit", (e) => save(e).catch(reportError));
  $("vd-search").addEventListener("input", render);
  $("vd-biz").addEventListener("blur", () => {
    const d = $("vd-biz").value.replace(/\D/g, "");
    if (d.length === 10) $("vd-biz").value = `${d.slice(0, 3)}-${d.slice(3, 5)}-${d.slice(5)}`;
  });
})();
