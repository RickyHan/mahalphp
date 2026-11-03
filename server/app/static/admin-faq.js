"use strict";

// 'FAQ 자동 답변' 탭: 기능 켜기·끄기, 문항 보기·추가·수정·삭제, 최근 자동 답변 이력.
// 서버 데이터는 텍스트 노드로만 넣는다(innerHTML 금지).
(() => {
  const $ = (id) => document.getElementById(id);
  const KC = window.KC;
  const { api, el, reportError, fmtDateTime } = KC;
  let data = { items: [], enabled: false };
  let editing = null; // 수정 중인 문항 id, 새 문항은 "new"

  async function send(method, path, body) {
    const res = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) location.href = "/admin/login";
    const out = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = Array.isArray(out.detail) ? out.detail.map((d) => d.msg).join(", ") : out.detail;
      throw new Error(detail || `요청 실패 (HTTP ${res.status})`);
    }
    return out;
  }

  function kpi(icon, tone, label, value, unit) {
    return el(
      "div",
      { class: `kpi-card tone-${tone}` },
      el("div", { class: "kpi-text" }, el("span", { class: "kpi-label", text: label }), el("div", { class: "kpi-num" }, el("strong", { text: String(value) }), el("span", { text: unit }))),
      el("span", { class: "kpi-ico ms", "aria-hidden": "true", text: icon }),
    );
  }

  async function loadFaq() {
    const [d, h] = await Promise.all([api("/api/admin/faq"), api("/api/admin/faq/history")]);
    data = d;
    $("fq-enabled").checked = d.enabled;
    $("fq-state").textContent = d.enabled ? "사용 중" : "꺼짐";
    $("fq-state").classList.toggle("on", d.enabled);
    const active = d.items.filter((i) => i.enabled).length;
    const sentToday = h.items.filter((x) => x.status === "sent" && (x.created_at || "").slice(0, 10) === new Date().toLocaleDateString("sv-SE")).length;
    $("fq-kpis").replaceChildren(
      kpi("textsms", "blue", "사용 중인 문항", active, `/ ${d.items.length}`),
      kpi("apartment", "green", "확인하는 건물 채팅방", d.building_rooms, "개"),
      kpi("check_circle", "indigo", "오늘 보낸 답변", sentToday, "건"),
      kpi("history", "slate", "최근 답변", h.items.length ? fmtDateTime(h.items[0].created_at).slice(5, 16) : "–", ""),
    );
    const warn = [];
    if (!d.ai_ready) warn.push("AI 민원 분류가 꺼져 있거나 Gemini 키가 없어 판단하지 않습니다. 시스템 설정에서 '민원 자동 분류 사용'을 켜십시오.");
    if (!d.building_rooms) warn.push("채팅방이 연결된 건물이 없습니다. 건물 관리에서 채팅방을 연결하십시오.");
    $("fq-warn").hidden = !(d.enabled && warn.length);
    $("fq-warn-text").textContent = warn.join(" ");
    fillCategories();
    render();
    renderHistory(h.items);
  }

  function fillCategories() {
    const sel = $("fq-cat");
    const cur = sel.value;
    const cats = [...new Set(data.items.map((i) => i.category).filter(Boolean))];
    sel.replaceChildren(el("option", { value: "", text: "전체 분야" }), ...cats.map((c) => el("option", { value: c, text: c })));
    sel.value = cats.includes(cur) ? cur : "";
  }

  function render() {
    const q = $("fq-search").value.trim().toLowerCase();
    const cat = $("fq-cat").value;
    const shown = data.items.filter((i) => (!cat || i.category === cat) && (!q || `${i.category} ${i.question} ${i.answer}`.toLowerCase().includes(q)));
    $("fq-count").textContent = shown.length === data.items.length ? `${data.items.length}개` : `${shown.length} / ${data.items.length}개`;
    const list = $("fq-list");
    list.replaceChildren();
    if (editing === "new") list.append(editRow(null));
    if (!shown.length && editing !== "new") {
      list.append(el("tr", {}, el("td", { colspan: "6", class: "empty-cell", text: data.items.length ? "조건에 맞는 문항이 없습니다." : "등록된 문항이 없습니다. '문항 추가'를 누르십시오." })));
      return;
    }
    for (const i of shown) list.append(editing === i.id ? editRow(i) : row(i));
  }

  function row(i) {
    const toggle = el("input", { type: "checkbox", class: "switch-toggle", "aria-label": `${i.question} 사용` });
    toggle.checked = i.enabled;
    toggle.addEventListener("change", async () => {
      try {
        await send("PUT", `/api/admin/faq/${i.id}`, { category: i.category, question: i.question, answer: i.answer, enabled: toggle.checked });
        i.enabled = toggle.checked;
        tr.classList.toggle("off", !i.enabled);
      } catch (err) {
        toggle.checked = i.enabled;
        reportError(err);
      }
    });
    const tr = el(
      "tr",
      { class: i.enabled ? "" : "off" },
      el("td", {}, i.category ? el("span", { class: "fq-cat", text: i.category }) : el("span", { class: "muted", text: "–" })),
      el("td", { class: "fq-q", text: i.question }),
      el("td", {}, el("div", { class: "fq-a", title: i.answer, text: i.answer })),
      el("td", { class: "num" }, el("span", { class: `count-pill${i.sent ? " has" : ""}`, text: `${i.sent}건` })),
      el("td", {}, toggle),
      el(
        "td",
        { class: "right-col" },
        el(
          "div",
          { class: "row-actions" },
          el("button", { class: "btn btn-small", type: "button", text: "수정", onclick: () => startEdit(i.id) }),
          el("button", {
            class: "btn btn-small btn-danger",
            type: "button",
            text: "삭제",
            onclick: async () => {
              if (!confirm(`이 문항을 삭제하시겠습니까?\n\n${i.question}`)) return;
              await send("DELETE", `/api/admin/faq/${i.id}`).catch(reportError);
              await loadFaq();
            },
          }),
        ),
      ),
    );
    return tr;
  }

  function startEdit(id) {
    if (editing !== null && editing !== id && !confirm("수정 중인 문항이 있습니다. 저장하지 않고 다른 문항을 여시겠습니까?")) return;
    editing = id;
    render();
    document.querySelector("#fq-list .fq-edit textarea, #fq-list .fq-edit input")?.focus();
  }

  function editRow(i) {
    const cats = [...new Set(data.items.map((x) => x.category).filter(Boolean))];
    const listId = "fq-cat-list";
    const category = el("input", { maxlength: "50", value: i?.category || $("fq-cat").value || "", list: listId, placeholder: "예: 승강기", "aria-label": "분야" });
    const question = el("textarea", { rows: "3", maxlength: "500", placeholder: "예: 엘리베이터가 멈췄어요 / 승강기 고장", "aria-label": "질문" });
    question.value = i?.question || "";
    const answer = el("textarea", { rows: "6", maxlength: "5000", placeholder: "이 질문에 보낼 답변", "aria-label": "답변" });
    answer.value = i?.answer || "";
    const enabled = el("input", { type: "checkbox", class: "switch-toggle", "aria-label": "사용" });
    enabled.checked = i ? i.enabled : true;
    const save = el("button", { class: "btn btn-primary btn-small", type: "button", text: i ? "저장" : "추가" });
    const cancel = el("button", { class: "btn btn-small", type: "button", text: "취소", onclick: () => ((editing = null), render()) });
    save.addEventListener("click", async () => {
      const body = { category: category.value, question: question.value, answer: answer.value, enabled: enabled.checked };
      if (!body.question.trim() || !body.answer.trim()) return reportError(new Error("질문과 답변을 모두 입력하십시오."));
      save.disabled = true;
      try {
        if (i) await send("PUT", `/api/admin/faq/${i.id}`, body);
        else await send("POST", "/api/admin/faq", body);
        editing = null;
        await loadFaq();
      } catch (err) {
        reportError(err);
        save.disabled = false;
      }
    });
    const form = el(
      "div",
      { class: "fq-edit" },
      el("datalist", { id: listId }, ...cats.map((c) => el("option", { value: c }))),
      el("div", { class: "fq-edit-grid" },
        el("label", { class: "vd-field" }, el("span", { class: "vd-cap", text: "분야" }), category),
        el("label", { class: "vd-field" }, el("span", { class: "vd-cap", text: "질문 (이런 의도의 메시지에 답합니다. '/'로 여러 표현을 적을 수 있습니다)" }), question),
        el("label", { class: "vd-field fq-edit-answer" }, el("span", { class: "vd-cap", text: "보낼 답변" }), answer),
      ),
      el("div", { class: "fq-edit-foot" }, el("label", { class: "check" }, enabled, " 사용"), cancel, save),
    );
    form.addEventListener("keydown", (e) => e.key === "Escape" && cancel.click());
    return el("tr", { class: "fq-edit-row" }, el("td", { colspan: "6" }, form));
  }

  const STATUS = { sent: ["보냄", "ok"], pending: ["전송 대기", "wait"], failed: ["실패", "bad"], expired: ["만료", "bad"] };

  function renderHistory(items) {
    const body = $("fq-history");
    body.replaceChildren();
    if (!items.length) {
      body.append(el("tr", {}, el("td", { colspan: "5", class: "empty-cell", text: "아직 보낸 자동 답변이 없습니다." })));
      return;
    }
    for (const h of items) {
      const [label, tone] = h.status ? STATUS[h.status] || [h.status, ""] : ["생략", "skip"];
      body.append(
        el(
          "tr",
          {},
          el("td", { class: "muted small", text: fmtDateTime(h.created_at).slice(5, 16) }),
          el("td", {}, KC.roomChip(h.room_name, true)),
          el("td", {}, el("div", { class: "fq-msg" }, h.sender_name ? el("strong", { text: `${h.sender_name} ` }) : null, h.message_text || "")),
          el("td", { class: "fq-q", text: h.question }),
          el("td", {}, el("span", { class: `fq-status ${tone}`, title: h.send_note || h.note || "", text: label }), h.note ? el("div", { class: "muted small", text: h.note }) : null),
        ),
      );
    }
  }

  $("fq-enabled").addEventListener("change", async (e) => {
    const on = e.target.checked;
    if (!on && !confirm("FAQ 자동 답변을 끄시겠습니까? 건물 채팅방에 자동 답변이 나가지 않습니다.")) {
      e.target.checked = true;
      return;
    }
    try {
      await send("PUT", "/api/admin/faq/settings", { enabled: on });
      await loadFaq();
    } catch (err) {
      e.target.checked = !on;
      reportError(err);
    }
  });
  $("fq-new").addEventListener("click", () => startEdit("new"));
  $("fq-search").addEventListener("input", render);
  $("fq-cat").addEventListener("change", render);

  KC.loaders = Object.assign(KC.loaders || {}, { faq: () => (editing === null ? loadFaq() : Promise.resolve()) }); // 수정 중에는 새로 읽지 않음
})();
