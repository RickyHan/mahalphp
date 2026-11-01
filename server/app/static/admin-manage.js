"use strict";

// '민원 카테고리' 탭과 '데이터 관리' 탭. 서버 데이터는 텍스트 노드로만 넣는다(innerHTML 금지).
(() => {
  const $ = (id) => document.getElementById(id);
  const KC = window.KC;
  const { api, el, postJson, reportError, fmtDateTime } = KC;

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

  // ---- 민원 카테고리 ------------------------------------------------------------------------

  let categories = [];

  function showCtError(msg) {
    $("ct-error").textContent = msg || "";
  }

  async function loadCategories() {
    const [data, vendors] = await Promise.all([api("/api/admin/categories"), api("/api/admin/vendors")]);
    categories = data.items;
    const vendorsByCat = new Map();
    for (const v of vendors.items) {
      if (!v.category_id) continue;
      vendorsByCat.set(v.category_id, [...(vendorsByCat.get(v.category_id) || []), v.name]);
    }
    const active = categories.filter((c) => c.enabled).length;
    const total = categories.reduce((n, c) => n + c.complaints, 0);
    const etc = categories.find((c) => c.protected);
    const linked = categories.filter((c) => vendorsByCat.has(c.id)).length;
    $("ct-status").textContent = `${active}개 사용 / ${categories.length - active}개 사용 안 함`;
    $("ct-kpis").replaceChildren(
      kpi("folder_special", "blue", "전체 카테고리", categories.length, "개 등록"),
      kpi("toggle_on", "indigo", "분류에 사용 중", active, `/ ${categories.length}`),
      kpi("domain", "green", "업체 연결된 카테고리", linked, `/ ${categories.length}`),
      kpi("pending_actions", etc && etc.complaints ? "rose" : "slate", "'기타'로 분류된 민원", etc ? etc.complaints : 0, total ? `건 · 전체 민원 ${total}건` : "건"),
    );
    // 통계 화면과 같은 색: '기타' 제외 등록 순서대로 1~8번 색, 그 뒤는 회색
    let slot = 0;
    const list = $("ct-list");
    list.replaceChildren();
    categories.forEach((c, i) => list.append(categoryRow(c, i, c.protected ? 0 : ++slot > 8 ? 0 : slot, vendorsByCat.get(c.id) || [])));
  }

  function kpi(icon, tone, label, value, unit) {
    return el(
      "div",
      { class: `kpi-card tone-${tone}` },
      el("div", { class: "kpi-text" }, el("span", { class: "kpi-label", text: label }), el("div", { class: "kpi-num" }, el("strong", { text: String(value) }), el("span", { text: unit }))),
      el("span", { class: "kpi-ico ms", "aria-hidden": "true", text: icon }),
    );
  }

  function categoryRow(c, index, slot, vendorNames) {
    const name = el("input", { class: "ct-name", value: c.name, maxlength: "30", "aria-label": "카테고리 이름", disabled: c.protected });
    const desc = el("input", { class: "ct-desc", value: c.description, maxlength: "200", placeholder: "판정 기준 설명 (선택)", "aria-label": "판정 기준 설명" });
    const enabled = el("input", { type: "checkbox", class: "switch", checked: c.enabled, disabled: c.protected, "aria-label": `${c.name} 사용` });
    const save = el("button", { class: "btn btn-small", type: "button", text: "저장", disabled: true });
    const dirty = () => {
      save.disabled = name.value.trim() === c.name && desc.value.trim() === c.description && enabled.checked === c.enabled;
      row.classList.toggle("dirty", !save.disabled);
    };
    for (const input of [name, desc]) input.addEventListener("input", dirty);
    enabled.addEventListener("change", dirty);

    save.addEventListener("click", async () => {
      showCtError("");
      try {
        const newName = name.value.trim();
        if (newName !== c.name && c.complaints && !confirm(`이름을 '${c.name}' → '${newName}' 로 바꾸면 기존 민원 ${c.complaints}건의 분류도 함께 바뀝니다. 계속하시겠습니까?`)) return;
        await send("PUT", `/api/admin/categories/${c.id}`, { name: newName, description: desc.value.trim(), enabled: enabled.checked });
        await loadCategories();
      } catch (err) {
        showCtError(err.message);
      }
    });

    const remove = el("button", {
      class: "btn btn-small btn-danger",
      type: "button",
      text: c.protected ? "삭제 불가" : "삭제",
      disabled: c.protected,
      title: c.protected ? "'기타'는 분류할 곳이 없는 민원을 받는 기본값이라 삭제할 수 없습니다" : "",
      onclick: async () => {
        const notes = [c.complaints && `기존 민원 ${c.complaints}건은 '기타'로 옮겨집니다.`, c.vendors && `업체 ${c.vendors}곳의 카테고리 지정이 해제됩니다.`].filter(Boolean);
        if (!confirm(`'${c.name}' 카테고리를 삭제하시겠습니까?${notes.length ? `\n${notes.join("\n")}` : ""}`)) return;
        showCtError("");
        try {
          await send("DELETE", `/api/admin/categories/${c.id}`);
          await loadCategories();
        } catch (err) {
          showCtError(err.message);
        }
      },
    });

    const move = (delta) => async () => {
      const ids = categories.map((x) => x.id);
      const j = index + delta;
      if (j < 0 || j >= ids.length) return;
      [ids[index], ids[j]] = [ids[j], ids[index]];
      await send("POST", "/api/admin/categories/order", { ids });
      await loadCategories();
    };

    const order = c.protected
      ? el("span", { class: "ms lock", title: "기본값", text: "lock" })
      : el(
          "div",
          { class: "ct-order" },
          el("button", { class: "icon-btn sm", type: "button", "aria-label": "위로", disabled: index === 0, onclick: () => move(-1)().catch(reportError) }, el("span", { class: "ms", text: "expand_less" })),
          el("button", { class: "icon-btn sm", type: "button", "aria-label": "아래로", disabled: index === categories.length - 1, onclick: () => move(1)().catch(reportError) }, el("span", { class: "ms", text: "expand_more" })),
        );
    const count = el("span", { class: `count-pill${c.complaints ? " has" : ""}`, text: `${c.complaints}건` });
    const vendorsCell = vendorNames.length
      ? el("div", { class: "chips" }, ...vendorNames.map((n) => el("span", { class: "chip-soft", text: n })))
      : el("span", { class: "muted small", text: "미지정" });
    const row = el(
      "tr",
      { class: `${c.enabled ? "" : "row-off"}${c.protected ? " row-locked" : ""}` },
      el("td", { class: "w-order" }, order),
      el("td", { class: "w-name" }, el("div", { class: "ct-name-cell" }, el("span", { class: `dot c${slot}`, "aria-hidden": "true" }), name)),
      el("td", {}, desc),
      el("td", { class: "num" }, count),
      el("td", {}, vendorsCell),
      el("td", { class: "center-col" }, enabled),
      el("td", { class: "right-col" }, el("div", { class: "ct-actions" }, save, remove)),
    );
    return row;
  }

  async function addCategory(e) {
    e.preventDefault();
    showCtError("");
    const name = $("ct-name").value.trim();
    if (!name) {
      showCtError("카테고리 이름을 입력하십시오.");
      $("ct-name").focus();
      return;
    }
    try {
      await send("POST", "/api/admin/categories", { name, description: $("ct-desc").value.trim(), enabled: true });
      $("ct-name").value = "";
      $("ct-desc").value = "";
      await loadCategories();
    } catch (err) {
      showCtError(err.message);
    }
  }

  // ---- 데이터 관리 --------------------------------------------------------------------------

  const picked = new Set();
  let rooms = [];

  async function loadData() {
    const data = await api("/api/admin/data/rooms");
    rooms = data.items;
    for (const r of [...picked]) if (!rooms.some((x) => x.room_name === r)) picked.delete(r);
    const tbody = $("dm-rows");
    tbody.replaceChildren();
    if (!rooms.length) tbody.append(el("tr", {}, el("td", { colspan: "7", class: "empty-row", text: "수집된 데이터가 없습니다." })));
    for (const r of rooms) {
      const box = el("input", { type: "checkbox", checked: picked.has(r.room_name), "aria-label": `${r.room_name} 선택` });
      box.addEventListener("change", () => {
        if (box.checked) picked.add(r.room_name);
        else picked.delete(r.room_name);
        updateDataControls();
      });
      const period = `${fmtDateTime(r.first_at).slice(0, 10)} ~ ${fmtDateTime(r.last_at).slice(0, 10)}`;
      tbody.append(
        el(
          "tr",
          {},
          el("td", { class: "dm-check" }, box),
          el("td", { class: "dm-room" }, el("button", { class: "linklike", type: "button", text: r.room_name, onclick: () => KC.openRoom(r.room_name) })),
          el("td", { class: "num", text: r.messages.toLocaleString("ko-KR") }),
          el("td", { class: "num", text: r.photos.toLocaleString("ko-KR") }),
          el("td", { class: "num", text: r.complaints.toLocaleString("ko-KR") }),
          el("td", { class: "muted small", text: period }),
          el("td", {}, el("button", { class: "btn btn-small btn-danger", type: "button", text: "삭제", onclick: () => KC.deleteRoom(r.room_name).then(loadData).catch(reportError) })),
        ),
      );
    }
    $("dm-status").textContent = `대화방 ${rooms.length}개 · 메시지 ${data.total.toLocaleString("ko-KR")}개`;
    updateDataControls();
  }

  function updateDataControls() {
    const n = picked.size;
    $("dm-delete").disabled = n === 0;
    $("dm-delete").textContent = n ? `선택한 방 ${n}개 삭제` : "선택한 방 삭제";
    $("dm-all").checked = rooms.length > 0 && n === rooms.length;
    $("dm-all").indeterminate = n > 0 && n < rooms.length;
    $("dm-all-delete").disabled = $("dm-confirm").value.trim() !== "전체 삭제";
  }

  async function deletePicked() {
    const list = [...picked];
    const total = rooms.filter((r) => picked.has(r.room_name)).reduce((s, r) => s + r.messages, 0);
    if (!confirm(`선택한 방 ${list.length}개(메시지 ${total.toLocaleString("ko-KR")}개)를 삭제하시겠습니까?\n되돌릴 수 없습니다.`)) return;
    const r = await postJson("/api/admin/data/delete", { scope: "rooms", rooms: list });
    picked.clear();
    alert(`방 ${list.length}개에서 메시지 ${r.messages}개를 삭제했습니다.`);
    await Promise.all([loadData(), KC.refreshAfterDelete()]);
  }

  async function deleteAll(e) {
    e.preventDefault();
    if ($("dm-confirm").value.trim() !== "전체 삭제") return;
    if (!confirm("수집된 모든 데이터를 삭제합니다. 정말 진행하시겠습니까?")) return;
    const r = await postJson("/api/admin/data/delete", { scope: "all", confirm: "전체 삭제" });
    $("dm-confirm").value = "";
    picked.clear();
    alert(`전체 삭제 완료: 메시지 ${r.messages}개 · 사진 ${r.photos}개 · 참여자 이력 ${r.member_events}건`);
    await Promise.all([loadData(), KC.refreshAfterDelete()]);
  }

  // ---- 연결 -------------------------------------------------------------------------------

  KC.loaders = Object.assign(KC.loaders || {}, { categories: loadCategories, data: loadData });
  $("ct-form").addEventListener("submit", (e) => addCategory(e).catch(reportError));
  $("dm-all").addEventListener("change", () => {
    picked.clear();
    if ($("dm-all").checked) for (const r of rooms) picked.add(r.room_name);
    loadData().catch(reportError);
  });
  $("dm-delete").addEventListener("click", () => deletePicked().catch(reportError));
  $("dm-confirm").addEventListener("input", updateDataControls);
  $("dm-all-form").addEventListener("submit", (e) => deleteAll(e).catch(reportError));
})();
