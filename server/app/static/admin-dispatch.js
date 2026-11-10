"use strict";

// '업체 발송 이력' 탭과 '시스템 설정 > 업체 문자'. 서버 데이터는 텍스트 노드로만 넣는다(innerHTML 금지).
(() => {
  const $ = (id) => document.getElementById(id);
  const KC = window.KC;
  const { api, el, reportError, fmtDateTime } = KC;

  async function send(method, path, body) {
    const res = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) location.href = "/admin/login";
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(", ") : data.detail) || `요청 실패 (HTTP ${res.status})`);
    return data;
  }

  const fmtPhone = (p) => (p || "").replace(/^(01\d)(\d{3,4})(\d{4})$/, "$1-$2-$3").replace(/^(02)(\d{3,4})(\d{4})$/, "$1-$2-$3");

  // ---- 업체 발송 이력 ---------------------------------------------------------------------------

  let view = "all";
  const STATUS = {
    sms: { sent: ["문자 보냄", "ok"], pending: ["문자 대기", "wait"], failed: ["문자 실패", "bad"] },
    kakao: { sent: ["카톡 보냄", "ok"], pending: ["카톡 대기", "wait"], failed: ["카톡 실패", "bad"], expired: ["카톡 만료", "bad"] },
  };

  function row(h) {
    const [label, tone] = STATUS[h.channel][h.status] || [h.status, "wait"];
    const where =
      h.channel === "sms"
        ? `📱 ${fmtPhone(h.phone)}${h.test && h.target_phone ? ` (실제 대상 ${fmtPhone(h.target_phone)})` : ""}`
        : `💬 ${h.vendor_room || "-"}`;
    const complaint = [h.room_name, h.category, h.summary].filter(Boolean).join(" · ");
    const retry =
      h.channel === "sms" && h.status === "failed" && !h.fallback_id
        ? el("button", { class: "btn btn-small", type: "button", text: "다시 보내기", onclick: () => send("POST", `/api/admin/sms/${h.id}/retry`).then(loadDispatch).catch(reportError) })
        : h.channel === "kakao" && (h.status === "failed" || h.status === "expired")
          ? el("button", { class: "btn btn-small", type: "button", text: "다시 보내기", onclick: () => send("POST", `/api/admin/vendor-dispatch/${h.id}/retry`).then(loadDispatch).catch(reportError) })
          : null;
    return el(
      "li",
      { class: "dispatch-item dp-item" },
      el("span", { class: `status-badge ${tone}`, text: label }),
      el(
        "div",
        { class: "dispatch-main" },
        el(
          "div",
          { class: "dispatch-title" },
          h.test ? el("span", { class: "chip-count", text: "테스트" }) : null,
          el("strong", { text: h.vendor_name || (h.test ? "테스트 문자" : "-") }),
          el("span", { class: "muted", text: " → " }),
          el("span", { text: where }),
          h.after_sms_failed ? el("span", { class: "muted small", text: " (문자 실패 후 카톡으로)" }) : null,
        ),
        el("div", { class: "muted small", text: `${complaint || "민원 없음"} · ${fmtDateTime(h.done_at || h.created_at).slice(0, 16)}` }),
        h.note && h.status !== "sent" ? el("div", { class: `small ${tone === "bad" ? "note-bad" : "muted"}`, text: h.note }) : null,
        el("details", {}, el("summary", { text: h.status === "sent" ? "보낸 문구" : "보낼 문구" }), el("pre", { class: "dispatch-text", text: h.text })),
        h.channel === "kakao" && h.status === "pending"
          ? el("div", { class: "dispatch-ready" }, KC.readyBadge(h.reply_ready), KC.dispatchActions({ id: h.id, text: h.text }, loadDispatch))
          : null,
      ),
      retry,
    );
  }

  async function loadDispatch() {
    const [data, conf] = await Promise.all([api("/api/admin/dispatch-history", { view, limit: 300 }), api("/api/admin/sms")]);
    for (const b of document.querySelectorAll("#dp-views button")) {
      b.classList.toggle("on", b.dataset.view === view);
      b.querySelector("span").textContent = (data.counts[b.dataset.view] ?? 0).toLocaleString();
    }
    const mode = $("dp-mode");
    mode.hidden = false;
    mode.classList.toggle("has-new", conf.enabled && conf.test_mode);
    mode.textContent = !conf.enabled
      ? "업체 문자 꺼짐 — 모든 처리 요청을 업체 카톡방으로 보냅니다. (시스템 설정 > 업체 문자)"
      : conf.test_mode
        ? `테스트 모드 — 문자는 모두 ${fmtPhone(conf.test_number)} 로만 보내고, 업체 카톡방 요청은 예전처럼 보냅니다.`
        : `문자 우선 — 건물×업체 담당자 번호(건물 관리 > 담당 업체)가 있으면 문자, 없으면 카톡방. 건물 담당자 등록 ${conf.building_contacts}건.`;
    if (conf.enabled && !conf.configured) mode.textContent += " ⚠️ 뿌리오 연동 정보가 없어 문자가 실패합니다.";
    const list = $("dp-list");
    list.replaceChildren();
    if (!data.items.length) list.append(el("li", { class: "empty-row", text: "이 보기에 해당하는 기록이 없습니다." }));
    for (const h of data.items) list.append(row(h));
  }

  for (const b of document.querySelectorAll("#dp-views button")) {
    b.addEventListener("click", () => {
      view = b.dataset.view;
      loadDispatch().catch(reportError);
    });
  }

  // ---- 시스템 설정 > 업체 문자 -------------------------------------------------------------------

  function fillSms(s) {
    $("sms-enabled").checked = s.enabled;
    $("sms-from").value = fmtPhone(s.sender);
    $("sms-test").checked = s.test_mode;
    $("sms-test-number").value = fmtPhone(s.test_number);
    $("sms-account").value = "";
    $("sms-key").value = "";
    $("sms-state").textContent = s.configured
      ? `뿌리오 연동: 설정됨 (계정 ${s.account}${s.from_env ? " · 서버 .env" : " · 관리자 웹"}) · 보냄 ${s.counts.sent || 0} · 대기 ${s.counts.pending || 0} · 실패 ${s.counts.failed || 0}`
      : "뿌리오 연동: 계정·인증키 없음 — 아래 '뿌리오 계정·인증키'에 입력하십시오. (없으면 문자가 실패해 카톡방으로 대신 갑니다)";
  }

  async function loadSms() {
    fillSms(await api("/api/admin/sms"));
  }

  $("sms-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      enabled: $("sms-enabled").checked,
      sender: $("sms-from").value,
      test_mode: $("sms-test").checked,
      test_number: $("sms-test-number").value,
    };
    if ($("sms-account").value.trim()) body.account = $("sms-account").value;
    if ($("sms-key").value.trim()) body.api_key = $("sms-key").value;
    try {
      fillSms(await send("PUT", "/api/admin/sms", body));
      $("sms-result").textContent = "저장했습니다.";
    } catch (err) {
      $("sms-result").textContent = err.message;
    }
  });
  $("sms-check").addEventListener("click", async () => {
    $("sms-result").textContent = "확인 중…";
    try {
      const r = await send("POST", "/api/admin/sms/check");
      $("sms-result").textContent = `${r.ok ? "✅" : "⚠️"} ${r.message}${r.user_ip ? ` (뿌리오가 본 서버 IP: ${r.user_ip} — 뿌리오 연동신청관리에 이 IP 가 등록돼 있어야 합니다)` : ""}`;
    } catch (err) {
      $("sms-result").textContent = err.message;
    }
  });
  $("sms-test-send").addEventListener("click", async () => {
    const to = prompt("테스트 문자를 받을 번호 (비우면 테스트 번호)", $("sms-test-number").value);
    if (to === null) return;
    $("sms-result").textContent = "보내는 중…";
    try {
      const r = await send("POST", "/api/admin/sms/test", { to });
      $("sms-result").textContent = `${r.ok ? "✅" : "⚠️"} ${r.message}`;
      loadSms().catch(() => {});
    } catch (err) {
      $("sms-result").textContent = err.message;
    }
  });

  // ---- 연결 -------------------------------------------------------------------------------------

  const prevSettings = KC.loaders?.settings;
  KC.loaders = Object.assign(KC.loaders || {}, {
    dispatch: loadDispatch,
    settings: () => Promise.all([prevSettings ? prevSettings() : null, loadSms()]),
  });
  const prevLive = KC.onLive;
  KC.onLive = (topics) => {
    if (KC.currentTab() === "dispatch" && topics.some((t) => t === "vendor" || t === "complaint")) return loadDispatch();
    return prevLive ? prevLive(topics) : null;
  };
})();
