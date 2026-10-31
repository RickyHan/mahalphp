"use strict";

// '문자 발송' 탭: 뿌리오 연동 상태 · 발신번호 · 템플릿(미리보기) · 테스트 발송 · 발송 이력.
// 서버 데이터는 텍스트 노드로만 넣는다(innerHTML 금지).
(() => {
  const $ = (id) => document.getElementById(id);
  const KC = window.KC;
  const { api, el, reportError, fmtDateTime } = KC;
  const STATUS = { waiting: "카톡 안내 대기", pending: "발송 대기", sending: "발송 중", sent: "발송 완료", failed: "실패" };
  let saved = null; // 서버에 저장된 값 (입력 중인지 판단)
  let previewTimer = null;
  let checked = false; // '연결 확인'을 눌러 본 적이 있는지

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

  const dirty = () =>
    saved &&
    ($("sms-template").value !== saved.template || $("sms-sender").value !== saved.sender || $("sms-enabled").checked !== saved.enabled);

  // ---- 설정 -------------------------------------------------------------------------------

  async function loadSms() {
    const s = await api("/api/admin/sms");
    if (!dirty()) {
      saved = { template: s.template, sender: s.sender, enabled: s.enabled, defaultTemplate: s.default_template };
      $("sms-enabled").checked = s.enabled;
      $("sms-sender").value = s.sender;
      $("sms-template").value = s.template;
      renderPreview(s.preview, s.limits);
    }
    saved.defaultTemplate = s.default_template;
    renderChips(s.placeholders);
    const conn = $("sms-conn");
    if (!s.configured) {
      conn.dataset.ok = "false";
      conn.textContent = "연동 정보 없음";
      $("sms-conn-text").textContent = "서버 .env 에 PPURIO_ACCOUNT · PPURIO_API_KEY 를 넣고 서비스를 재시작하십시오.";
    } else if (!checked) {
      conn.dataset.ok = "unknown";
      conn.textContent = `계정 ${s.account}`;
      $("sms-conn-text").textContent = `업체 문자 대상: 카테고리·전화번호가 지정된 업체 ${s.linked_vendors}곳${s.last ? ` · 최근 ${s.last}` : ""}`;
    }
    $("sms-check").disabled = !s.configured;
    await loadHistory();
  }

  function renderChips(placeholders) {
    const box = $("sms-chips");
    if (box.childElementCount) return;
    for (const [key, desc] of Object.entries(placeholders)) {
      box.append(
        el("button", {
          class: "chip-insert",
          type: "button",
          title: desc,
          text: key,
          onclick: () => insertAtCursor($("sms-template"), key),
        }),
      );
    }
  }

  function insertAtCursor(area, text) {
    const start = area.selectionStart ?? area.value.length;
    const end = area.selectionEnd ?? area.value.length;
    area.value = area.value.slice(0, start) + text + area.value.slice(end);
    area.focus();
    area.selectionStart = area.selectionEnd = start + text.length;
    schedulePreview();
  }

  function renderPreview(p, limits) {
    $("sms-preview").textContent = p.text;
    $("sms-type").textContent = p.type;
    $("sms-type").dataset.type = p.type;
    const limit = p.type === "SMS" ? (limits?.sms ?? 90) : (limits?.lms ?? 2000);
    $("sms-bytes").textContent = `${p.bytes} / ${limit} byte${p.type === "LMS" ? " (90byte 초과 → 장문 LMS 요금)" : ""}${p.too_long ? " · 너무 깁니다" : ""}`;
    $("sms-bytes").classList.toggle("over", p.too_long);
  }

  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(async () => {
      try {
        const p = await send("POST", "/api/admin/sms/preview", { template: $("sms-template").value });
        renderPreview(p);
      } catch (err) {
        $("sms-error").textContent = err.message;
      }
    }, 300);
  }

  async function save(e) {
    e.preventDefault();
    $("sms-error").textContent = "";
    try {
      await send("PUT", "/api/admin/sms", {
        enabled: $("sms-enabled").checked,
        sender: $("sms-sender").value.trim(),
        template: $("sms-template").value,
      });
      saved = null;
      await loadSms();
      $("sms-saved").textContent = "저장했습니다.";
      setTimeout(() => ($("sms-saved").textContent = ""), 3000);
    } catch (err) {
      $("sms-error").textContent = err.message;
    }
  }

  async function check() {
    const conn = $("sms-conn");
    conn.textContent = "확인 중…";
    conn.dataset.ok = "unknown";
    $("sms-check").disabled = true;
    let r;
    try {
      r = await send("POST", "/api/admin/sms/check");
    } catch (err) {
      r = { ok: false, message: `서버 요청 실패: ${err.message}` };
    } finally {
      $("sms-check").disabled = false;
    }
    checked = true;
    conn.dataset.ok = r.ok ? "true" : "false";
    conn.textContent = r.ok ? "연결 정상" : "연결 실패";
    $("sms-conn-text").textContent = `${r.message}${r.user_ip ? ` · 뿌리오가 본 이 서버 IP: ${r.user_ip}` : ""}`;
  }

  async function testSend() {
    const to = $("sms-test-to").value.trim();
    $("sms-test-result").textContent = "보내는 중…";
    try {
      const r = await send("POST", "/api/admin/sms/test", { to, template: $("sms-template").value });
      $("sms-test-result").textContent = `${r.ok ? "✅" : "⚠️"} ${r.message}${r.user_ip ? ` (서버 IP ${r.user_ip})` : ""}`;
    } catch (err) {
      $("sms-test-result").textContent = `⚠️ ${err.message}`;
    }
    await loadHistory();
  }

  // ---- 이력 -------------------------------------------------------------------------------

  async function loadHistory() {
    const data = await api("/api/admin/sms/history", { limit: 100 });
    const c = data.counts;
    $("sms-counts").textContent = `발송 완료 ${c.sent || 0} · 실패 ${c.failed || 0} · 대기 ${(c.waiting || 0) + (c.pending || 0)}`;
    const list = $("sms-history");
    list.replaceChildren();
    if (!data.items.length) {
      list.append(el("li", { class: "empty-row", text: "아직 보낸 문자가 없습니다." }));
      return;
    }
    for (const m of data.items) {
      const retry =
        m.status === "failed" && m.message_id
          ? el("button", {
              class: "btn btn-small",
              type: "button",
              text: "다시 보내기",
              onclick: () => send("POST", `/api/admin/sms/${m.id}/retry`).then(loadHistory).catch(reportError),
            })
          : null;
      list.append(
        el(
          "li",
          { class: "sms-item" },
          el(
            "div",
            { class: "sms-item-head" },
            el("span", { class: `sms-state sms-${m.status}`, text: STATUS[m.status] || m.status }),
            el("strong", { text: m.vendor_name || "-" }),
            el("span", { class: "muted", text: m.phone }),
            m.msg_type ? el("span", { class: "muted small", text: m.msg_type }) : null,
            el("span", { class: "muted small sms-time", text: fmtDateTime(m.done_at || m.created_at).slice(0, 16) }),
          ),
          el("p", { class: "muted small", text: `${m.room_name} · ${m.category}` }),
          el("details", {}, el("summary", { text: "문자 내용" }), el("pre", { class: "sms-text", text: m.text })),
          m.note ? el("p", { class: `small ${m.status === "failed" ? "sms-note-bad" : "muted"}`, text: m.note }) : null,
          retry,
        ),
      );
    }
  }

  // ---- 연결 -------------------------------------------------------------------------------

  KC.loaders = Object.assign(KC.loaders || {}, { sms: loadSms });
  $("sms-form").addEventListener("submit", (e) => save(e).catch(reportError));
  $("sms-template").addEventListener("input", schedulePreview);
  $("sms-reset").addEventListener("click", () => {
    if (!saved || !confirm("기본 문구로 되돌리시겠습니까? (저장을 눌러야 적용됩니다)")) return;
    $("sms-template").value = saved.defaultTemplate;
    schedulePreview();
  });
  $("sms-check").addEventListener("click", () => check().catch(reportError));
  $("sms-test").addEventListener("click", () => testSend().catch(reportError));
})();
