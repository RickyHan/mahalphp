"use strict";

// 민원 자동처리 시스템 관리자 화면. 서버 데이터는 모두 textContent/텍스트 노드로만 넣는다(innerHTML 사용 금지).
(() => {
  const $ = (id) => document.getElementById(id);
  const PAGE = 100;
  const POLL_MS = 10000; // 실시간 연결이 끊겼을 때의 대체 새로고침 주기
  const SAFETY_MS = 60000; // 실시간 연결 중에도 혹시 놓친 변경을 잡는 주기
  const SELF = "나";
  const TYPE_LABEL = { photo: "📷 사진", video: "🎬 동영상", emoticon: "😊 이모티콘", file: "📎 파일", other: "기타" };
  const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

  const state = {
    room: null,
    messages: [],
    hasOlder: false,
    focusId: null,
    roomQuery: "",
    tab: "chat",
    photos: [],
    photosKey: null,
    photosHasMore: false,
    search: null,
    searchItems: [],
    lightbox: { items: [], index: 0 },
    busy: false,
    selecting: false,
    selected: new Set(),
  };

  // ---- 공통 ----------------------------------------------------------------------

  async function api(path, params = {}) {
    const url = new URL(path, location.origin);
    for (const [k, v] of Object.entries(params)) {
      if (v !== null && v !== undefined && v !== "") url.searchParams.set(k, v);
    }
    const res = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (res.status === 401) {
      location.href = "/admin/login";
      throw new Error("로그인이 필요합니다.");
    }
    if (!res.ok) throw new Error(`요청 실패 (HTTP ${res.status})`);
    return res.json();
  }

  async function postJson(path, body) {
    const res = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 401) {
      location.href = "/admin/login";
      throw new Error("로그인이 필요합니다.");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(", ") : data.detail;
      throw new Error(detail || `요청 실패 (HTTP ${res.status})`);
    }
    return data;
  }

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of children) if (c !== null && c !== undefined && c !== false) node.append(c);
    return node;
  }

  function reportError(err) {
    console.error(err);
    const title = $("panel-title");
    title.textContent = `⚠ ${err.message}`;
    setTimeout(() => updateTitle(), 4000);
  }

  // 서버 시각은 KST 문자열(YYYY-MM-DDTHH:MM:SS[.ffffff])이므로 브라우저 시간대와 무관하게 잘라서 표시한다
  const hhmm = (s) => (s ? s.slice(11, 16) : "");
  function fmtDay(day) {
    const [y, m, d] = day.split("-").map(Number);
    return `${y}년 ${m}월 ${d}일 (${WEEKDAYS[new Date(y, m - 1, d).getDay()]})`;
  }
  function fmtDateTime(s) {
    return s ? `${s.slice(0, 10)} ${s.slice(11, 19)}` : "–";
  }
  function shortTime(s) {
    if (!s) return "";
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    return s.slice(0, 10) === today ? hhmm(s) : s.slice(5, 10).replace("-", "/");
  }

  function avatar(name, isGroup) {
    let hash = 0;
    for (const ch of name) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
    const node = el("span", { class: "avatar", "aria-hidden": "true", text: isGroup ? "👥" : Array.from(name)[0] || "?" });
    node.style.background = `hsl(${hash % 360} 45% 52%)`;
    return node;
  }

  function linkify(text) {
    const frag = document.createDocumentFragment();
    for (const part of text.split(/(https?:\/\/[^\s<>"']+)/g)) {
      if (/^https?:\/\//.test(part)) {
        frag.append(el("a", { href: part, target: "_blank", rel: "noopener noreferrer nofollow", text: part }));
      } else if (part) {
        frag.append(part);
      }
    }
    return frag;
  }

  function highlight(text, query) {
    const frag = document.createDocumentFragment();
    if (!query) {
      frag.append(text);
      return frag;
    }
    const lower = text.toLowerCase();
    const q = query.toLowerCase();
    let pos = 0;
    for (let i = lower.indexOf(q); i !== -1; i = lower.indexOf(q, pos)) {
      if (i > pos) frag.append(text.slice(pos, i));
      frag.append(el("mark", { text: text.slice(i, i + q.length) }));
      pos = i + q.length;
    }
    if (pos < text.length) frag.append(text.slice(pos));
    return frag;
  }

  const mediaUrl = (id) => `/api/media/${encodeURIComponent(id)}`;
  // 카카오톡이 내용 대신 넣는 안내 문구("사진을 보냈습니다." 등)
  const PLACEHOLDER = /(보냈습니다\.?|^(사진|동영상|이모티콘|photos?|video|emoticon))$/i;
  const isPlaceholder = (type, text) => type !== "text" && type !== "file" && (!text || PLACEHOLDER.test(text.trim()));
  function preview(type, text) {
    if (type === "text" || !TYPE_LABEL[type]) return text || "";
    return isPlaceholder(type, text) ? TYPE_LABEL[type] : `${TYPE_LABEL[type]} ${text}`;
  }
  const dims = (w, h) => (w && h ? ` · ${w}×${h}px` : "");

  const TAB_TITLES = { photos: "사진 모아보기", search: "메시지 검색", complaints: "민원", stats: "민원 통계", members: "참여자 이력", vendors: "민원처리업체", categories: "민원 카테고리", sms: "문자 발송", data: "데이터 관리", settings: "설정" };
  function updateTitle() {
    $("panel-title").textContent = state.tab === "chat" ? state.room || "" : TAB_TITLES[state.tab] || "";
  }

  // ---- 요약 · 대화방 목록 ------------------------------------------------------------------

  async function loadSummary() {
    const s = await api("/api/admin/summary");
    const n = (v) => Number(v || 0).toLocaleString("ko-KR");
    $("stat-messages").textContent = n(s.messages);
    $("stat-today").textContent = n(s.today);
    $("stat-rooms").textContent = n(s.rooms);
    $("stat-photos").textContent = n(s.photos);
    $("stat-last").textContent = fmtDateTime(s.last_received_at);
  }

  async function loadRooms() {
    const data = await api("/api/admin/rooms", { q: state.roomQuery });
    const list = $("room-list");
    const scroll = list.scrollTop;
    list.replaceChildren();
    if (!data.items.length) {
      list.append(el("li", { class: "center muted", text: state.roomQuery ? "검색 결과가 없습니다." : "아직 수집된 대화가 없습니다." }));
    }
    for (const r of data.items) {
      const last = `${r.is_group_chat && r.last_sender ? `${r.last_sender}: ` : ""}${preview(r.last_type, r.last_message)}`;
      const button = el(
        "button",
        {
          class: "room",
          type: "button",
          "data-room": r.room_name,
          "aria-current": r.room_name === state.room ? "true" : "false",
          onclick: () => openRoom(r.room_name).catch(reportError),
        },
        avatar(r.room_name, r.is_group_chat),
        el("span", { class: "room-name" }, r.room_name, el("span", { class: "room-count", text: r.message_count.toLocaleString("ko-KR") })),
        el("span", { class: "room-time", text: shortTime(r.last_posted_at) }),
        el("span", { class: "room-last", text: last }),
      );
      list.append(el("li", {}, button));
    }
    list.scrollTop = scroll;
  }

  function markCurrentRoom() {
    for (const b of document.querySelectorAll(".room")) b.setAttribute("aria-current", String(b.dataset.room === state.room));
  }

  // ---- 대화 보기 -----------------------------------------------------------------------

  async function openRoom(room, focusId = null) {
    if (state.room !== room) endSelect(false);
    state.room = room;
    state.focusId = focusId;
    state.messages = [];
    state.hasOlder = false;
    try {
      localStorage.setItem("kc-room", room);
    } catch {
      /* 저장소 사용 불가 시 무시 */
    }
    markCurrentRoom();
    switchTab("chat");
    $("chat").replaceChildren(el("div", { class: "empty", text: "불러오는 중…" }));

    const data = await api("/api/admin/messages", { room, before_id: focusId ? focusId + 1 : null, limit: PAGE });
    if (state.room !== room) return; // 그사이 다른 방을 열었음
    state.messages = data.items;
    state.hasOlder = data.has_more;
    renderChat();

    const view = $("view-chat");
    if (focusId) {
      const target = document.querySelector(`.msg[data-id="${focusId}"]`);
      if (target) target.scrollIntoView({ block: "center" });
      await pollNewer(false); // 검색 결과 이후의 대화도 이어서 보여 준다
    } else {
      view.scrollTop = view.scrollHeight;
    }
  }

  function renderChat() {
    const chat = $("chat");
    chat.replaceChildren();
    renderChatTools();
    chat.classList.toggle("selecting", state.selecting);
    if (!state.room) {
      chat.append(el("div", { class: "empty", text: "왼쪽에서 대화방을 선택하십시오." }));
      return;
    }
    if (state.hasOlder) {
      chat.append(el("button", { class: "btn load-older", type: "button", text: "이전 메시지 더 보기", onclick: () => loadOlder().catch(reportError) }));
    }
    if (!state.messages.length) {
      chat.append(el("div", { class: "empty", text: "메시지가 없습니다." }));
      return;
    }
    let prevDay = null;
    state.messages.forEach((m, i) => {
      const day = m.posted_at.slice(0, 10);
      if (day !== prevDay) {
        chat.append(el("div", { class: "day-sep", text: fmtDay(day) }));
        prevDay = day;
      }
      const prev = state.messages[i - 1];
      const next = state.messages[i + 1];
      const sameGroup = (a, b) => a && b && a.sender_name === b.sender_name && a.posted_at.slice(0, 16) === b.posted_at.slice(0, 16);
      chat.append(renderMessage(m, sameGroup(prev, m), !sameGroup(m, next)));
    });
  }

  function renderMessage(m, continued, showTime) {
    const self = m.sender_name === SELF;
    const classes = ["msg", self && "self", continued && "cont", m.id === state.focusId && "highlight"].filter(Boolean).join(" ");
    const node = el("div", { class: classes + (state.selected.has(m.id) ? " selected" : ""), "data-id": m.id });
    if (state.selecting) {
      node.setAttribute("role", "checkbox");
      node.setAttribute("aria-checked", String(state.selected.has(m.id)));
      node.tabIndex = 0;
      const toggle = (e) => {
        e.preventDefault();
        e.stopPropagation(); // 선택 중에는 사진 크게 보기·링크 이동 대신 선택
        toggleSelect(m.id, node);
      };
      node.addEventListener("click", toggle, true);
      node.addEventListener("keydown", (e) => (e.key === " " || e.key === "Enter") && toggle(e));
    }
    if (!self && !continued && m.is_group_chat) node.append(el("div", { class: "msg-sender", text: m.sender_name }));

    const content = el("div", {});
    const text = m.message_text || "";
    if (m.media.length) {
      if (text && !isPlaceholder(m.message_type, text)) content.append(el("div", { class: "bubble" }, linkify(text)));
      const thumbs = el("div", { class: "thumbs" });
      for (const media of m.media) {
        const ratio = media.width && media.height ? Math.min(1, 240 / Math.max(media.width, media.height)) : 1;
        thumbs.append(
          el(
            "button",
            { class: "thumb", type: "button", "aria-label": "사진 크게 보기", onclick: () => openChatLightbox(media.id) },
            el("img", {
              src: mediaUrl(media.id),
              alt: `${m.sender_name}이(가) 보낸 사진`,
              loading: "lazy",
              width: media.width ? Math.round(media.width * ratio) : null,
              height: media.height ? Math.round(media.height * ratio) : null,
            }),
          ),
        );
      }
      content.append(thumbs);
    } else if (m.message_type !== "text") {
      const placeholder = isPlaceholder(m.message_type, text);
      const body = placeholder ? (m.message_type === "photo" ? "미리보기 없음" : "") : text;
      content.append(el("div", { class: "bubble placeholder" }, el("span", { class: "badge", text: TYPE_LABEL[m.message_type] || m.message_type }), body));
    } else {
      content.append(el("div", { class: "bubble" }, linkify(text)));
    }

    node.append(el("div", { class: "msg-row" }, content, showTime ? el("time", { class: "msg-time", datetime: m.posted_at, text: hhmm(m.posted_at) }) : null));
    return node;
  }

  // ---- 대화 선택 삭제 · 방 삭제 --------------------------------------------------------------

  function renderChatTools() {
    const open = Boolean(state.room);
    $("chat-tools").hidden = !open;
    if (!open) return;
    const n = state.selected.size;
    $("chat-tools-info").textContent = state.selecting
      ? n ? `${n}개 선택됨` : "지울 메시지를 누르십시오"
      : `${state.room}`;
    for (const id of ["sel-start", "room-delete"]) $(id).hidden = state.selecting;
    for (const id of ["sel-all", "sel-delete", "sel-cancel"]) $(id).hidden = !state.selecting;
    $("sel-delete").disabled = n === 0;
    $("sel-delete").textContent = n ? `${n}개 삭제` : "삭제";
  }

  function startSelect() {
    state.selecting = true;
    state.selected.clear();
    renderChat();
  }

  function endSelect(render = true) {
    state.selecting = false;
    state.selected.clear();
    if (render) renderChat();
  }

  function toggleSelect(id, node) {
    if (state.selected.has(id)) state.selected.delete(id);
    else state.selected.add(id);
    node.classList.toggle("selected", state.selected.has(id));
    node.setAttribute("aria-checked", String(state.selected.has(id)));
    renderChatTools();
  }

  async function deleteSelected() {
    const ids = [...state.selected];
    if (!ids.length || !confirm(`선택한 메시지 ${ids.length}개를 삭제하시겠습니까?\n사진·민원 분류도 함께 지워지며 되돌릴 수 없습니다.`)) return;
    const r = await postJson("/api/admin/data/delete", { scope: "messages", ids });
    endSelect(false);
    alert(`메시지 ${r.messages}개를 삭제했습니다.`);
    await refreshAfterDelete();
  }

  async function deleteRoom(room) {
    if (!confirm(`'${room}' 방의 수집 내용을 모두 삭제하시겠습니까?\n대화·사진·민원 분류·참여자 이력이 지워지며 되돌릴 수 없습니다.`)) return;
    const r = await postJson("/api/admin/data/delete", { scope: "rooms", rooms: [room] });
    alert(`'${room}' 방에서 메시지 ${r.messages}개를 삭제했습니다.`);
    await refreshAfterDelete();
  }

  /** 삭제 뒤 화면 정리: 열린 방이 사라졌으면 닫고, 남아 있으면 다시 읽는다. */
  async function refreshAfterDelete() {
    state.photosKey = null; // 사진 탭은 다음에 열 때 새로 읽음
    await Promise.all([loadSummary(), loadRooms()]);
    if (state.room) {
      const exists = document.querySelector(`.room[data-room="${CSS.escape(state.room)}"]`);
      if (exists) {
        const view = $("view-chat");
        const top = view.scrollTop;
        const data = await api("/api/admin/messages", { room: state.room, limit: Math.min(300, Math.max(PAGE, state.messages.length)) });
        state.messages = data.items;
        state.hasOlder = data.has_more;
        renderChat();
        view.scrollTop = top;
      } else {
        state.room = null;
        state.messages = [];
        endSelect(false);
        renderChat();
        updateTitle();
      }
    }
  }

  async function loadOlder() {
    if (state.busy || !state.messages.length) return;
    state.busy = true;
    try {
      const room = state.room;
      const data = await api("/api/admin/messages", { room, before_id: state.messages[0].id, limit: PAGE });
      if (state.room !== room) return;
      const view = $("view-chat");
      const before = view.scrollHeight;
      state.messages = data.items.concat(state.messages);
      state.hasOlder = data.has_more;
      renderChat();
      view.scrollTop += view.scrollHeight - before; // 읽던 위치 유지
    } finally {
      state.busy = false;
    }
  }

  async function pollNewer(autoScroll = true) {
    if (!state.room || state.busy) return;
    const room = state.room;
    const lastId = state.messages.length ? state.messages[state.messages.length - 1].id : 0;
    const data = await api("/api/admin/messages", { room, after_id: lastId, limit: PAGE });
    if (state.room !== room || !data.items.length) return;
    const view = $("view-chat");
    const nearBottom = view.scrollHeight - view.scrollTop - view.clientHeight < 120;
    const top = view.scrollTop;
    state.messages = state.messages.concat(data.items);
    renderChat();
    if (autoScroll && nearBottom && !state.focusId) {
      view.scrollTop = view.scrollHeight;
    } else {
      view.scrollTop = top;
      const unseenBelow = view.scrollHeight - view.scrollTop - view.clientHeight > 120;
      if (state.tab === "chat" && unseenBelow) $("new-msg").hidden = false;
    }
  }

  // ---- 사진 ---------------------------------------------------------------------------

  async function loadPhotos(reset) {
    const roomOnly = $("photos-room-only").checked && state.room;
    const key = roomOnly ? `room:${state.room}` : "all";
    if (reset || key !== state.photosKey) {
      state.photos = [];
      state.photosKey = key;
      $("gallery").replaceChildren();
    }
    const before = state.photos.length ? state.photos[state.photos.length - 1].id : null;
    const data = await api("/api/admin/photos", { room: roomOnly ? state.room : null, before_id: before, limit: 60 });
    const start = state.photos.length;
    state.photos = state.photos.concat(data.items);
    state.photosHasMore = data.has_more;
    const gallery = $("gallery");
    data.items.forEach((p, i) => {
      gallery.append(
        el(
          "button",
          { class: "tile", type: "button", "aria-label": `${p.room_name} ${p.sender_name} 사진`, onclick: () => openLightbox(galleryItems(), start + i) },
          el("img", { src: mediaUrl(p.id), alt: "", loading: "lazy" }),
          el("span", { text: `${p.room_name} · ${p.sender_name}` }),
        ),
      );
    });
    if (!state.photos.length) gallery.append(el("p", { class: "muted", text: "저장된 사진이 없습니다." }));
    $("photos-more").hidden = !state.photosHasMore;
  }

  const galleryItems = () =>
    state.photos.map((p) => ({ id: p.id, caption: `${p.room_name} · ${p.sender_name} · ${fmtDateTime(p.posted_at)}${dims(p.width, p.height)}` }));

  // ---- 검색 ---------------------------------------------------------------------------

  async function runSearch(more = false) {
    if (!more) {
      state.search = {
        q: $("search-q").value.trim(),
        type: $("search-type").value,
        room: $("search-room-only").checked ? state.room : null,
      };
      state.searchItems = [];
      $("results").replaceChildren();
    }
    if (!state.search || !state.search.q) return;
    const oldest = state.searchItems.length ? state.searchItems[state.searchItems.length - 1].id : null;
    const data = await api("/api/admin/messages", { ...state.search, before_id: oldest, limit: 50 });
    const items = data.items.slice().reverse(); // 최신순
    state.searchItems = state.searchItems.concat(items);
    const list = $("results");
    for (const m of items) {
      list.append(
        el(
          "li",
          {},
          el(
            "button",
            { class: "result", type: "button", onclick: () => openRoom(m.room_name, m.id).catch(reportError) },
            el("div", { class: "result-meta" }, el("strong", { text: m.room_name }), el("span", { text: m.sender_name }), el("span", { text: fmtDateTime(m.posted_at) })),
            el("div", { class: "result-text" }, highlight(preview(m.message_type, m.message_text), state.search.q)),
          ),
        ),
      );
    }
    if (!state.searchItems.length) list.append(el("li", { class: "center muted", text: "검색 결과가 없습니다." }));
    $("results-more").hidden = !data.has_more;
  }

  // ---- 탭 · 라이트박스 --------------------------------------------------------------------

  function switchTab(tab) {
    state.tab = tab;
    for (const b of document.querySelectorAll(".tab")) b.setAttribute("aria-selected", String(b.dataset.tab === tab));
    for (const v of document.querySelectorAll(".view")) v.hidden = v.id !== `view-${tab}`;
    $("layout").classList.add("room-open");
    $("new-msg").hidden = true;
    updateTitle();
    if (tab === "photos") {
      const key = $("photos-room-only").checked && state.room ? `room:${state.room}` : "all";
      if (key !== state.photosKey) loadPhotos(true).catch(reportError);
    }
    if (tab === "search") $("search-q").focus();
    window.KC?.onTab?.(tab); // 민원·통계·이력·설정 탭 (admin-ops.js)
  }

  function openChatLightbox(mediaId) {
    const items = [];
    for (const m of state.messages) {
      for (const media of m.media) {
        items.push({ id: media.id, caption: `${m.sender_name} · ${fmtDateTime(m.posted_at)}${dims(media.width, media.height)}` });
      }
    }
    openLightbox(items, Math.max(0, items.findIndex((x) => x.id === mediaId)));
  }

  function openLightbox(items, index) {
    if (!items.length) return;
    state.lightbox = { items, index };
    showLightboxItem();
    $("lightbox").hidden = false;
    $("lb-close").focus();
  }

  function showLightboxItem() {
    const { items, index } = state.lightbox;
    const item = items[index];
    $("lb-img").src = mediaUrl(item.id);
    $("lb-img").alt = item.caption;
    $("lb-caption").textContent = `${item.caption}  (${index + 1}/${items.length})`;
    $("lb-prev").hidden = index === 0;
    $("lb-next").hidden = index === items.length - 1;
  }

  function stepLightbox(delta) {
    const lb = state.lightbox;
    const next = lb.index + delta;
    if (next < 0 || next >= lb.items.length) return;
    lb.index = next;
    showLightboxItem();
  }

  const closeLightbox = () => {
    $("lightbox").hidden = true;
    $("lb-img").removeAttribute("src");
  };

  // ---- 초기화 --------------------------------------------------------------------------

  function bind() {
    let debounce;
    $("room-search").addEventListener("input", (e) => {
      clearTimeout(debounce);
      debounce = setTimeout(() => {
        state.roomQuery = e.target.value.trim();
        loadRooms().catch(reportError);
      }, 250);
    });
    for (const b of document.querySelectorAll(".tab")) b.addEventListener("click", () => switchTab(b.dataset.tab));
    $("back-btn").addEventListener("click", () => $("layout").classList.remove("room-open"));
    $("new-msg").addEventListener("click", () => {
      const view = $("view-chat");
      view.scrollTop = view.scrollHeight;
      $("new-msg").hidden = true;
    });
    $("view-chat").addEventListener("scroll", (e) => {
      const v = e.target;
      if (v.scrollHeight - v.scrollTop - v.clientHeight < 40) $("new-msg").hidden = true;
    });
    $("sel-start").addEventListener("click", startSelect);
    $("sel-cancel").addEventListener("click", () => endSelect());
    $("sel-all").addEventListener("click", () => {
      for (const m of state.messages) state.selected.add(m.id);
      renderChat();
    });
    $("sel-delete").addEventListener("click", () => deleteSelected().catch(reportError));
    $("room-delete").addEventListener("click", () => state.room && deleteRoom(state.room).catch(reportError));
    $("photos-room-only").addEventListener("change", () => loadPhotos(true).catch(reportError));
    $("photos-more").addEventListener("click", () => loadPhotos(false).catch(reportError));
    $("search-form").addEventListener("submit", (e) => {
      e.preventDefault();
      runSearch(false).catch(reportError);
    });
    $("results-more").addEventListener("click", () => runSearch(true).catch(reportError));
    $("lb-close").addEventListener("click", closeLightbox);
    $("lb-prev").addEventListener("click", () => stepLightbox(-1));
    $("lb-next").addEventListener("click", () => stepLightbox(1));
    $("lightbox").addEventListener("click", (e) => {
      if (e.target.id === "lightbox") closeLightbox();
    });
    document.addEventListener("keydown", (e) => {
      if ($("lightbox").hidden) return;
      if (e.key === "Escape") closeLightbox();
      else if (e.key === "ArrowLeft") stepLightbox(-1);
      else if (e.key === "ArrowRight") stepLightbox(1);
    });
  }

  async function tick() {
    if (document.hidden) return;
    await Promise.all([loadSummary(), loadRooms(), state.tab === "chat" ? pollNewer() : null]);
  }

  // ---- 실시간 갱신 (Server-Sent Events) -------------------------------------------------------
  // 서버가 새 메시지를 저장하면 'change' 신호를 보내고, 화면은 그 즉시 필요한 부분만 다시 읽는다.

  const live = { connected: false, lastPoll: 0, pending: new Set(), timer: null, offTimer: null };

  function setLive(state, text) {
    $("live").dataset.state = state;
    $("live-text").textContent = text;
  }

  function pulseLive() {
    const node = $("live");
    node.classList.remove("pulse");
    void node.offsetWidth; // 애니메이션 다시 시작
    node.classList.add("pulse");
  }

  async function applyChanges() {
    const topics = [...live.pending];
    live.pending.clear();
    const jobs = [];
    if (topics.includes("delete")) jobs.push(refreshAfterDelete());
    else if (topics.includes("message")) jobs.push(loadSummary(), loadRooms(), state.tab === "chat" && !state.selecting ? pollNewer() : null);
    jobs.push(window.KC.onLive?.(topics)); // 민원·통계·업체 탭 (admin-ops.js, admin-vendors.js)
    await Promise.all(jobs);
  }

  function onChange(topics) {
    for (const t of topics) live.pending.add(t);
    if (topics.includes("message")) pulseLive();
    clearTimeout(live.timer);
    live.timer = setTimeout(() => applyChanges().catch(reportError), 250); // 몰려오는 신호는 한 번에 처리
  }

  function startLive() {
    if (!("EventSource" in window)) {
      setLive("off", "자동 새로고침");
      return;
    }
    const source = new EventSource("/api/admin/stream");
    source.addEventListener("hello", () => {
      clearTimeout(live.offTimer);
      const wasDown = !live.connected;
      live.connected = true;
      setLive("on", "실시간");
      if (wasDown) tick().catch(reportError); // 끊겨 있던 동안의 변경 반영
    });
    source.addEventListener("change", (e) => {
      try {
        onChange(JSON.parse(e.data).topics || []);
      } catch {
        onChange(["message"]);
      }
    });
    source.onerror = () => {
      // 서버는 1분마다 연결을 새로 맺으므로 잠깐의 끊김은 표시하지 않는다
      clearTimeout(live.offTimer);
      live.offTimer = setTimeout(() => {
        live.connected = false;
        setLive("off", "재연결 중");
      }, 4000);
      if (source.readyState === EventSource.CLOSED) {
        // 로그인 만료 등으로 영구히 닫힘 → 로그인 확인 후 다시 연결
        setTimeout(() => api("/api/admin/me").then(startLive).catch(reportError), 5000);
      }
    };
  }

  async function init() {
    bind();
    const me = await api("/api/admin/me");
    $("whoami").textContent = me.user;
    await Promise.all([loadSummary(), loadRooms()]);
    let saved = null;
    try {
      saved = localStorage.getItem("kc-room");
    } catch {
      /* 저장소 사용 불가 시 무시 */
    }
    if (saved && document.querySelector(`.room[data-room="${CSS.escape(saved)}"]`)) {
      await openRoom(saved);
    } else {
      $("layout").classList.remove("room-open");
    }
    startLive();
    setInterval(() => {
      const now = Date.now();
      if (now - live.lastPoll < (live.connected ? SAFETY_MS : POLL_MS)) return;
      live.lastPoll = now;
      tick().catch(reportError);
    }, 2000);
  }

  // admin-ops.js 에서 쓰는 공용 함수
  window.KC = Object.assign(window.KC || {}, {
    api, el, postJson, reportError, fmtDateTime, openRoom: (room, id) => openRoom(room, id).catch(reportError),
    refreshAfterDelete, deleteRoom,
    currentRoom: () => state.room, currentTab: () => state.tab,
  });

  init().catch(reportError);
})();
