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
    const data = await api("/api/admin/categories");
    categories = data.items;
    const active = categories.filter((c) => c.enabled).length;
    $("ct-status").textContent = `카테고리 ${categories.length}개 · 사용 ${active}개`;
    const list = $("ct-list");
    list.replaceChildren();
    categories.forEach((c, i) => list.append(categoryRow(c, i)));
  }

  function categoryRow(c, index) {
    const name = el("input", { class: "ct-name", value: c.name, maxlength: "30", "aria-label": "카테고리 이름", disabled: c.protected });
    const desc = el("input", { class: "ct-desc", value: c.description, maxlength: "200", placeholder: "판정 기준 설명 (선택)", "aria-label": "판정 기준 설명" });
    const enabled = el("input", { type: "checkbox", checked: c.enabled, disabled: c.protected, "aria-label": "사용" });
    const save = el("button", { class: "btn btn-small", type: "button", text: "저장", disabled: true });
    const dirty = () => {
      save.disabled = name.value.trim() === c.name && desc.value.trim() === c.description && enabled.checked === c.enabled;
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
      text: "삭제",
      disabled: c.protected,
      title: c.protected ? "'기타'는 삭제할 수 없습니다" : "",
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

    return el(
      "li",
      { class: `ct-row${c.enabled ? "" : " ct-off"}` },
      el(
        "div",
        { class: "ct-order" },
        el("button", { class: "btn btn-icon", type: "button", text: "▲", "aria-label": "위로", disabled: index === 0, onclick: () => move(-1)().catch(reportError) }),
        el("button", { class: "btn btn-icon", type: "button", text: "▼", "aria-label": "아래로", disabled: index === categories.length - 1, onclick: () => move(1)().catch(reportError) }),
      ),
      el("div", { class: "ct-fields" }, name, desc),
      el("div", { class: "ct-meta" }, el("span", { class: "chip-count", text: `민원 ${c.complaints}` }), c.vendors ? el("span", { class: "muted small", text: `업체 ${c.vendors}` }) : null),
      el("label", { class: "check ct-enabled" }, enabled, " 사용"),
      el("div", { class: "ct-actions" }, save, remove),
    );
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
