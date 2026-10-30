"use strict";

// 민원 · 통계 · 참여자 이력 · 설정 탭. 서버 데이터는 텍스트 노드로만 넣는다(innerHTML 금지).
(() => {
  const $ = (id) => document.getElementById(id);
  const KC = window.KC;
  const { api, el, reportError, fmtDateTime } = KC;
  const loaded = {};
  let statsPeriod = "week";

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
  const replyLabel = (status, note) => (REPLY[status] ? REPLY[status](note) : "");
  const span = (s, e) => (s && e && s.slice(0, 16) !== e.slice(0, 16) ? `${fmtDateTime(s).slice(0, 16)} ~ ${e.slice(11, 16)}` : fmtDateTime(s).slice(0, 16));

  // ---- 민원 ------------------------------------------------------------------------------

  async function loadComplaints() {
    const data = await api("/api/admin/complaints", { category: $("cp-category").value });
    const sel = $("cp-category");
    if (sel.options.length === 1) for (const c of data.categories) sel.append(el("option", { value: c, text: c }));

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

  // ---- 통계 (단일 계열: 막대 한 가지 색, 글자는 텍스트 색) -----------------------------------------

  function bars(rows, total) {
    if (!rows.length) return el("p", { class: "muted", text: "데이터 없음" });
    const max = Math.max(...rows.map((r) => r[1]), 1);
    const wrap = el("div", { class: "hbars" });
    for (const [name, v] of rows) {
      const fill = el("span", { class: "hbar-fill" });
      fill.style.width = `${(v / max) * 100}%`;
      wrap.append(
        el(
          "div",
          { class: "hbar", title: `${name}: ${v}건` },
          el("span", { class: "hbar-label", text: name }),
          el("span", { class: "hbar-track" }, fill),
          el("span", { class: "hbar-value", text: `${v}건 (${total ? Math.round((v * 100) / total) : 0}%)` }),
        ),
      );
    }
    return wrap;
  }

  function columns(points, every) {
    const max = Math.max(...points.map((p) => p[1]), 1);
    const plot = el("div", { class: "cols" });
    for (const [label, v] of points) {
      const bar = el("span", { class: "col-bar" });
      bar.style.height = v ? `${Math.max((v / max) * 100, 2)}%` : "0";
      plot.append(el("div", { class: "col", title: `${label}: ${v}건`, tabindex: "0" }, bar));
    }
    const axis = el("div", { class: "cols-axis" });
    points.forEach(([label], i) => axis.append(el("span", { text: i % every === 0 || i === points.length - 1 ? label : "" })));
    return el("div", {}, el("p", { class: "muted small", text: `최대 ${max}건 · 막대에 마우스를 올리면 값 표시` }), plot, axis);
  }

  async function loadStats() {
    for (const b of $("st-periods").children) b.setAttribute("aria-pressed", String(b.dataset.period === statsPeriod));
    const s = await api("/api/admin/stats", { period: statsPeriod });
    const label = { today: "오늘", week: "최근 7일", month: "최근 30일", all: "전체" }[statsPeriod];
    const body = $("st-body");
    body.replaceChildren(
      el("p", { class: "muted", text: `${label} 접수된 문제` }),
      el("p", { class: "hero", text: `${s.issues}건` }),
      el(
        "div",
        { class: "tiles" },
        ...[
          ["민원 메시지", s.messages],
          ["반복 민원", s.repeated_issues],
          ["안내 보냄", s.reply_sent],
          ["안내 실패", s.reply_failed],
        ].map(([k, v]) => el("div", { class: "tile" }, el("strong", { text: String(v) }), el("span", { text: k }))),
      ),
      el("h3", { text: "분류별 문제" }),
      bars(s.by_category, s.issues),
      el("h3", { text: statsPeriod === "all" ? "일별 추이 (최근 30일)" : "일별 추이" }),
      columns(s.daily, Math.max(1, Math.floor(s.daily.length / 6))),
      el("h3", { text: "시간대별 (문제 시작 시각)" }),
      columns(s.hourly.map((v, h) => [`${h}시`, v]), 6),
      el("h3", { text: "방별 문제 TOP 10" }),
      bars(s.by_room, s.issues),
      el("h3", { text: "민원 제기자 TOP 10" }),
      bars(s.by_sender, s.issues),
    );
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
    $("set-gemini").textContent = s.gemini_configured
      ? "서버에 Gemini API 키가 설정되어 있습니다. (키는 서버 .env 의 GEMINI_API_KEY)"
      : "⚠️ 서버에 Gemini API 키가 없습니다. 서버의 server/.env 에 GEMINI_API_KEY 를 넣고 서비스를 재시작하십시오.";
    $("set-worker").textContent = `${s.worker.state}${s.worker.last ? ` — ${s.worker.last}` : ""}`;
  }

  async function saveSettings(e) {
    e.preventDefault();
    await send("PUT", "/api/admin/settings", {
      complaint_enabled: $("set-complaint").checked,
      gemini_model: $("set-model").value.trim(),
      auto_reply_enabled: $("set-reply").checked,
      reply_template: $("set-template").value,
    });
    $("set-saved").textContent = "저장했습니다.";
    setTimeout(() => ($("set-saved").textContent = ""), 3000);
    await loadSettings();
  }

  // ---- 연결 -------------------------------------------------------------------------------

  const LOADERS = { complaints: loadComplaints, stats: loadStats, members: loadMembers, settings: loadSettings };

  KC.onTab = (tab) => {
    const load = LOADERS[tab];
    if (load) load().catch(reportError);
    loaded[tab] = true;
  };

  $("cp-category").addEventListener("change", () => loadComplaints().catch(reportError));
  $("cp-room-only").addEventListener("change", () => loadComplaints().catch(reportError));
  $("cp-classify").addEventListener("click", () => send("POST", "/api/admin/classify").then(() => setTimeout(() => loadComplaints().catch(reportError), 3000)).catch(reportError));
  $("cp-retry").addEventListener("click", () =>
    send("POST", "/api/admin/classify?retry_failed=true").then(() => setTimeout(() => loadComplaints().catch(reportError), 3000)).catch(reportError),
  );
  for (const b of $("st-periods").children) {
    b.addEventListener("click", () => {
      statsPeriod = b.dataset.period;
      loadStats().catch(reportError);
    });
  }
  $("mb-mode").addEventListener("change", () => loadMembers().catch(reportError));
  $("mb-type").addEventListener("change", () => loadMembers().catch(reportError));
  $("mb-room-only").addEventListener("change", () => loadMembers().catch(reportError));
  $("settings-form").addEventListener("submit", (e) => saveSettings(e).catch(reportError));

  // 보고 있는 탭은 30초마다 새로고침 (설정 탭 제외)
  setInterval(() => {
    const tab = KC.currentTab();
    if (!document.hidden && tab !== "settings" && LOADERS[tab]) LOADERS[tab]().catch(reportError);
  }, 30000);
})();
