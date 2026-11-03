"use strict";

// '예약 메시지' 탭: 정한 날짜·요일·시각에 채팅방으로 메시지 보내기. 서버 데이터는 텍스트 노드로만 넣는다(innerHTML 금지).
(() => {
  const $ = (id) => document.getElementById(id);
  const KC = window.KC;
  const { api, el, reportError, fmtDateTime } = KC;
  const DAYS = ["월", "화", "수", "목", "금", "토", "일"];
  const TOKENS = { "{날짜}": "보내는 날 (예: 11월 3일)", "{요일}": "보내는 요일 (예: 화요일)", "{시각}": "보내는 시각 (예: 09:00)" };
  const STATUS = { pending: ["전송 대기", "wait"], sent: ["보냄", "ok"], failed: ["실패", "bad"], expired: ["만료", "bad"] };
  const state = { items: [], rooms: [], editing: null, kind: "weekly", days: new Set([0]) };

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

  // ---- 다음 발송 시각 (서버와 같은 규칙, 미리보기용) -------------------------------------------------

  const pad = (n) => String(n).padStart(2, "0");
  const at = (d, hhmm) => {
    const [h, m] = hhmm.split(":").map(Number);
    return new Date(d.getFullYear(), d.getMonth(), d.getDate(), h, m);
  };
  const dow = (d) => (d.getDay() + 6) % 7; // 월=0

  function nextRun(spec, after = new Date()) {
    if (!/^\d{2}:\d{2}$/.test(spec.time || "")) return null;
    if (spec.kind === "once") {
      if (!spec.date) return null;
      const [y, mo, d] = spec.date.split("-").map(Number);
      const w = at(new Date(y, mo - 1, d), spec.time);
      return w > after ? w : null;
    }
    if (spec.kind === "daily") {
      const w = at(after, spec.time);
      return w > after ? w : new Date(w.getTime() + 86400000);
    }
    if (spec.kind === "weekly") {
      for (let i = 0; i < 8; i++) {
        const d = new Date(after.getFullYear(), after.getMonth(), after.getDate() + i);
        if (spec.days.includes(dow(d)) && at(d, spec.time) > after) return at(d, spec.time);
      }
      return null;
    }
    if (spec.kind === "monthly" && spec.mday) {
      let y = after.getFullYear();
      let m = after.getMonth();
      for (let i = 0; i < 14; i++) {
        const last = new Date(y, m + 1, 0).getDate();
        const w = at(new Date(y, m, Math.min(spec.mday, last)), spec.time);
        if (w > after) return w;
        m += 1;
        if (m === 12) [y, m] = [y + 1, 0];
      }
    }
    return null;
  }

  const fmtWhen = (d) => `${d.getMonth() + 1}/${d.getDate()} (${DAYS[dow(d)]}) ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const renderText = (text, d) =>
    text.replaceAll("{날짜}", `${d.getMonth() + 1}월 ${d.getDate()}일`).replaceAll("{요일}", `${DAYS[dow(d)]}요일`).replaceAll("{시각}", `${pad(d.getHours())}:${pad(d.getMinutes())}`);

  // ---- 목록 ------------------------------------------------------------------------------------

  function kpi(icon, tone, label, value, unit) {
    return el(
      "div",
      { class: `kpi-card tone-${tone}` },
      el("div", { class: "kpi-text" }, el("span", { class: "kpi-label", text: label }), el("div", { class: "kpi-num" }, el("strong", { text: String(value) }), el("span", { text: unit }))),
      el("span", { class: "kpi-ico ms", "aria-hidden": "true", text: icon }),
    );
  }

  async function loadSchedules() {
    const [data, hist, rooms] = await Promise.all([api("/api/admin/schedules"), api("/api/admin/schedules/history", { limit: 30 }), api("/api/admin/rooms")]);
    state.items = data.items;
    state.rooms = rooms.items.map((r) => r.room_name);
    const today = new Date().toISOString().slice(0, 10);
    const todayLocal = `${new Date().getFullYear()}-${pad(new Date().getMonth() + 1)}-${pad(new Date().getDate())}`;
    const active = state.items.filter((s) => s.enabled);
    const failed = state.items.filter((s) => s.last_send && ["failed", "expired"].includes(s.last_send.status)).length;
    $("sc-kpis").replaceChildren(
      kpi("event_repeat", "blue", "예약", state.items.length, "건"),
      kpi("toggle_on", "indigo", "사용 중", active.length, `/ ${state.items.length}`),
      kpi("schedule", "green", "오늘 보낼 예약", active.filter((s) => (s.next_run_at || "").startsWith(todayLocal) || (s.next_run_at || "").startsWith(today)).length, "건"),
      kpi("warning", failed ? "rose" : "slate", "최근 실패", failed, "건"),
    );
    renderList();
    renderHistory(hist.items);
  }

  function renderList() {
    $("sc-count-pill").textContent = `${state.items.length}건`;
    const list = $("sc-list");
    list.replaceChildren();
    if (!state.items.length) {
      list.append(el("tr", {}, el("td", { colspan: "7", class: "empty-cell", text: "아직 예약이 없습니다. 오른쪽 위 '예약 추가'를 누르십시오." })));
      return;
    }
    const roomSet = new Set(state.rooms);
    for (const s of state.items) {
      const toggle = el("input", { type: "checkbox", class: "switch-toggle", checked: s.enabled, "aria-label": `${s.title} 사용` });
      toggle.addEventListener("change", async () => {
        try {
          await send("POST", `/api/admin/schedules/${s.id}/enabled`, { enabled: toggle.checked });
          await loadSchedules();
        } catch (err) {
          toggle.checked = !toggle.checked;
          reportError(err);
        }
      });
      const last = s.last_send;
      const [label, tone] = last ? STATUS[last.status] || [last.status, "wait"] : ["—", ""];
      const lastCell = last
        ? el("div", { class: "sc-last" }, el("span", { class: `status-badge ${tone}`, text: label }), el("span", { class: "muted small", text: fmtDateTime(last.done_at || last.created_at).slice(5, 16) }))
        : el("span", { class: "muted small", text: s.last_note || "아직 보내지 않음" });
      list.append(
        el(
          "tr",
          { class: s.enabled ? "" : "row-off" },
          el("td", { class: "sc-title-cell" }, el("strong", { text: s.title }), el("span", { class: "muted small sc-snippet", text: s.text.replace(/\s+/g, " ").slice(0, 60) })),
          el("td", {}, KC.roomChip(s.room_name, roomSet.has(s.room_name))),
          el("td", {}, el("span", { class: "sc-repeat" }, el("span", { class: "ms", "aria-hidden": "true", text: s.kind === "once" ? "event" : "event_repeat" }), s.summary)),
          el("td", { class: "small", text: s.enabled && s.next_run_at ? fmtDateTime(s.next_run_at).slice(5, 16) : "—" }),
          el("td", {}, lastCell),
          el("td", { class: "center-col" }, toggle),
          el(
            "td",
            { class: "right-col" },
            el(
              "div",
              { class: "row-actions" },
              el("button", { class: "btn btn-small", type: "button", title: "예약과 상관없이 지금 한 번 보냅니다", onclick: () => sendNow(s).catch(reportError) }, el("span", { class: "ms", text: "send" }), "지금 보내기"),
              el("button", { class: "btn btn-small", type: "button", onclick: () => openForm(s) }, el("span", { class: "ms", text: "edit" }), "수정"),
              el("button", { class: "btn btn-small btn-danger", type: "button", text: "삭제", onclick: () => remove(s).catch(reportError) }),
            ),
          ),
        ),
      );
    }
  }

  function renderHistory(items) {
    const list = $("sc-history");
    list.replaceChildren();
    if (!items.length) {
      list.append(el("li", { class: "empty-row", text: "아직 보낸 예약 메시지가 없습니다." }));
      return;
    }
    for (const h of items) {
      const [label, tone] = STATUS[h.status] || [h.status, "wait"];
      list.append(
        el(
          "li",
          { class: "dispatch-item" },
          el("span", { class: `status-badge ${tone}`, text: label }),
          el(
            "div",
            { class: "dispatch-main" },
            el("div", { class: "dispatch-title" }, el("strong", { text: h.title || "예약 메시지" }), el("span", { class: "muted", text: " → " }), KC.roomChip(h.room_name)),
            el("div", { class: "muted small", text: fmtDateTime(h.done_at || h.created_at).slice(0, 16) }),
            h.note && h.status !== "sent" ? el("div", { class: `small ${tone === "bad" ? "note-bad" : "muted"}`, text: h.note }) : null,
            el("details", {}, el("summary", { text: "보낸 문구" }), el("pre", { class: "dispatch-text", text: h.text })),
          ),
        ),
      );
    }
  }

  async function sendNow(s) {
    if (!confirm(`'${s.title}' 을(를) 지금 '${s.room_name}' 방으로 보내시겠습니까?`)) return;
    await send("POST", `/api/admin/schedules/${s.id}/send-now`);
    await loadSchedules();
  }

  async function remove(s) {
    if (!confirm(`'${s.title}' 예약을 삭제하시겠습니까?`)) return;
    await send("DELETE", `/api/admin/schedules/${s.id}`);
    if (state.editing?.id === s.id) closeForm();
    await loadSchedules();
  }

  // ---- 추가·수정 폼 ---------------------------------------------------------------------------------

  function roomOptions(current) {
    const sel = $("sc-room");
    const rooms = [...new Set([...(current ? [current] : []), ...state.rooms])];
    const groups = [
      ["아파트 (★)", (r) => /^\s*[★☆⭐]/.test(r)],
      ["민원처리업체 (●)", (r) => /^\s*[●⚫⬤🔴🟠🟡🟢🔵🟣🟤]/u.test(r)],
      ["그 밖의 방", () => true],
    ];
    sel.replaceChildren(el("option", { value: "", text: "채팅방을 고르십시오" }));
    const used = new Set();
    for (const [label, test] of groups) {
      const g = el("optgroup", { label });
      for (const r of rooms) if (!used.has(r) && test(r)) { g.append(el("option", { value: r, text: r })); used.add(r); }
      if (g.childElementCount) sel.append(g);
    }
    sel.value = current || "";
  }

  function setKind(kind) {
    state.kind = kind;
    for (const b of $("sc-kind").children) {
      b.setAttribute("aria-checked", String(b.dataset.kind === kind));
      b.classList.toggle("on", b.dataset.kind === kind);
    }
    $("sc-date-wrap").hidden = kind !== "once";
    $("sc-week-wrap").hidden = kind !== "weekly";
    $("sc-month-wrap").hidden = kind !== "monthly";
    updatePreview();
  }

  function renderDays() {
    const box = $("sc-days");
    box.replaceChildren(
      ...DAYS.map((d, i) => {
        const b = el("button", { type: "button", class: `day-chip${state.days.has(i) ? " on" : ""}${i >= 5 ? " weekend" : ""}`, "aria-pressed": String(state.days.has(i)), text: d });
        b.addEventListener("click", () => {
          state.days.has(i) ? state.days.delete(i) : state.days.add(i);
          renderDays();
          updatePreview();
        });
        return b;
      }),
    );
  }

  function spec() {
    return { kind: state.kind, time: $("sc-time").value, date: $("sc-date").value, days: [...state.days].sort(), mday: Number($("sc-mday").value) || null };
  }

  function summaryText(sp) {
    const t = sp.time || "--:--";
    if (sp.kind === "once") return sp.date ? `${sp.date} ${t}에 한 번` : "날짜를 고르십시오";
    if (sp.kind === "daily") return `매일 ${t}`;
    if (sp.kind === "weekly") {
      const d = sp.days.join(",");
      const label = d === "0,1,2,3,4" ? "평일" : d === "5,6" ? "주말" : d === "0,1,2,3,4,5,6" ? "매일" : sp.days.map((i) => DAYS[i]).join("·");
      return sp.days.length ? `매주 ${label} ${t}` : "요일을 고르십시오";
    }
    return sp.mday ? `매월 ${sp.mday}일 ${t}` : "며칠인지 입력하십시오";
  }

  function updatePreview() {
    const sp = spec();
    const next = nextRun(sp);
    const room = $("sc-room").value;
    const text = $("sc-text").value;
    $("sc-count").textContent = `${text.length} / 1000자`;
    $("sc-preview-when").textContent = next ? `(${fmtWhen(next)} 기준)` : "";
    $("sc-preview").textContent = text.trim() ? renderText(text, next || new Date()) : "메시지를 입력하면 여기에 보낼 모양이 보입니다.";
    $("sc-preview").classList.toggle("empty", !text.trim());
    const sum = $("sc-summary");
    sum.replaceChildren(
      el("span", { class: "ms", "aria-hidden": "true", text: "schedule" }),
      el("b", { text: summaryText(sp) }),
      room ? ` → '${room}' 방으로 보냅니다.` : " → 보낼 채팅방을 고르십시오.",
      next ? el("span", { class: "sc-next", text: ` 다음 발송: ${fmtWhen(next)}` }) : sp.kind === "once" && sp.date && sp.time ? el("span", { class: "note-bad", text: " 이미 지난 시각입니다." }) : "",
    );
  }

  function openForm(s = null) {
    state.editing = s;
    $("sc-form-title").textContent = s ? `예약 수정 — ${s.title}` : "예약 추가";
    roomOptions(s?.room_name || "");
    $("sc-title").value = s?.title || "";
    $("sc-text").value = s?.text || "";
    $("sc-time").value = s?.run_time || "09:00";
    const tomorrow = new Date(Date.now() + 86400000);
    $("sc-date").value = s?.run_date || `${tomorrow.getFullYear()}-${pad(tomorrow.getMonth() + 1)}-${pad(tomorrow.getDate())}`;
    $("sc-mday").value = s?.month_day || 1;
    $("sc-enabled").checked = s ? s.enabled : true;
    state.days = new Set(s?.weekdays?.length ? s.weekdays : [0]);
    renderDays();
    setKind(s?.kind || "weekly");
    $("sc-error").textContent = "";
    $("sc-form").hidden = false;
    $("sc-form").scrollIntoView?.({ block: "start", behavior: "smooth" });
    $("sc-room").focus({ preventScroll: true });
  }

  function closeForm() {
    state.editing = null;
    $("sc-form").hidden = true;
  }

  async function save(e) {
    e.preventDefault();
    const sp = spec();
    const body = {
      title: $("sc-title").value.trim(),
      room_name: $("sc-room").value,
      text: $("sc-text").value,
      kind: sp.kind,
      run_time: sp.time,
      run_date: sp.kind === "once" ? sp.date : null,
      weekdays: sp.kind === "weekly" ? sp.days : [],
      month_day: sp.kind === "monthly" ? sp.mday : null,
      enabled: $("sc-enabled").checked,
    };
    const problem = !body.room_name ? "보낼 채팅방을 고르십시오." : !body.text.trim() ? "메시지를 입력하십시오." : !body.run_time ? "보낼 시각을 입력하십시오." : null;
    if (problem) {
      $("sc-error").textContent = problem;
      return;
    }
    $("sc-save").disabled = true;
    try {
      await send(state.editing ? "PUT" : "POST", state.editing ? `/api/admin/schedules/${state.editing.id}` : "/api/admin/schedules", body);
      closeForm();
      await loadSchedules();
    } catch (err) {
      $("sc-error").textContent = err.message;
    } finally {
      $("sc-save").disabled = false;
    }
  }

  // ---- 연결 -------------------------------------------------------------------------------------

  KC.loaders = Object.assign(KC.loaders || {}, { schedules: loadSchedules });
  $("sc-new").addEventListener("click", () => openForm());
  $("sc-cancel").addEventListener("click", closeForm);
  $("sc-form").addEventListener("submit", (e) => save(e).catch(reportError));
  for (const b of $("sc-kind").children) b.addEventListener("click", () => setKind(b.dataset.kind));
  for (const b of document.querySelectorAll(".quick-days .link-btn")) {
    b.addEventListener("click", () => {
      state.days = new Set(b.dataset.days ? b.dataset.days.split(",").map(Number) : []);
      renderDays();
      updatePreview();
    });
  }
  for (const id of ["sc-room", "sc-text", "sc-time", "sc-date", "sc-mday"]) $(id).addEventListener("input", updatePreview);
  $("sc-room").addEventListener("change", updatePreview);
  $("sc-tokens").append(
    ...Object.entries(TOKENS).map(([k, desc]) =>
      el("button", {
        class: "chip-insert",
        type: "button",
        title: desc,
        text: k,
        onclick: () => {
          const area = $("sc-text");
          const s = area.selectionStart ?? area.value.length;
          area.value = area.value.slice(0, s) + k + area.value.slice(area.selectionEnd ?? s);
          area.focus();
          area.selectionStart = area.selectionEnd = s + k.length;
          updatePreview();
        },
      }),
    ),
  );
})();
