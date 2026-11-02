"use strict";

// 통계 탭: 요약 지표 · 분류 도넛 · 일별(분류 쌓은 막대) · 요일×시간 히트맵 · 방별(분류 쌓은 막대) · 제기자 · 처리 현황.
// 색 규칙: 분류 색은 '민원 카테고리' 등록 순서로 고정 배정(기간이 바뀌어도 같은 분류 = 같은 색), 9번째부터·'기타'는 회색.
// 서버 데이터는 텍스트 노드로만 넣는다(innerHTML 금지).
(() => {
  const $ = (id) => document.getElementById(id);
  const KC = window.KC;
  const { api, el, reportError } = KC;
  const SVG = "http://www.w3.org/2000/svg";
  const WEEK = ["월", "화", "수", "목", "금", "토", "일"];
  const PERIOD_LABEL = { today: "오늘", week: "최근 7일", month: "최근 30일", all: "전체" };
  const PREV_LABEL = { today: "어제", week: "직전 7일", month: "직전 30일" };
  let period = "week";

  // ---- 공통 ---------------------------------------------------------------------------------

  const fmt = (n) => Number(n || 0).toLocaleString("ko-KR");
  const pct = (v, total) => (total ? Math.round((v * 100) / total) : 0);
  const svg = (tag, attrs = {}) => {
    const node = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
    return node;
  };

  /** 분류 → 색 슬롯 (1~8, 0 = 회색 '기타·그 외') */
  function colorMap(order) {
    const map = new Map();
    order.filter((c) => c !== "기타").forEach((c, i) => map.set(c, i < 8 ? i + 1 : 0));
    return (cat) => map.get(cat) ?? 0;
  }
  const swatch = (slot) => el("span", { class: `sw c${slot}`, "aria-hidden": "true" });

  // 마우스를 올리면 뜨는 설명 (한 개를 재사용)
  const tip = el("div", { class: "viz-tip", role: "tooltip", hidden: true });
  document.body.append(tip);
  function bindTip(node, lines) {
    node.addEventListener("mouseenter", () => {
      tip.replaceChildren(...lines().map((l, i) => el("div", { class: i ? "" : "viz-tip-title", text: l })));
      tip.hidden = false;
    });
    node.addEventListener("mousemove", (e) => {
      const pad = 14;
      const x = Math.min(e.clientX + pad, window.innerWidth - tip.offsetWidth - 8);
      const y = e.clientY + pad + tip.offsetHeight > window.innerHeight ? e.clientY - tip.offsetHeight - pad : e.clientY + pad;
      tip.style.left = `${x}px`;
      tip.style.top = `${y}px`;
    });
    node.addEventListener("mouseleave", () => (tip.hidden = true));
    node.addEventListener("focus", () => node.dispatchEvent(new MouseEvent("mouseenter")));
    node.addEventListener("blur", () => (tip.hidden = true));
  }

  const card = (title, body, cls = "", sub = null) =>
    el("section", { class: `sv-card ${cls}` }, el("header", { class: "sv-head" }, el("h3", { text: title }), sub ? el("span", { class: "sv-sub", text: sub }) : null), body);
  const empty = () => el("p", { class: "sv-empty", text: "이 기간에는 데이터가 없습니다." });

  function legend(cats, slotOf) {
    return el("ul", { class: "sv-legend" }, ...cats.map((c) => el("li", {}, swatch(slotOf(c)), c)));
  }

  // ---- 요약 지표 --------------------------------------------------------------------------------

  function kpis(s) {
    const tile = (icon, tone, label, value, sub) =>
      el("div", { class: `kpi kpi-${tone}` }, el("span", { class: "kpi-icon", "aria-hidden": "true", text: icon }), el("span", { class: "kpi-label", text: label }), el("strong", { class: "kpi-value", text: value }), sub ? el("span", { class: "kpi-sub" }, ...sub) : null);

    let trend = null;
    if (s.prev_issues !== null && s.prev_issues !== undefined) {
      const diff = s.issues - s.prev_issues;
      const arrow = diff > 0 ? "▲" : diff < 0 ? "▼" : "–";
      trend = [el("span", { class: `delta ${diff > 0 ? "up" : diff < 0 ? "down" : ""}`, text: `${arrow} ${fmt(Math.abs(diff))}` }), ` ${PREV_LABEL[period]} ${fmt(s.prev_issues)}건 대비`];
    }
    const replyTotal = s.reply_sent + s.reply_failed;
    const vendorTotal = s.vendor_sent + s.vendor_failed;
    return el(
      "div",
      { class: "sv-kpis" },
      tile("📌", "blue", `${PERIOD_LABEL[period]} 접수된 문제`, `${fmt(s.issues)}건`, trend),
      tile("💬", "aqua", "민원 메시지", `${fmt(s.messages)}건`, [`문제당 평균 ${s.issues ? (s.messages / s.issues).toFixed(1) : 0}건`]),
      tile("🔁", "orange", "반복 민원", `${fmt(s.repeated_issues)}건`, [`전체 문제의 ${pct(s.repeated_issues, s.issues)}%`]),
      tile("✉️", "violet", "카톡 접수 안내", `${fmt(s.reply_sent)}건`, replyTotal ? [`성공률 ${pct(s.reply_sent, replyTotal)}%`, s.reply_failed ? el("span", { class: "bad", text: ` · ⚠ 실패 ${fmt(s.reply_failed)}` }) : ""] : ["보낸 안내 없음"]),
      tile("🏢", "magenta", "업체 톡방 처리 요청", `${fmt(s.vendor_sent)}건`, vendorTotal || s.vendor_waiting ? [`성공률 ${pct(s.vendor_sent, vendorTotal)}%`, s.vendor_failed ? el("span", { class: "bad", text: ` · ⚠ 실패 ${fmt(s.vendor_failed)}` }) : "", s.vendor_waiting ? ` · 대기 ${fmt(s.vendor_waiting)}` : ""] : ["보낸 요청 없음"]),
    );
  }

  // ---- 분류 도넛 ----------------------------------------------------------------------------------

  function donut(rows, total, slotOf) {
    if (!total) return empty();
    const size = 180;
    const r = 70;
    const stroke = 26;
    const circ = 2 * Math.PI * r;
    const gap = rows.length > 1 ? 2 : 0; // 조각 사이 2px 간격
    const g = svg("g", { transform: `rotate(-90 ${size / 2} ${size / 2})` });
    g.append(svg("circle", { cx: size / 2, cy: size / 2, r, fill: "none", class: "donut-track", "stroke-width": stroke }));
    let offset = 0;
    for (const [name, v] of rows) {
      const len = (v / total) * circ;
      const seg = svg("circle", {
        cx: size / 2, cy: size / 2, r, fill: "none", class: `donut-seg s${slotOf(name)}`, "stroke-width": stroke,
        "stroke-dasharray": `${Math.max(len - gap, 0.5)} ${circ}`, "stroke-dashoffset": -offset, tabindex: "0",
      });
      bindTip(seg, () => [name, `${fmt(v)}건 · ${pct(v, total)}%`]);
      g.append(seg);
      offset += len;
    }
    const chart = svg("svg", { viewBox: `0 0 ${size} ${size}`, width: size, height: size, role: "img", "aria-label": `분류별 문제 ${total}건` });
    chart.append(g);
    const center = el("div", { class: "donut-center" }, el("strong", { text: fmt(total) }), el("span", { text: "건" }));
    const list = el(
      "ul",
      { class: "donut-list" },
      ...rows.map(([name, v]) =>
        el(
          "li",
          {},
          swatch(slotOf(name)),
          el("span", { class: "dl-name", text: name }),
          el("span", { class: "dl-bar" }, Object.assign(el("span", { class: `dl-fill c${slotOf(name)}` }), {})),
          el("span", { class: "dl-val", text: `${fmt(v)} · ${pct(v, total)}%` }),
        ),
      ),
    );
    list.querySelectorAll(".dl-fill").forEach((f, i) => (f.style.width = `${pct(rows[i][1], rows[0][1])}%`));
    return el("div", { class: "donut-wrap" }, el("div", { class: "donut" }, chart, center), list);
  }

  // ---- 일별: 분류 쌓은 세로 막대 -------------------------------------------------------------------------

  function niceMax(v) {
    if (v <= 4) return Math.max(v, 1);
    const p = 10 ** Math.floor(Math.log10(v));
    return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= v);
  }

  function stackedColumns(days, cats, slotOf) {
    const total = days.reduce((n, d) => n + d[1], 0);
    if (!total) return empty();
    const max = niceMax(Math.max(...days.map((d) => d[1])));
    const ticks = [max, max / 2, 0];
    const grid = el("div", { class: "sc-grid" }, ...ticks.map((t) => el("div", { class: "sc-line" }, el("span", { text: Number.isInteger(t) ? fmt(t) : t.toFixed(1) }))));
    const plot = el("div", { class: "sc-plot" });
    const every = Math.max(1, Math.ceil(days.length / 10));
    const axis = el("div", { class: "sc-axis" });
    days.forEach(([label, n, byCat, wd], i) => {
      const col = el("div", { class: "sc-col", tabindex: "0" });
      const stack = el("div", { class: "sc-stack" });
      stack.style.height = `${(n / max) * 100}%`;
      for (const c of cats) {
        const v = byCat[c] || 0;
        if (!v) continue;
        const seg = el("span", { class: `sc-seg c${slotOf(c)}` });
        seg.style.flexGrow = String(v);
        stack.append(seg);
      }
      col.append(stack);
      bindTip(col, () => [`${label} (${WEEK[wd]})  총 ${fmt(n)}건`, ...cats.filter((c) => byCat[c]).map((c) => `● ${c} ${fmt(byCat[c])}건`)]);
      plot.append(col);
      axis.append(el("span", { class: wd >= 5 ? "weekend" : "", text: i % every === 0 || i === days.length - 1 ? label : "" }));
    });
    return el("div", { class: "sc" }, legend(cats, slotOf), el("div", { class: "sc-body" }, grid, plot), axis);
  }

  // ---- 요일 × 시간 히트맵 ---------------------------------------------------------------------------------

  function heatmap(matrix) {
    const max = Math.max(...matrix.flat());
    if (!max) return empty();
    const level = (v) => (v ? Math.min(5, Math.ceil((v / max) * 5)) : 0);
    const grid = el("div", { class: "hm", role: "grid", "aria-label": "요일·시간대별 민원" });
    grid.append(el("span", { class: "hm-corner" }));
    for (let h = 0; h < 24; h++) grid.append(el("span", { class: "hm-hour", text: h % 3 === 0 ? `${h}` : "" }));
    let peak = [0, 0, 0];
    matrix.forEach((row, d) => {
      grid.append(el("span", { class: `hm-day${d >= 5 ? " weekend" : ""}`, text: WEEK[d] }));
      row.forEach((v, h) => {
        if (v > peak[2]) peak = [d, h, v];
        const cell = el("span", { class: `hm-cell l${level(v)}`, tabindex: v ? "0" : "-1" });
        bindTip(cell, () => [`${WEEK[d]}요일 ${h}시 ~ ${h + 1}시`, `${fmt(v)}건`]);
        grid.append(cell);
      });
    });
    const scale = el("div", { class: "hm-scale" }, el("span", { text: "적음" }), ...[1, 2, 3, 4, 5].map((l) => el("span", { class: `hm-cell l${l}` })), el("span", { text: `많음 (최대 ${fmt(max)}건)` }));
    const hourTotals = Array.from({ length: 24 }, (_, h) => matrix.reduce((n, row) => n + row[h], 0));
    const busiest = hourTotals.indexOf(Math.max(...hourTotals));
    const note = el("p", { class: "sv-note" }, "가장 많은 때: ", el("strong", { text: `${WEEK[peak[0]]}요일 ${peak[1]}시` }), ` (${fmt(peak[2])}건) · 하루 중 `, el("strong", { text: `${busiest}시대` }));
    return el("div", { class: "hm-wrap" }, grid, el("div", { class: "hm-foot" }, note, scale));
  }

  // ---- 가로 막대 (방별: 분류 쌓음 / 제기자: 단색) ------------------------------------------------------------

  function hstack(rows, cats, slotOf) {
    if (!rows.length) return empty();
    const max = rows[0][1];
    const list = el("ul", { class: "hs" });
    for (const [name, n, byCat] of rows) {
      const bar = el("span", { class: "hs-bar" });
      bar.style.width = `${(n / max) * 100}%`;
      for (const c of cats) {
        if (!byCat[c]) continue;
        const seg = el("span", { class: `hs-seg c${slotOf(c)}` });
        seg.style.flexGrow = String(byCat[c]);
        bar.append(seg);
      }
      const row = el("li", { tabindex: "0" }, el("span", { class: "hs-name", text: name, title: name }), el("span", { class: "hs-track" }, bar), el("span", { class: "hs-val", text: `${fmt(n)}건` }));
      bindTip(row, () => [name, ...cats.filter((c) => byCat[c]).map((c) => `● ${c} ${fmt(byCat[c])}건`)]);
      list.append(row);
    }
    return list;
  }

  function hbars(rows, total) {
    if (!rows.length) return empty();
    const max = rows[0][1];
    return el(
      "ul",
      { class: "hs" },
      ...rows.map(([name, n], i) => {
        const bar = el("span", { class: "hs-bar hs-single" });
        bar.style.width = `${(n / max) * 100}%`;
        const [who, room] = name.split(" · ");
        return el(
          "li",
          {},
          el("span", { class: "hs-rank", text: String(i + 1) }),
          el("span", { class: "hs-name", title: name }, who, room ? el("small", { text: ` ${room}` }) : null),
          el("span", { class: "hs-track" }, bar),
          el("span", { class: "hs-val", text: `${fmt(n)}건 · ${pct(n, total)}%` }),
        );
      }),
    );
  }

  // ---- 처리 현황 (상태 색 + 아이콘·글자) ----------------------------------------------------------------

  function outcome(s) {
    const row = (title, parts) => {
      const total = parts.reduce((n, p) => n + p[2], 0);
      const bar = el("div", { class: "oc-bar" });
      for (const [label, tone, v] of parts) {
        if (!v) continue;
        const seg = el("span", { class: `oc-seg ${tone}`, tabindex: "0" });
        seg.style.flexGrow = String(v);
        bindTip(seg, () => [`${title} · ${label}`, `${fmt(v)}건 · ${pct(v, total)}%`]);
        bar.append(seg);
      }
      if (!total) bar.append(el("span", { class: "oc-seg none", text: "기록 없음" }));
      return el(
        "div",
        { class: "oc-row" },
        el("div", { class: "oc-title" }, el("strong", { text: title }), el("span", { class: "sv-sub", text: `${fmt(total)}건` })),
        bar,
        el("ul", { class: "sv-legend" }, ...parts.map(([label, tone, v]) => el("li", {}, el("span", { class: `sw ${tone}`, "aria-hidden": "true" }), `${label} ${fmt(v)}`))),
      );
    };
    const noReply = Math.max(0, s.issues - s.reply_sent - s.reply_failed);
    return el(
      "div",
      { class: "oc" },
      row("카톡 접수 안내", [["✓ 보냄", "good", s.reply_sent], ["⚠ 실패", "bad", s.reply_failed], ["– 안내 없음(반복·지난 메시지·꺼짐)", "muted", noReply]]),
      row("업체 톡방 처리 요청", [["✓ 보냄", "good", s.vendor_sent], ["⏳ 대기", "warn", s.vendor_waiting], ["⚠ 실패·만료", "bad", s.vendor_failed]]),
    );
  }

  // ---- 화면 -------------------------------------------------------------------------------------

  async function loadStats() {
    for (const b of $("st-periods").children) b.setAttribute("aria-pressed", String(b.dataset.period === period));
    const s = await api("/api/admin/stats", { period });
    const slotOf = colorMap(s.category_order || []);
    // 범례·쌓는 순서도 등록 순서 (데이터에 있는 분류만)
    const present = new Set(
      s.daily_detail.flatMap((d) => Object.keys(d[2]).filter((c) => d[2][c] > 0)).concat(s.by_category.map((c) => c[0]), s.room_detail.flatMap((r) => Object.keys(r[2]).filter((c) => r[2][c] > 0))),
    );
    const cats = [...(s.category_order || []).filter((c) => present.has(c)), ...[...present].filter((c) => !(s.category_order || []).includes(c))];
    const byCatOrdered = [...s.by_category].sort((a, b) => b[1] - a[1]);

    $("st-body").replaceChildren(
      kpis(s),
      el(
        "div",
        { class: "sv-grid" },
        card("분류별 문제", donut(byCatOrdered, s.issues, slotOf), "span-5", `${byCatOrdered.length}개 분류`),
        card(period === "all" ? "일별 추이 (최근 30일)" : "일별 추이", stackedColumns(s.daily_detail, cats, slotOf), "span-7", "막대에 마우스를 올리면 분류별 건수"),
        card("요일 · 시간대", heatmap(s.weekday_hour), "span-7", "문제가 시작된 시각 기준"),
        card("처리 현황", outcome(s), "span-5"),
        card("방별 문제 TOP 10", hstack(s.room_detail, cats, slotOf), "span-6"),
        card("민원 제기자 TOP 10", hbars(s.by_sender, s.issues), "span-6"),
      ),
    );
  }

  KC.loaders = Object.assign(KC.loaders || {}, { stats: loadStats });
  for (const b of $("st-periods").children) {
    b.addEventListener("click", () => {
      period = b.dataset.period;
      loadStats().catch(reportError);
    });
  }
})();
