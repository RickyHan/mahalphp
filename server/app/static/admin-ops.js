"use strict";

// 민원 · 통계 · 참여자 이력 · 설정 탭. 서버 데이터는 텍스트 노드로만 넣는다(innerHTML 금지).
(() => {
  const $ = (id) => document.getElementById(id);
  const KC = window.KC;
  const { api, el, reportError, fmtDateTime } = KC;
  const loaded = {};

  async function send(method, path, body) {
    const res = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) location.href = "/admin/login";
    if (!res.ok) throw new Error(`요청 실패 (HTTP ${res.status})`);
    return res.json();
  }

  const REPLY = {
    1: (n) => "✅ 접수 안내 보냄",
    "-1": (n) => `⚠️ 안내 실패${n ? `: ${n}` : ""}`,
    3: () => "안내 생략 (30분 지난 메시지)",
    4: () => "⏳ 휴대폰 전송 대기",
  };
  const DISPATCH_LABEL = { pending: "⏳ 대기 — 업체 톡방에 새 글이 오면 바로 전송", sent: "✅ 보냄", failed: "⚠️ 실패", expired: "⚠️ 만료(앱이 가져가지 않음)" };
  const replyLabel = (status, note) => (REPLY[status] ? REPLY[status](note) : "");
  const span = (s, e) => (s && e && s.slice(0, 16) !== e.slice(0, 16) ? `${fmtDateTime(s).slice(0, 16)} ~ ${e.slice(11, 16)}` : fmtDateTime(s).slice(0, 16));

  // ---- 민원 ------------------------------------------------------------------------------

  async function loadComplaints() {
    const data = await api("/api/admin/complaints", { category: $("cp-category").value });
    const sel = $("cp-category");
    // 카테고리가 바뀔 수 있으므로 매번 다시 채운다 (선택값 유지)
    const chosen = sel.value;
    sel.replaceChildren(el("option", { value: "", text: "전체 분류" }), ...data.categories.map((c) => el("option", { value: c, text: c })));
    sel.value = data.categories.includes(chosen) ? chosen : "";

    const roomOnly = $("cp-room-only").checked && KC.currentRoom();
    const items = roomOnly ? data.items.filter((g) => g.room_name === KC.currentRoom()) : data.items;
    const c = data.counts;
    $("cp-status").textContent =
      `문제 ${items.length}개 · 민원 메시지 ${items.reduce((n, g) => n + g.count, 0)}건 · 분류 대기 ${c.waiting} · 실패 ${c.failed}` +
      (data.gemini_configured ? ` · 워커: ${data.worker.state}${data.worker.last ? ` (${data.worker.last})` : ""}` : " · ⚠️ 서버에 Gemini API 키가 설정되지 않음");

    const list = $("cp-list");
    list.replaceChildren();
    if (!items.length) list.append(el("li", { class: "empty-row", text: "민원으로 분류된 메시지가 없습니다." }));
    for (const g of items) {
      const detail = el("div", { class: "cp-detail", hidden: true });
      const card = el(
        "li",
        { class: "cp-card" },
        el(
          "button",
          { class: "cp-head", type: "button", "aria-expanded": "false", onclick: (e) => toggleGroup(e.currentTarget, detail, g) },
          el("span", { class: "chip-cat", text: g.category }),
          el("strong", { class: "cp-summary", text: g.summary }),
          g.count > 1 ? el("span", { class: "chip-count", text: `${g.count}건` }) : null,
        ),
        el("p", { class: "cp-text", text: g.latest_text || "" }),
        el("p", { class: "cp-meta", text: `${g.room_name} · ${g.sender_name} · ${span(g.start_at, g.end_at)}` }),
        replyLabel(g.reply_status, g.reply_note) ? el("p", { class: "cp-reply", text: replyLabel(g.reply_status, g.reply_note) }) : null,
        ...(g.vendor_dispatch || []).map((m) =>
          el("p", { class: "cp-reply", text: `🏢 ${m.vendor} 톡방(${m.room}) 처리 요청: ${DISPATCH_LABEL[m.status] || m.status}${m.status !== "sent" && m.status !== "pending" && m.note ? ` — ${m.note}` : ""}` }),
        ),
        detail,
      );
      list.append(card);
    }
  }

  async function toggleGroup(button, detail, g) {
    const open = detail.hidden;
    detail.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    if (!open || detail.childElementCount) return;
    const data = await api("/api/admin/complaints/group", { ids: g.ids.join(",") });
    data.items.forEach((m, i) => {
      detail.append(
        el(
          "div",
          { class: "cp-item" },
          el("span", { class: "muted", text: `${i + 1}. ${fmtDateTime(m.posted_at).slice(0, 16)}` }),
          el("p", { text: m.message_text || "" }),
          el("button", { class: "btn btn-small", type: "button", text: "대화에서 보기", onclick: () => KC.openRoom(m.room_name, m.id) }),
        ),
      );
    });
  }

  // ---- 참여자 이력 ----------------------------------------------------------------------------

  const ICON = { join: "➕", leave: "➖", kick: "⛔", invite: "📨", rename: "✏️", first_seen: "💬" };

  async function loadMembers() {
    const mode = $("mb-mode").value;
    $("mb-type").hidden = mode !== "events";
    const room = $("mb-room-only").checked ? KC.currentRoom() : null;
    const list = $("mb-list");
    list.replaceChildren();
    if (mode === "members") {
      if (!KC.currentRoom()) {
        $("mb-status").textContent = "왼쪽에서 대화방을 먼저 선택하십시오.";
        return;
      }
      const data = await api("/api/admin/members", { room: KC.currentRoom() });
      $("mb-status").textContent = `${KC.currentRoom()} · 발언한 참여자 ${data.items.length}명 (카톡 알림으로 확인된 사람만)`;
      for (const m of data.items) {
        list.append(
          el(
            "li",
            { class: "mb-row" },
            el("strong", { text: m.display_name }),
            el("span", {
              class: "muted",
              text: `첫 발언 ${fmtDateTime(m.first_seen).slice(0, 16)} · 최근 ${fmtDateTime(m.last_seen).slice(0, 16)} · ${m.message_count}건${m.keyed ? "" : " · 대화명 변경 추적 불가"}`,
            }),
          ),
        );
      }
      return;
    }
    const data = await api("/api/admin/member-events", { room, type: $("mb-type").value });
    $("mb-status").textContent = `이력 ${data.items.length}건${room ? ` · ${room}` : " · 전체 방"}`;
    if (!data.items.length) list.append(el("li", { class: "empty-row", text: "기록된 이력이 없습니다." }));
    for (const e of data.items) {
      const title =
        e.type === "rename"
          ? `${e.old_name} → ${e.member}`
          : `${e.member} ${e.label}${e.actor ? ` (by ${e.actor})` : ""}`;
      list.append(
        el(
          "li",
          { class: "mb-row" },
          el("strong", { text: `${ICON[e.type] || ""} ${title}` }),
          el("span", { class: "muted", text: `${room ? "" : `${e.room_name} · `}${fmtDateTime(e.at).slice(0, 16)}` }),
        ),
      );
    }
  }

  // ---- 설정 -------------------------------------------------------------------------------

  async function loadSettings() {
    const s = await api("/api/admin/settings");
    $("set-complaint").checked = s.complaint_enabled;
    $("set-model").value = s.gemini_model;
    $("set-reply").checked = s.auto_reply_enabled;
    $("set-template").value = s.reply_template;
    $("set-vendor").checked = s.vendor_enabled;
    $("set-vendor-template").value = s.vendor_template;
    const chips = $("set-vendor-chips");
    if (!chips.childElementCount) {
      for (const [key, desc] of Object.entries(s.vendor_placeholders)) {
        chips.append(el("button", { class: "chip-insert", type: "button", title: desc, text: key, onclick: () => insertToken($("set-vendor-template"), key) }));
      }
      chips.append(el("button", { class: "chip-insert chip-reset", type: "button", text: "기본 문구", title: "기본 문구로 되돌리기 (저장해야 적용)", onclick: () => ($("set-vendor-template").value = s.vendor_default_template) }));
    }
    $("set-gemini").textContent = s.gemini_configured
      ? "서버에 Gemini API 키가 설정되어 있습니다. (키는 서버 .env 의 GEMINI_API_KEY)"
      : "⚠️ 서버에 Gemini API 키가 없습니다. 서버의 server/.env 에 GEMINI_API_KEY 를 넣고 서비스를 재시작하십시오.";
    $("set-worker").textContent = `${s.worker.state}${s.worker.last ? ` — ${s.worker.last}` : ""}`;
  }

  function insertToken(area, text) {
    const start = area.selectionStart ?? area.value.length;
    const end = area.selectionEnd ?? area.value.length;
    area.value = area.value.slice(0, start) + text + area.value.slice(end);
    area.focus();
    area.selectionStart = area.selectionEnd = start + text.length;
  }

  async function saveSettings(e) {
    e.preventDefault();
    await send("PUT", "/api/admin/settings", {
      complaint_enabled: $("set-complaint").checked,
      gemini_model: $("set-model").value.trim(),
      auto_reply_enabled: $("set-reply").checked,
      reply_template: $("set-template").value,
      vendor_enabled: $("set-vendor").checked,
      vendor_template: $("set-vendor-template").value,
    });
    $("set-saved").textContent = "저장했습니다.";
    setTimeout(() => ($("set-saved").textContent = ""), 3000);
    await loadSettings();
  }

  // ---- 연결 -------------------------------------------------------------------------------

  // 탭별 화면 읽기. admin-vendors.js 가 여기에 'vendors' 를 추가한다.
  const LOADERS = (KC.loaders = Object.assign(KC.loaders || {}, {
    complaints: loadComplaints,
    members: loadMembers,
    settings: loadSettings,
  }));
  // 실시간 신호가 왔을 때 다시 읽을 탭
  const LIVE_TOPICS = {
    complaints: ["message", "complaint", "category", "delete", "vendor"],
    stats: ["complaint", "category", "delete", "vendor"],
    members: ["message", "delete"],
    vendors: ["vendor", "category", "complaint"],
    categories: ["category", "complaint", "delete"],
    data: ["message", "delete"],
  };

  KC.onTab = (tab) => {
    const load = LOADERS[tab];
    if (load) load().catch(reportError);
    loaded[tab] = true;
  };

  KC.onLive = (topics) => {
    const tab = KC.currentTab();
    const load = LOADERS[tab];
    if (load && (LIVE_TOPICS[tab] || []).some((t) => topics.includes(t))) return load();
    return null;
  };

  $("cp-category").addEventListener("change", () => loadComplaints().catch(reportError));
  $("cp-room-only").addEventListener("change", () => loadComplaints().catch(reportError));
  $("cp-classify").addEventListener("click", () => send("POST", "/api/admin/classify").then(() => setTimeout(() => loadComplaints().catch(reportError), 3000)).catch(reportError));
  $("cp-retry").addEventListener("click", () =>
    send("POST", "/api/admin/classify?retry_failed=true").then(() => setTimeout(() => loadComplaints().catch(reportError), 3000)).catch(reportError),
  );
  $("mb-mode").addEventListener("change", () => loadMembers().catch(reportError));
  $("mb-type").addEventListener("change", () => loadMembers().catch(reportError));
  $("mb-room-only").addEventListener("change", () => loadMembers().catch(reportError));
  $("settings-form").addEventListener("submit", (e) => saveSettings(e).catch(reportError));

  // 실시간 신호와 별도로, 보고 있는 탭은 1분마다 새로고침 (설정·업체 탭 제외: 입력 중인 내용 보호)
  setInterval(() => {
    const tab = KC.currentTab();
    if (!document.hidden && !["settings", "vendors", "categories"].includes(tab) && LOADERS[tab]) LOADERS[tab]().catch(reportError);
  }, 60000);
})();
