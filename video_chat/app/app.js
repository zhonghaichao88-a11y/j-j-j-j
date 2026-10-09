// SeeU 视频交友 · 前端（设计预览版）
// 所有页面和按钮都可点击，数据来自 data.js 的演示数据并保存在本机浏览器里。
// 接后端时，把标注了「API」的地方换成接口请求即可。
(() => {
  const M = window.MOCK;
  const $ = (s, el = document) => el.querySelector(s);
  const view = $("#view");
  const appEl = $("#app");

  // ======================= 图标 =======================
  const P = {
    discover: '<path d="M5 13a7 7 0 1 1 14 0v5a2 2 0 0 1-2 2h-1.5l-1-2-1.5 2h-2l-1.5-2-1 2H7a2 2 0 0 1-2-2z"/><circle cx="9.5" cy="12" r=".8" fill="currentColor"/><circle cx="14.5" cy="12" r=".8" fill="currentColor"/>',
    community: '<circle cx="12" cy="12" r="6.5"/><path d="M4.5 15.5c-2 2.6-2.3 4.4-1.2 5 1.6.9 6-1.6 10-5.4s6.4-8.2 5.5-9.8c-.6-1-2.3-.8-4.8.9"/>',
    messages: '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    me: '<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5c1.8 1.6 5.2 1.6 7 0"/>',
    crown: '<path d="M3 8l4.5 4L12 4l4.5 8L21 8l-2 11H5z"/><path d="M5 19h14"/>',
    filter: '<path d="M3 5h10M3 10h7M3 15h5M3 20h5"/><path d="M13 9h8l-3 4v6l-2 1v-7z"/>',
    videoFill: '<path d="M3 7a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM17 10l4-2.5v9L17 14z"/>',
    video: '<rect x="2.5" y="6" width="13" height="12" rx="2"/><path d="M15.5 10.5l6-3.5v10l-6-3.5"/>',
    star: '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
    heart: '<path d="M12 20s-7.5-4.6-9.2-9.4C1.6 7 4 4 7 4c2 0 3.5 1.1 5 3 1.5-1.9 3-3 5-3 3 0 5.4 3 4.2 6.6C19.5 15.4 12 20 12 20z"/>',
    comment: '<path d="M21 12a8.5 8.5 0 0 1-12.6 7.4L3 21l1.6-5.2A8.5 8.5 0 1 1 21 12z"/><path d="M8.5 12h7"/>',
    more: '<circle cx="5" cy="12" r="1.3" fill="currentColor"/><circle cx="12" cy="12" r="1.3" fill="currentColor"/><circle cx="19" cy="12" r="1.3" fill="currentColor"/>',
    plane: '<path d="M21.5 2.5L10 14M21.5 2.5l-7 19-4.5-7.5L2.5 9.5z"/>',
    back: '<path d="M15 4l-8 8 8 8"/>',
    chev: '<path d="M9 5l7 7-7 7"/>',
    phone: '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
    micOff: '<path d="M15 9.5V6a3 3 0 0 0-5.8-1M9 9v2a3 3 0 0 0 4.9 2.3M18.5 11a6.5 6.5 0 0 1-1 3.4M5.5 11a6.5 6.5 0 0 0 10 5.5M12 17.5V21M3 3l18 18"/>',
    camOff: '<path d="M2.5 6v10a2 2 0 0 0 2 2h9M15.5 13V8a2 2 0 0 0-2-2H8.5M15.5 10.5l6-3.5v10M3 3l18 18"/>',
    flip: '<path d="M4 8h2.5L8 6h8l1.5 2H20a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><path d="M9 13.5a3 3 0 0 1 5.4-1.8M15 12.5a3 3 0 0 1-5.4 1.8M14.5 10v1.8h-1.8M9.5 16v-1.8h1.8"/>',
    magic: '<path d="M14 4.5l1.5 1.5M18 8l1.5 1.5M4 20l11-11-2-2L2 18zM17 3v3M20 6h-3M20 13v2M21 14h-2"/>',
    gift: '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M5 12v8h14v-8M12 8v12M12 8c-1.5-3-5-3.5-5-1.5S10 8 12 8zm0 0c1.5-3 5-3.5 5-1.5S14 8 12 8z"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.8"/><path d="M21 16l-5-5-9 9"/>',
    wallet: '<path d="M4 7h15a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z"/><path d="M4 7l11-3 1.5 3"/><circle cx="16" cy="13.5" r="1.3" class="acc"/>',
    guard: '<path d="M12 20s-7.5-4.6-9.2-9.4C1.6 7 4 4 7 4c2 0 3.5 1.1 5 3 1.5-1.9 3-3 5-3 3 0 5.4 3 4.2 6.6"/><path class="acc" d="M17 21s-3.5-2-4-4.2c-.3-1.3.7-2.3 1.9-2.3.9 0 1.6.6 2.1 1.3.5-.7 1.2-1.3 2.1-1.3 1.2 0 2.2 1 1.9 2.3-.5 2.2-4 4.2-4 4.2z"/>',
    tag: '<path d="M3 12V4h8l10 10-8 7z"/><circle cx="8" cy="8.5" r="1.8" class="acc"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2" class="acc"/>',
    game: '<path d="M7 7h10a4 4 0 0 1 4 4.5l-.6 5a2.5 2.5 0 0 1-4.3 1.3L14 15.5h-4l-2.1 2.3a2.5 2.5 0 0 1-4.3-1.3l-.6-5A4 4 0 0 1 7 7z"/><path d="M8 10v4M6 12h4" class="acc"/><circle cx="16" cy="11" r=".8" fill="currentColor"/><circle cx="17.5" cy="13.5" r=".8" fill="currentColor"/>',
    doc: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 9h6M9 13h6M9 17h3" class="acc"/>',
    lang: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M9 21l3-4M9.5 13.5L12 7l2.5 6.5M10.3 11.5h3.4" class="acc"/>',
    gear: '<circle cx="12" cy="12" r="3" class="acc"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    camera: '<path d="M4 8h3l1.5-2.5h7L17 8h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><circle cx="12" cy="13.5" r="3.5" class="acc"/>',
    lock: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/><circle cx="12" cy="15" r="1.5" class="acc"/>',
    beautyStar: '<path d="M10 3l2 5 5 .5-4 3.5 1.5 5L10 14l-4.5 3 1.5-5-4-3.5 5-.5z"/><path d="M15 15l5 5" class="acc"/>',
    coinCard: '<rect x="3" y="6" width="15" height="12" rx="2"/><circle cx="14" cy="12" r="4"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
    clear: '<rect x="4" y="3" width="14" height="18" rx="2"/><path d="M8 8h6M8 12h4M15 15l5 5M20 15l-5 5"/>',
    share: '<circle cx="18" cy="5" r="2.5"/><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="19" r="2.5"/><path d="M8.2 10.8l7.6-4.4M8.2 13.2l7.6 4.4"/>',
    location: '<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    bellFill: '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z" fill="currentColor"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    play: '<path d="M8 5v14l11-7z" fill="currentColor"/>',
    shield: '<path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
  };
  const ic = (name, cls = "i") => `<svg class="${cls}" viewBox="0 0 24 24">${P[name] || ""}</svg>`;

  // ======================= 状态（演示期存本机） =======================
  const KEY = "seeu.demo.v1";
  const fresh = () => ({
    me: { ...M.me, avatar: "" },
    follows: [1002, 1004],
    likedPosts: {},
    posts: M.posts.map((p) => ({ ...p })),
    reelLikes: {},
    convs: M.convs.map((c) => ({ ...c })),
    chats: {},
    calls: [],
    ledger: [{ t: "2026-09-28", title: "充值", amount: 180 }, { t: "2026-09-28", title: "签到奖励", amount: 1 }],
    ratings: [],
    sys: [{ t: "2026-09-28", text: "金额：18.00；充值：180；账户余额：181", unread: true }],
    service: [{ t: "2026-09-28", text: "签到成功，获赠 1 币，快去免费打给钟意的人吧！" }],
    filter: { age: "不限", price: "不限", onlineOnly: false, sameCity: false },
    ui: { chip: {} },
    settings: { dnd: false, notify: true, hideDistance: false, stealth: false, hideNearby: false, lang: "简体中文" },
    beauty: { smooth: 50, white: 40, ruddy: 30, slim: 20 },
    signed: "",
    verified: false,
    vipUntil: "2026-12-31",
  });
  let S;
  try { S = JSON.parse(localStorage.getItem(KEY)) || fresh(); } catch { S = fresh(); }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch {} };

  const U = (id) => (id === "me" || id === S.me.id ? meUser() : M.users.find((u) => u.id === +id));
  const meUser = () => ({ ...S.me, isMe: true });
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const now = () => new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  const today = () => new Date().toISOString().slice(0, 10);
  const mmss = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  const bgOf = (u, v = 0) => (u.isMe && S.me.avatar ? `url('${S.me.avatar}')` : M.art(u.seed, v));
  const av = (u, size, extra = "") =>
    `<div class="av" style="width:${size}px;height:${size}px;background-image:${bgOf(u)}">${extra}</div>`;
  const sexTag = (u) => `<span class="tag sex ${u.sex === "m" ? "m" : ""}">${u.sex === "m" ? "♂" : "♀"}${u.age}</span>`;
  const statusText = (u) => ({ online: "在线", busy: "通话中", offline: "离线" }[u.status] || "");

  // ======================= 提示 / 弹层 =======================
  let toastT;
  function toast(t) {
    const el = $("#toast");
    el.textContent = t; el.classList.remove("hidden");
    clearTimeout(toastT); toastT = setTimeout(() => el.classList.add("hidden"), 1800);
  }
  const layer = $("#layer");
  function closeLayer() { layer.innerHTML = ""; }
  function openSheet(html, { dark = false, onMount } = {}) {
    layer.innerHTML = `<div class="mask"><div class="sheet ${dark ? "dark" : ""}">${html}</div></div>`;
    const mask = layer.firstChild;
    mask.addEventListener("click", (e) => { if (e.target === mask) closeLayer(); });
    onMount?.(mask.firstChild);
    return mask.firstChild;
  }
  function dialog({ title, text, ok = "确定", cancel = "取消", onOk }) {
    layer.innerHTML = `<div class="mask center"><div class="dialog"><h3>${title}</h3><p>${text}</p>
      <div class="btns">${cancel ? `<button class="btn ghost" data-x="c">${cancel}</button>` : ""}<button class="btn primary" data-x="o">${ok}</button></div></div></div>`;
    layer.querySelector('[data-x="o"]').onclick = () => { closeLayer(); onOk?.(); };
    layer.querySelector('[data-x="c"]')?.addEventListener("click", closeLayer);
  }
  function actionSheet(items) {
    const sh = openSheet(`<div class="action-list">${items.map((it, i) =>
      `<button data-i="${i}" class="${it.danger ? "danger" : ""}">${it.text}</button>`).join("")}<button class="cancel">取消</button></div>`);
    sh.querySelectorAll("[data-i]").forEach((b) => (b.onclick = () => { closeLayer(); items[+b.dataset.i].fn?.(); }));
    sh.querySelector(".cancel").onclick = closeLayer;
  }
  const reportSheet = (u) => actionSheet([
    { text: "举报", fn: () => actionSheet(["色情低俗", "诈骗/引导转账", "广告骚扰", "其他"].map((r) => ({ text: r, fn: () => toast("举报已提交，我们会尽快处理") }))) },
    { text: "不感兴趣", fn: () => toast("将减少此类推荐") },
    { text: "拉黑", danger: true, fn: () => dialog({ title: "拉黑", text: `拉黑后将不再收到 ${esc(u.name)} 的消息和来电`, onOk: () => toast("已拉黑") }) },
  ]);

  function addLedger(title, amount) {
    S.ledger.unshift({ t: today(), title, amount });
    S.me.coins += amount;
    save();
  }
  function needCoins(n, then) {
    if (S.me.coins >= n) return then();
    dialog({ title: "金币不足", text: `需要 ${n} 金币，当前余额 ${S.me.coins}。充值后即可继续。`, ok: "去充值", onOk: () => go("/recharge") });
  }

  function giftSheet(u, { dark = true, onSent } = {}) {
    let sel = M.gifts[0];
    const sh = openSheet(`<h3>送礼物给 ${esc(u.name)}</h3>
      <div class="gifts">${M.gifts.map((g, i) => `<button data-g="${i}" class="${i === 0 ? "on" : ""}"><span class="g">${g.icon}</span><b>${g.name}</b>${g.price}金币</button>`).join("")}</div>
      <div class="gift-foot"><span class="coin">余额 <span id="gBal">${S.me.coins}</span> 金币<a id="gTop">充值 ›</a></span><button class="btn pink" id="gSend">赠送</button></div>`,
      { dark });
    sh.querySelectorAll("[data-g]").forEach((b) => (b.onclick = () => {
      sh.querySelectorAll("[data-g]").forEach((x) => x.classList.remove("on"));
      b.classList.add("on"); sel = M.gifts[+b.dataset.g];
    }));
    sh.querySelector("#gTop").onclick = () => { closeLayer(); go("/recharge"); };
    sh.querySelector("#gSend").onclick = () => needCoins(sel.price, () => {
      addLedger(`送礼物·${sel.name}`, -sel.price); // API: POST /gifts
      closeLayer(); onSent?.(sel);
    });
  }

  // ======================= 路由 =======================
  let cleanups = [];
  const onLeave = (fn) => cleanups.push(fn);
  const go = (path) => { location.hash = "#" + path; };
  const back = (fallback = "/discover") => (history.length > 1 ? history.back() : go(fallback));

  const routes = [];
  const route = (re, fn, opts = {}) => routes.push({ re, fn, ...opts });

  function render() {
    cleanups.forEach((f) => { try { f(); } catch {} });
    cleanups = [];
    closeLayer();
    const path = location.hash.slice(1) || "/discover";
    for (const r of routes) {
      const m = path.match(r.re);
      if (!m) continue;
      appEl.classList.toggle("no-tab", !r.tab);
      appEl.classList.remove("dark-tab");
      document.querySelectorAll("#tabbar a").forEach((a) => a.classList.toggle("on", a.dataset.tab === r.tab));
      view.scrollTop = 0;
      view.innerHTML = r.fn(...m.slice(1)) || "";
      r.mount?.(...m.slice(1));
      updateBadge();
      return;
    }
    go("/discover");
  }
  addEventListener("hashchange", render);

  // 全局点击：data-go 跳转，data-act 执行动作
  const acts = {};
  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-act]");
    if (a && document.body.contains(a)) { e.preventDefault(); acts[a.dataset.act]?.(a, e); return; }
    const g = e.target.closest("[data-go]");
    if (g) { e.preventDefault(); go(g.dataset.go); }
  });
  acts.back = () => back();

  // 底部导航
  const TAB_ICON = { discover: "discover", community: "community", messages: "messages", me: "me" };
  document.querySelectorAll("#tabbar a").forEach((a) => {
    a.querySelector(".ti").innerHTML = ic(TAB_ICON[a.dataset.tab]);
    a.onclick = () => go("/" + a.dataset.tab);
  });
  function updateBadge() {
    const n = S.convs.reduce((s, c) => s + c.unread, 0) + S.sys.filter((x) => x.unread).length;
    const b = $("#msgBadge");
    b.textContent = n > 99 ? "99+" : n;
    b.classList.toggle("hidden", !n);
  }

  const pageHd = (title, right = "") =>
    `<header class="page-hd"><button class="back" data-act="back">${ic("back")}</button><h2>${title}</h2><div class="right">${right}</div></header>`;
  const emptyIll = `<svg class="ill" viewBox="0 0 200 150"><ellipse cx="100" cy="138" rx="80" ry="8" fill="#f1ede8"/>
    <path d="M40 70h120v60H40z" fill="#f3dccb"/><path d="M40 70l20-18h120l-20 18z" fill="#f8e8dc"/><path d="M40 70l-14 16h120l14-16z" fill="#eacdb8"/>
    <ellipse cx="78" cy="56" rx="30" ry="34" fill="#fff" stroke="#e7e2dc" stroke-width="2"/><circle cx="68" cy="52" r="5" fill="none" stroke="#c9bfb6" stroke-width="2"/><circle cx="88" cy="52" r="5" fill="none" stroke="#c9bfb6" stroke-width="2"/>
    <path d="M74 64q4 4 8 0" stroke="#f08aa0" stroke-width="2" fill="none"/><rect x="128" y="18" width="50" height="32" rx="16" fill="#fff" stroke="#e7e2dc" stroke-width="2"/>
    <circle cx="143" cy="34" r="3" fill="#d6cec6"/><circle cx="153" cy="34" r="3" fill="#d6cec6"/><circle cx="163" cy="34" r="3" fill="#d6cec6"/></svg>`;
  const empty = (title, ...lines) => `<div class="empty">${title ? `<b>${title}</b>` : ""}${emptyIll}${lines.map((l) => `<p>${l}</p>`).join("")}</div>`;

  // ======================= 发现 =======================
  const DISC_TABS = [["recommend", "推荐"], ["square", "广场"], ["active", "活跃"], ["nearby", "附近"]];
  const CHIPS = {
    recommend: [["all", "所有", "#ff6fa3", "▦"], ["new", "新人", "#5b8cff", "新"], ["city", "同城", "#36c6f4", "城"], ["close", "亲密度", "#7a8cff", "♥"]],
    square: [["all", "所有", "#ff6fa3", "▦"], ["verified", "认证", "#36c6f4", "✓"], ["new", "新人", "#5b8cff", "新"], ["city", "同城", "#36c6f4", "城"]],
    active: [["all", "所有", "#ff6fa3", "▦"], ["online", "在线", "#2ecc71", "●"], ["new", "新人", "#5b8cff", "新"]],
    nearby: [["all", "所有", "#ff6fa3", "▦"], ["1km", "1km内", "#36c6f4", "1"], ["3km", "3km内", "#5b8cff", "3"]],
  };
  function hostList(tab, chip) {
    let list = [...M.users];
    const f = S.filter;
    if (tab === "square") list.sort((a, b) => ((a.seed * 37) % 11) - ((b.seed * 37) % 11));
    if (tab === "active") list.sort((a, b) => (a.status === "online" ? -1 : 0) - (b.status === "online" ? -1 : 0));
    if (tab === "nearby") list.sort((a, b) => a.distance - b.distance);
    if (chip === "new") list = list.filter((u) => u.isNew);
    if (chip === "city" || f.sameCity) list = list.filter((u) => u.city === S.me.city || u.seed % 3 === 0);
    if (chip === "verified") list = list.filter((u) => u.badge === "verified");
    if (chip === "online" || f.onlineOnly) list = list.filter((u) => u.status === "online");
    if (chip === "close") list = list.filter((u) => S.follows.includes(u.id) || S.convs.some((c) => c.uid === u.id));
    if (chip === "1km") list = list.filter((u) => u.distance <= 1);
    if (chip === "3km") list = list.filter((u) => u.distance <= 3);
    const ageR = { "18-22": [18, 22], "23-27": [23, 27], "28以上": [28, 99] }[f.age];
    if (ageR) list = list.filter((u) => u.age >= ageR[0] && u.age <= ageR[1]);
    const priceR = { "30以下": [0, 30], "30-60": [30, 60], "60以上": [60, 9999] }[f.price];
    if (priceR) list = list.filter((u) => u.price >= priceR[0] && u.price <= priceR[1]);
    return list;
  }
  const BADGE = { verified: `${ic("play", "i")}已认证`, goddess: "♡女神", newbie: "新人" };
  function cardHtml(u, tab) {
    const priceHtml = u.status === "busy"
      ? `<span class="price busy" data-act="call" data-uid="${u.id}">${ic("videoFill")}通话中</span>`
      : u.status === "offline"
        ? `<span class="price off" data-act="call" data-uid="${u.id}">${ic("videoFill")}离线</span>`
        : `<span class="price" data-act="call" data-uid="${u.id}">${ic("videoFill")}${u.price}金币</span>`;
    return `<a class="card" data-go="/user/${u.id}" style="background:${M.art(u.seed)} center/cover">
      ${u.badge ? `<span class="badge-tl ${u.badge}">${BADGE[u.badge].replace('class="i"', 'class="i" style="width:12px;height:12px"')}</span>` : ""}
      ${u.status === "online" ? '<i class="online-dot"></i>' : ""}
      <div class="shade"></div>
      <div class="nm">${esc(u.name)}</div>
      <div class="meta"><span class="rate">★${u.rating.toFixed(1)}</span>${tab === "nearby" && !S.settings.hideDistance ? `${u.distance.toFixed(1)}km` : esc(u.city)}</div>
      ${priceHtml}</a>`;
  }
  const BANNERS = [
    { bg: "linear-gradient(120deg,#8d5cff,#e45cff 60%,#ff7ab0)", h: "邀请用户得奖励", p: "超值大奖等你拿", d: "10月1日-10月31日", go: "/invite" },
    { bg: "linear-gradient(120deg,#ffcf8a,#ffb26b)", h: "月满中秋 礼遇国庆", p: "充值最高送 700 金币", d: "限时活动", go: "/recharge" },
    { bg: "linear-gradient(120deg,#36c6f4,#5b8cff)", h: "新人专享", p: "首次视频通话 5 折", d: "注册 7 天内有效", go: "/recharge" },
  ];
  const bannerHtml = () => `<div class="banner" id="banner"><div class="slides">${BANNERS.map((b) =>
    `<div class="slide" data-go="${b.go}" style="background:${b.bg}"><h3>${b.h}</h3><p>${b.p}</p><span class="date">${b.d}</span></div>`).join("")}</div>
    <span class="tagline">视频速配交友</span><div class="dots">${BANNERS.map((_, i) => `<i class="${i ? "" : "on"}"></i>`).join("")}</div></div>`;
  function startBanner() {
    const b = $("#banner");
    if (!b) return;
    let i = 0;
    const t = setInterval(() => {
      i = (i + 1) % BANNERS.length;
      b.querySelector(".slides").style.transform = `translateX(-${i * 100}%)`;
      b.querySelectorAll(".dots i").forEach((d, k) => d.classList.toggle("on", k === i));
    }, 3500);
    onLeave(() => clearInterval(t));
  }

  route(/^\/discover(?:\/(\w+))?$/, (tab = "recommend") => {
    const chip = S.ui.chip[tab] || "all";
    const list = hostList(tab, chip);
    const cards = list.map((u) => cardHtml(u, tab));
    if (cards.length >= 4) cards.splice(4, 0, bannerHtml()); else cards.push(bannerHtml());
    return `<div class="top"><div class="top-row"><nav class="big-tabs">${DISC_TABS.map(([k, t]) =>
      `<a data-go="/discover/${k}" class="${k === tab ? "on" : ""}">${t}</a>`).join("")}</nav>
      <a class="top-act crown" data-go="/rank">${ic("crown")}排行</a><a class="top-act" data-act="filter">${ic("filter")}筛选</a></div></div>
      <div class="chips">${CHIPS[tab].map(([k, t, c, s]) => `<button class="chip ${k === chip ? "on" : ""}" data-act="chip" data-tab="${tab}" data-k="${k}"><span class="ci" style="background:${c}">${s}</span>${t}</button>`).join("")}</div>
      <div class="cards">${list.length ? cards.join("") : `<div style="grid-column:1/-1">${empty("", "没有符合条件的人，换个筛选试试")}</div>`}</div>`;
  }, { tab: "discover", mount: startBanner });

  acts.chip = (el) => { S.ui.chip[el.dataset.tab] = el.dataset.k; save(); render(); };
  acts.filter = () => {
    const f = { ...S.filter };
    const opt = (key, vals) => `<div class="opts">${vals.map((v) => `<button data-k="${key}" data-v="${v}" class="${f[key] === v ? "on" : ""}">${v}</button>`).join("")}</div>`;
    const sh = openSheet(`<h3>筛选</h3>
      <div class="s-row"><label>年龄</label>${opt("age", ["不限", "18-22", "23-27", "28以上"])}</div>
      <div class="s-row"><label>通话价格（金币/分钟）</label>${opt("price", ["不限", "30以下", "30-60", "60以上"])}</div>
      <div class="s-row"><label>其他</label><div class="opts"><button data-b="onlineOnly" class="${f.onlineOnly ? "on" : ""}">只看在线</button><button data-b="sameCity" class="${f.sameCity ? "on" : ""}">只看同城</button></div></div>
      <div class="btns"><button class="btn ghost" id="fReset">重置</button><button class="btn primary" id="fOk">确定</button></div>`);
    sh.querySelectorAll("[data-k]").forEach((b) => (b.onclick = () => {
      f[b.dataset.k] = b.dataset.v;
      sh.querySelectorAll(`[data-k="${b.dataset.k}"]`).forEach((x) => x.classList.toggle("on", x === b));
    }));
    sh.querySelectorAll("[data-b]").forEach((b) => (b.onclick = () => { f[b.dataset.b] = !f[b.dataset.b]; b.classList.toggle("on"); }));
    sh.querySelector("#fReset").onclick = () => { S.filter = fresh().filter; save(); closeLayer(); render(); };
    sh.querySelector("#fOk").onclick = () => { S.filter = f; save(); closeLayer(); render(); };
  };

  // 排行榜
  route(/^\/rank(?:\/(\w+))?(?:\/(\w+))?$/, (kind = "charm", period = "day") => {
    const mul = { day: 1, week: 6.3, month: 24.7 }[period];
    const list = [...M.users].sort((a, b) => ((b.seed * (kind === "charm" ? 7 : 11) + (period === "day" ? 0 : period === "week" ? 3 : 5)) % 20) -
      ((a.seed * (kind === "charm" ? 7 : 11) + (period === "day" ? 0 : period === "week" ? 3 : 5)) % 20));
    const val = (i) => `${((20 - i) * 0.83 * mul).toFixed(1)}万`;
    const label = kind === "charm" ? "魅力值" : "贡献值";
    const top = [list[1], list[0], list[2]];
    return `${pageHd("排行榜")}
      <div class="rank-hero"><div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">
        <div class="seg"><button data-go="/rank/charm/${period}" class="${kind === "charm" ? "on" : ""}">魅力榜</button><button data-go="/rank/rich/${period}" class="${kind === "rich" ? "on" : ""}">富豪榜</button></div>
        <div class="seg">${[["day", "日榜"], ["week", "周榜"], ["month", "月榜"]].map(([k, t]) => `<button data-go="/rank/${kind}/${k}" class="${k === period ? "on" : ""}">${t}</button>`).join("")}</div></div>
        <div class="podium">${top.map((u, k) => { const rank = [2, 1, 3][k]; return `<div class="p${rank}" data-go="/user/${u.id}"><span class="crown">${["🥇", "🥈", "🥉"][rank - 1]}</span>${av(u, rank === 1 ? 82 : 64)}<b>${esc(u.name)}</b><span>${label} ${val(rank - 1)}</span></div>`; }).join("")}</div></div>
      <div class="rank-list list">${list.slice(3).map((u, i) => `<a class="cell" data-go="/user/${u.id}"><span class="rank-no">${i + 4}</span>${av(u, 46)}<div class="grow">${esc(u.name)}<small>${esc(u.city)}</small></div><span class="val">${label} ${val(i + 3)}</span></a>`).join("")}</div>`;
  });

  // ======================= 用户主页 =======================
  route(/^\/user\/(\d+)$/, (id) => {
    const u = U(id);
    if (!u) return empty("用户不存在");
    const followed = S.follows.includes(u.id);
    const dot = { online: "", busy: "busy", offline: "off" }[u.status];
    const giftsGot = M.gifts.slice(0, 4 + (u.seed % 4));
    return `<header class="page-hd clear"><button class="back" data-act="back">${ic("back")}</button><h2></h2><div class="right"><button data-act="userMore" data-uid="${u.id}" style="color:#fff">${ic("more")}</button></div></header>
      <div class="u-hero" id="hero" data-photo="0" style="background-image:${M.art(u.seed, 0)}"><span class="live"><i class="${dot}"></i>${statusText(u)}</span><span class="pager" id="pager">1/3</span></div>
      <div class="u-body">
        <div class="u-name">${esc(u.name)} ${sexTag(u)} ${u.badge === "verified" ? '<span class="tag ver">已认证</span>' : ""}${u.badge === "goddess" ? '<span class="tag" style="background:var(--purple)">女神</span>' : ""}${u.vip ? '<span class="tag vip">VIP</span>' : ""}</div>
        <div class="u-sub"><span>${ic("location", "i")} ${esc(u.city)}${S.settings.hideDistance ? "" : ` · ${u.distance.toFixed(1)}km`}</span><span>ID: ${u.id}</span></div>
        <div class="u-stats"><div><b>${u.fans}</b><span>粉丝</span></div><div><b>${u.answerRate}%</b><span>接通率</span></div><div><b>${u.rating.toFixed(1)}</b><span>评分</span></div><div><b>${u.price}</b><span>金币/分钟</span></div></div>
        <div class="u-sec"><h4>个性签名</h4><div class="u-sign">${esc(u.sign)}</div></div>
        <div class="u-sec"><h4>标签</h4><div class="labels">${u.labels.map((l) => `<span>${l}</span>`).join("")}</div></div>
        <div class="u-sec"><h4>她的动态</h4><div class="thumbs">${[1, 2, 3, 4].map((v) => `<div data-act="viewPhoto" data-uid="${u.id}" data-v="${v}" style="background-image:${M.art(u.seed, v)}"></div>`).join("")}</div></div>
        <div class="u-sec"><h4>收到的礼物</h4><div class="giftwall">${giftsGot.map((g, i) => `<div><div class="g">${g.icon}</div>${g.name} ×${(u.seed * (i + 3)) % 30 + 1}</div>`).join("")}</div></div>
      </div>
      <div class="bottom-bar"><button class="mini ${followed ? "on" : ""}" data-act="follow" data-uid="${u.id}">${ic("heart")}${followed ? "已关注" : "关注"}</button>
        <button class="btn ghost" data-go="/chat/${u.id}">${ic("comment")}私信</button>
        <button class="btn primary" data-act="call" data-uid="${u.id}">${ic("video")}${u.status === "busy" ? "通话中" : `视频 ${u.price}币/分`}</button></div>`;
  }, {
    mount: (id) => {
      const hero = $("#hero");
      hero.onclick = () => {
        const n = (+hero.dataset.photo + 1) % 3;
        hero.dataset.photo = n;
        hero.style.backgroundImage = M.art(U(id).seed, n);
        $("#pager").textContent = `${n + 1}/3`;
      };
    },
  });
  acts.userMore = (el) => reportSheet(U(el.dataset.uid));
  acts.viewPhoto = (el) => {
    const u = U(el.dataset.uid);
    layer.innerHTML = `<div class="mask center" style="background:rgba(0,0,0,.92)"><div style="width:100%;max-width:480px;aspect-ratio:3/4;background:${M.art(u.seed, +el.dataset.v)} center/cover"></div></div>`;
    layer.firstChild.onclick = closeLayer;
  };
  acts.follow = (el) => {
    const id = +el.dataset.uid;
    const i = S.follows.indexOf(id);
    if (i >= 0) S.follows.splice(i, 1); else S.follows.push(id); // API: POST/DELETE /follows/:id
    S.me.follow = S.follows.length + 9;
    save();
    toast(i >= 0 ? "已取消关注" : "关注成功");
    if (el.closest(".call")) { el.textContent = i >= 0 ? "+关注" : "已关注"; return; }
    render();
  };

  // ======================= 视频通话 =======================
  acts.call = (el) => startCall(+el.dataset.uid);
  function startCall(id) {
    const u = U(id);
    if (u.status === "busy") return toast("对方正在通话中，请稍后再试");
    if (u.status === "offline") return dialog({ title: "对方不在线", text: "可以先给她留言，上线后会看到", ok: "去留言", onOk: () => go(`/chat/${id}`) });
    needCoins(u.price, () => go(`/call/${id}`));
  }

  route(/^\/call\/(\d+)$/, (id) => {
    const u = U(id);
    return `<div class="call ringing" id="call">
      <div class="remote" style="background-image:${M.art(u.seed, 0)}"></div>
      <div class="self ${S.beautyOn ? "beauty" : ""}" id="selfBox"><video id="selfVideo" autoplay muted playsinline></video><div class="off hidden" id="selfOff">摄像头已关闭</div></div>
      <div class="hdr hidden" id="callHdr">${av(u, 40)}<div class="who"><b>${esc(u.name)}</b><span id="callTime">00:00</span></div>
        <button class="fl" data-act="follow" data-uid="${u.id}">${S.follows.includes(u.id) ? "已关注" : "+关注"}</button></div>
      <div class="bill hidden" id="bill">${u.price} 金币/分钟<br>余额 <b id="callBal">${S.me.coins}</b> 金币</div>
      <div class="center" id="ringBox">${av(u, 100)}<b>${esc(u.name)}</b><span>正在等待对方接听…</span></div>
      <div class="ctrl">
        <button data-act="callMic"><span class="cb" id="cMic">${ic("mic")}</span><span>静音</span></button>
        <button data-act="callCam"><span class="cb" id="cCam">${ic("video")}</span><span>摄像头</span></button>
        <button class="hang" data-act="hangup"><span class="cb">${ic("phone")}</span><span>挂断</span></button>
        <button data-act="callBeauty"><span class="cb ${S.beautyOn ? "active" : ""}" id="cBeauty">${ic("magic")}</span><span>美颜</span></button>
        <button class="gift" data-act="callGift"><span class="cb">${ic("gift")}</span><span>礼物</span></button>
      </div></div>`;
  }, { mount: mountCall });

  let call = null;
  function mountCall(id) {
    const u = U(id);
    call = { u, secs: 0, connected: false, stream: null, mic: true, cam: true, timers: [] };
    const c = call;
    navigator.mediaDevices?.getUserMedia?.({ video: { facingMode: "user" }, audio: true })
      .then((s) => { if (call !== c) return s.getTracks().forEach((t) => t.stop()); c.stream = s; $("#selfVideo").srcObject = s; })
      .catch(() => { const o = $("#selfOff"); if (o) { o.classList.remove("hidden"); o.textContent = "未获得摄像头权限"; } });
    // API: 发起呼叫 -> 等对方接听（演示：2.5 秒后自动接通）
    c.timers.push(setTimeout(() => {
      if (call !== c) return;
      c.connected = true;
      if (!$("#call")) return;
      $("#call").classList.remove("ringing");
      $("#ringBox").classList.add("hidden");
      $("#callHdr").classList.remove("hidden");
      $("#bill").classList.remove("hidden");
      chargeMinute(c);
      c.timers.push(setInterval(() => {
        c.secs++;
        $("#callTime").textContent = mmss(c.secs);
        if (c.secs % 60 === 0) chargeMinute(c);
      }, 1000));
    }, 2500));
    onLeave(() => endCall(c, true));
  }
  function chargeMinute(c) {
    if (S.me.coins < c.u.price) {
      endCall(c);
      return dialog({ title: "余额不足，通话已结束", text: "充值后可以继续和她视频", ok: "去充值", onOk: () => go("/recharge") });
    }
    addLedger(`视频通话·${c.u.name}`, -c.u.price); // API: 服务端按分钟扣费，前端只显示
    const b = $("#callBal"); if (b) b.textContent = S.me.coins;
  }
  function endCall(c, silent) {
    if (!c || c.ended) return;
    c.ended = true;
    c.timers.forEach((t) => { clearTimeout(t); clearInterval(t); });
    c.stream?.getTracks().forEach((t) => t.stop());
    S.calls.unshift({ uid: c.u.id, secs: c.secs, ok: c.connected, t: `${today().slice(5)} ${now()}` });
    pushMsg(c.u.id, { me: true, call: c.connected ? `视频通话 ${mmss(c.secs)}` : "已取消" });
    save();
    if (call === c) call = null;
    if (silent) return;
    if (c.connected) rateSheet(c.u, c.secs); else back();
  }
  acts.hangup = () => endCall(call);
  acts.callMic = () => {
    if (!call) return;
    call.mic = !call.mic;
    call.stream?.getAudioTracks().forEach((t) => (t.enabled = call.mic));
    $("#cMic").classList.toggle("active", !call.mic);
    $("#cMic").innerHTML = ic(call.mic ? "mic" : "micOff");
    toast(call.mic ? "麦克风已打开" : "已静音");
  };
  acts.callCam = () => {
    if (!call) return;
    call.cam = !call.cam;
    call.stream?.getVideoTracks().forEach((t) => (t.enabled = call.cam));
    $("#cCam").classList.toggle("active", !call.cam);
    $("#cCam").innerHTML = ic(call.cam ? "video" : "camOff");
    $("#selfOff").classList.toggle("hidden", call.cam);
  };
  acts.callBeauty = () => {
    S.beautyOn = !S.beautyOn; save();
    $("#selfBox").classList.toggle("beauty", S.beautyOn);
    $("#cBeauty").classList.toggle("active", S.beautyOn);
    toast(S.beautyOn ? "美颜已开启" : "美颜已关闭");
  };
  acts.callGift = () => call && giftSheet(call.u, {
    onSent: (g) => {
      pushMsg(call.u.id, { me: true, gift: g });
      const el = document.createElement("div");
      el.className = "float-gift";
      el.innerHTML = `<div class="g">${g.icon}</div><p>送出 ${g.name}</p>`;
      $("#call").appendChild(el);
      setTimeout(() => el.remove(), 2000);
      const b = $("#callBal"); if (b) b.textContent = S.me.coins;
    },
  });
  function rateSheet(u, secs) {
    let stars = 5;
    const tags = ["颜值高", "声音好听", "聊得来", "很热情", "有礼貌", "网络卡顿"];
    const picked = new Set();
    const sh = openSheet(`<h3>通话结束 · ${mmss(secs)}</h3>
      <div style="text-align:center;margin-bottom:6px">${av(u, 64).replace('class="av"', 'class="av" style="margin:0 auto 8px"')}<b>${esc(u.name)}</b></div>
      <div style="text-align:center;font-size:34px;color:#ffb020;letter-spacing:6px" id="stars">${"★".repeat(5)}</div>
      <div class="opts" style="justify-content:center;margin:14px 0 18px">${tags.map((t) => `<button data-t="${t}">${t}</button>`).join("")}</div>
      <div class="btns"><button class="btn ghost" id="rSkip">跳过</button><button class="btn primary" id="rOk">提交评价</button></div>`);
    const starsEl = sh.querySelector("#stars");
    starsEl.onclick = (e) => {
      const r = starsEl.getBoundingClientRect();
      stars = Math.max(1, Math.min(5, Math.ceil(((e.clientX - r.left) / r.width) * 5)));
      starsEl.textContent = "★".repeat(stars) + "☆".repeat(5 - stars);
    };
    sh.querySelectorAll("[data-t]").forEach((b) => (b.onclick = () => { b.classList.toggle("on"); picked.has(b.dataset.t) ? picked.delete(b.dataset.t) : picked.add(b.dataset.t); }));
    const done = () => { closeLayer(); go(`/chat/${u.id}`); };
    sh.querySelector("#rSkip").onclick = done;
    sh.querySelector("#rOk").onclick = () => {
      S.ratings.unshift({ uid: u.id, stars, tags: [...picked], t: today() }); save(); // API: POST /calls/:id/rating
      toast("感谢评价"); done();
    };
  }

  // ======================= 社区 =======================
  const COMM_TABS = [["feed", "动态"], ["reels", "小视频"], ["city", "同城"], ["follow", "关注"]];
  function postHtml(p, i) {
    const u = p.uid === "me" ? meUser() : U(p.uid);
    const liked = !!S.likedPosts[i];
    const pics = p.images?.length
      ? `<div class="pics">${p.images.map((src) => `<div style="background-image:url('${src}')"></div>`).join("")}</div>`
      : p.pics === 1 ? `<div class="pic" data-act="viewPhoto" data-uid="${u.id}" data-v="${i + 5}" style="background-image:${M.art(u.seed, i + 5)}"></div>`
      : p.pics > 1 ? `<div class="pics">${[0, 1, 2].map((k) => `<div data-act="viewPhoto" data-uid="${u.id}" data-v="${i + 5 + k}" style="background-image:${M.art(u.seed, i + 5 + k)}"></div>`).join("")}</div>` : "";
    return `<article class="post"><div class="post-hd"><a data-go="${u.isMe ? "/me" : `/user/${u.id}`}">${av(u, 48)}</a>
      <div class="who"><b>${esc(u.name)}</b>${sexTag(u)}${u.badge === "verified" || (u.isMe && S.verified) ? '<span class="tag ver">已认证</span>' : ""}${u.vip ? '<span class="tag vip">VIP</span>' : ""}</div>
      <button class="more" data-act="postMore" data-i="${i}">${ic("more")}</button></div>
      ${p.text ? `<div class="txt">${esc(p.text)}</div>` : ""}${pics}
      <div class="time">${p.time}</div>
      <div class="acts"><button data-act="like" data-i="${i}" class="${liked ? "liked" : ""}">${ic("heart")}${p.likes + (liked ? 1 : 0)}</button>
      <button data-act="comments" data-i="${i}">${ic("comment")}${p.comments.length ? p.comments.length : "评论"}</button></div></article>`;
  }
  route(/^\/community(?:\/(\w+))?$/, (tab = "feed") => {
    const head = (dark) => `<div class="top ${dark ? "reels-top" : ""}"><div class="top-row"><nav class="big-tabs">${COMM_TABS.map(([k, t]) =>
      `<a data-go="/community/${k}" class="${k === tab ? "on" : ""}">${t}</a>`).join("")}</nav>
      ${dark ? `<button class="pub-btn" data-go="/publish">+发布</button>` : `<a class="top-act" data-act="feedFilter">${ic("filter")}筛选</a>`}</div></div>`;
    if (tab === "reels") {
      return `<div class="reels">${M.reels.map((r, i) => {
        const u = U(r.uid); const liked = !!S.reelLikes[i];
        return `<section class="reel"><div class="bg" style="background-image:${M.art(u.seed, 20 + i)}"></div>
          <div class="play-hint">${ic("play", "i")}</div>
          <div class="info"><b>@${esc(u.name)}</b><p>${esc(r.text)}</p></div>
          <div class="side"><a data-go="/user/${u.id}" style="position:relative">${av(u, 56).replace('class="av"', 'class="av rav"')}${S.follows.includes(u.id) ? "" : `<span class="plus" data-act="follow" data-uid="${u.id}" style="position:absolute;left:50%;bottom:-10px;margin-left:-12px;width:24px;height:18px;border-radius:9px;background:var(--c1);display:grid;place-items:center">+</span>`}</a>
            <button data-act="reelLike" data-i="${i}" class="${liked ? "liked" : ""}">${ic("heart")}${r.likes + (liked ? 1 : 0)}</button>
            <button data-act="reelComment" data-i="${i}">${ic("comment")}${r.comments}</button>
            <button data-act="share">${ic("share")}分享</button></div>
          <button class="dm" data-go="/chat/${u.id}">私信聊天<i>${ic("comment")}</i></button></section>`;
      }).join("")}</div>${head(true)}`;
    }
    let idx = S.posts.map((p, i) => i);
    if (tab === "city") idx = idx.filter((i) => S.posts[i].uid === "me" || U(S.posts[i].uid).city === S.me.city || i % 2 === 0);
    if (tab === "follow") idx = idx.filter((i) => S.follows.includes(S.posts[i].uid));
    if (S.ui.feedOnlyVerified) idx = idx.filter((i) => S.posts[i].uid !== "me" && U(S.posts[i].uid).badge === "verified");
    return `${head(false)}${idx.length ? idx.map((i) => postHtml(S.posts[i], i)).join("")
      : empty(tab === "follow" ? "还没有关注的人发布动态" : "暂无动态", "去发现页关注感兴趣的人吧")}
      <div style="height:90px"></div><button class="fab" data-go="/publish">${ic("plane")}发布</button>`;
  }, {
    tab: "community",
    mount: (tab = "feed") => { if (tab === "reels") appEl.classList.add("dark-tab"); },
  });
  acts.like = (el) => { const i = +el.dataset.i; S.likedPosts[i] = !S.likedPosts[i]; save(); render(); };
  acts.reelLike = (el) => {
    const i = +el.dataset.i; S.reelLikes[i] = !S.reelLikes[i]; save();
    el.classList.toggle("liked", S.reelLikes[i]);
    el.lastChild.textContent = M.reels[i].likes + (S.reelLikes[i] ? 1 : 0);
  };
  acts.share = () => {
    const link = location.href;
    navigator.clipboard?.writeText(link).then(() => toast("链接已复制，快去分享吧"), () => toast("分享链接：" + link));
  };
  acts.feedFilter = () => actionSheet([
    { text: `${S.ui.feedOnlyVerified ? "" : "✓ "}全部动态`, fn: () => { S.ui.feedOnlyVerified = false; save(); render(); } },
    { text: `${S.ui.feedOnlyVerified ? "✓ " : ""}只看已认证`, fn: () => { S.ui.feedOnlyVerified = true; save(); render(); } },
  ]);
  acts.postMore = (el) => {
    const p = S.posts[+el.dataset.i];
    if (p.uid === "me") return actionSheet([{ text: "删除动态", danger: true, fn: () => dialog({ title: "删除动态", text: "删除后无法恢复", onOk: () => { S.posts.splice(+el.dataset.i, 1); S.likedPosts = {}; save(); render(); } }) }]);
    reportSheet(U(p.uid));
  };
  function commentSheet(list, onAdd) {
    const draw = () => list.length ? list.map((c) => `<div style="padding:10px 0;border-bottom:1px solid var(--line)"><b style="font-weight:500;color:var(--text-2)">${esc(c.name)}</b>：${esc(c.text)}</div>`).join("")
      : '<p style="text-align:center;color:var(--muted);padding:20px 0">还没有评论，快来抢沙发</p>';
    const sh = openSheet(`<h3>评论 ${list.length || ""}</h3><div id="cList" style="max-height:40vh;overflow:auto">${draw()}</div>
      <form id="cForm" style="display:flex;gap:8px;margin-top:12px"><input id="cIn" maxlength="200" placeholder="说点什么…" style="flex:1;height:40px;border:0;border-radius:20px;background:var(--bg-2);padding:0 14px;outline:none"><button class="btn primary" style="height:40px">发送</button></form>`);
    sh.querySelector("#cForm").onsubmit = (e) => {
      e.preventDefault();
      const t = sh.querySelector("#cIn").value.trim();
      if (!t) return;
      list.push({ name: S.me.name, text: t }); onAdd?.(); // API: POST /posts/:id/comments
      sh.querySelector("#cIn").value = "";
      sh.querySelector("#cList").innerHTML = draw();
      sh.querySelector("h3").textContent = `评论 ${list.length}`;
    };
  }
  acts.comments = (el) => {
    const list = S.posts[+el.dataset.i].comments;
    commentSheet(list, () => { save(); el.lastChild.textContent = list.length; });
  };
  acts.reelComment = (el) => {
    const i = +el.dataset.i;
    S.ui.reelComments = S.ui.reelComments || {};
    const list = (S.ui.reelComments[i] = S.ui.reelComments[i] || [{ name: "晚风", text: "好好看" }]);
    commentSheet(list, save);
  };

  // 发布动态
  let draftImgs = [];
  route(/^\/publish$/, () => {
    draftImgs = [];
    return `${pageHd("发布动态", `<button class="btn primary" style="height:32px;font-size:14px;padding:0 16px" data-act="doPublish">发布</button>`)}
      <div class="publish-box"><textarea id="pubText" maxlength="500" placeholder="分享你的生活，认识更多有趣的人…"></textarea>
      <div class="pub-imgs" id="pubImgs"></div><input type="file" id="pubFile" accept="image/*" multiple hidden></div>
      <div class="group-gap"></div>
      <div class="list"><a class="cell" data-act="pubLoc">${ic("location")}<div class="grow">所在位置</div><span class="val" id="pubLocV">${esc(S.me.city)}</span>${ic("chev", "i chev")}</a>
      <a class="cell" data-act="pubWho">${ic("lock")}<div class="grow">谁可以看</div><span class="val" id="pubWhoV">所有人</span>${ic("chev", "i chev")}</a></div>`;
  }, { mount: drawPubImgs });
  function drawPubImgs() {
    const box = $("#pubImgs");
    if (!box) return;
    box.innerHTML = draftImgs.map((src, i) => `<div style="background-image:url('${src}')"><span class="del" data-act="pubDel" data-i="${i}">×</span></div>`).join("")
      + (draftImgs.length < 9 ? `<label class="add" for="pubFile">${ic("plus")}</label>` : "");
    $("#pubFile").onchange = async (e) => {
      for (const f of [...e.target.files].slice(0, 9 - draftImgs.length)) draftImgs.push(await shrink(f, 900));
      e.target.value = ""; drawPubImgs();
    };
  }
  acts.pubDel = (el) => { draftImgs.splice(+el.dataset.i, 1); drawPubImgs(); };
  acts.pubLoc = () => actionSheet([S.me.city, "不显示位置"].map((t) => ({ text: t, fn: () => ($("#pubLocV").textContent = t) })));
  acts.pubWho = () => actionSheet(["所有人", "仅关注我的人", "仅自己"].map((t) => ({ text: t, fn: () => ($("#pubWhoV").textContent = t) })));
  acts.doPublish = () => {
    const text = $("#pubText").value.trim();
    if (!text && !draftImgs.length) return toast("写点什么或者选张图片吧");
    S.posts.unshift({ uid: "me", text, images: draftImgs.slice(0, 3), pics: 0, time: "刚刚", likes: 0, comments: [] }); // API: POST /posts
    S.likedPosts = {};
    save(); toast("发布成功"); go("/community/feed");
  };
  // 压缩图片为 dataURL（演示期存本机；接后端后改为上传）
  function shrink(file, max) {
    return new Promise((res) => {
      const img = new Image();
      img.onload = () => {
        const k = Math.min(1, max / Math.max(img.width, img.height));
        const c = document.createElement("canvas");
        c.width = img.width * k; c.height = img.height * k;
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(img.src);
        res(c.toDataURL("image/jpeg", 0.8));
      };
      img.src = URL.createObjectURL(file);
    });
  }

  // ======================= 消息 =======================
  route(/^\/messages(?:\/(\w+))?$/, (tab = "list") => {
    const head = `<div class="top"><div class="top-row"><nav class="big-tabs"><a data-go="/messages/list" class="${tab === "list" ? "on" : ""}">消息</a><a data-go="/messages/calls" class="${tab === "calls" ? "on" : ""}">通话记录</a></nav>
      ${tab === "list" ? `<a class="top-act" data-act="clearUnread">${ic("clear")}清空</a>` : ""}</div></div>`;
    if (tab === "calls") {
      return head + (S.calls.length ? `<div class="list">${S.calls.map((c) => { const u = U(c.uid); return `<div class="cell"><a data-go="/user/${u.id}">${av(u, 50)}</a><div class="grow">${esc(u.name)}<small style="color:${c.ok ? "var(--muted)" : "var(--red)"}">${c.ok ? `视频通话 ${mmss(c.secs)}` : "未接通"} · ${c.t}</small></div><button data-act="call" data-uid="${u.id}" style="color:var(--c2)">${ic("video")}</button></div>`; }).join("")}</div>`
        : `<p style="text-align:center;color:var(--text-2);font-weight:600;margin:24px 0 0">已经全部加载完毕</p>${empty("", "暂时没有找到通话记录", "去拨打视频试试吧")}`);
    }
    const online = M.users.filter((u) => u.status === "online").slice(0, 8);
    const sysUnread = S.sys.filter((x) => x.unread).length;
    return `${head}
      <div class="notice-banner" data-go="/antifraud">防范电信诈骗宣传手册</div>
      <div class="online-row">${online.map((u) => `<a data-go="/user/${u.id}">${av(u, 64, '<i class="on-dot"></i>')}${esc(u.name)}</a>`).join("")}</div>
      <div class="conv-list">
        <a class="conv pinned" data-go="/service"><div class="av sys-av" style="width:58px;height:58px;background:linear-gradient(135deg,#ffb3cf,#c9a5ff)">${ic("comment")}</div><div class="body"><div class="l1"><b>SeeU 客服</b><span class="tag official">官方</span><time>${S.service[0].t}</time></div><div class="l2"><span>${esc(S.service[0].text)}</span></div></div></a>
        <a class="conv pinned" data-go="/system"><div class="av sys-av" style="width:58px;height:58px">${ic("bellFill")}</div><div class="body"><div class="l1"><b>系统消息</b><time>${S.sys[0]?.t || ""}</time></div><div class="l2"><span>${esc(S.sys[0]?.text || "暂无")}</span>${sysUnread ? `<i class="dot-badge">${sysUnread}</i>` : ""}</div></div></a>
        ${S.convs.map((c) => { const u = U(c.uid); return `<a class="conv" data-go="/chat/${u.id}">${av(u, 58, u.status === "online" ? '<i class="on-dot"></i>' : "")}<div class="body"><div class="l1"><b>${esc(u.name)}</b><time>${c.time}</time></div><div class="l2"><span>${esc(c.last)}</span>${c.unread ? `<i class="dot-badge">${c.unread}</i>` : ""}</div></div></a>`; }).join("")}
      </div>`;
  }, { tab: "messages" });
  acts.clearUnread = () => dialog({ title: "清空未读", text: "将所有消息标记为已读？", onOk: () => { S.convs.forEach((c) => (c.unread = 0)); S.sys.forEach((x) => (x.unread = false)); save(); render(); } });

  route(/^\/antifraud$/, () => `${pageHd("防范电信诈骗")}<div class="page-pad" style="line-height:1.8">
    <div class="balance-card" style="margin:0 0 16px;background:linear-gradient(135deg,#c9a5ff,#ff9ac0)"><b style="font-size:22px">守护你的钱包安全</b><small>平台不会以任何理由要求你私下转账</small></div>
    ${["凡是要求「私下转账」「刷单返利」「投资带你赚钱」的，一律是诈骗。", "不要点击聊天中陌生人发来的链接，不要下载陌生 App。", "对方以「见面」「垫付车费」「生病急用钱」为由借钱，请立即停止并举报。",
       "不要向任何人透露验证码、银行卡号、支付密码。", "遇到可疑情况，在对方主页右上角「…」举报，或拨打 96110 反诈专线。"].map((t, i) =>
       `<div style="display:flex;gap:10px;margin-bottom:12px"><span style="flex:none;width:24px;height:24px;border-radius:50%;background:var(--grad);color:#fff;display:grid;place-items:center;font-size:13px">${i + 1}</span><span>${t}</span></div>`).join("")}</div>`);

  route(/^\/system$/, () => {
    S.sys.forEach((x) => (x.unread = false)); save();
    return `${pageHd("系统消息")}<div class="bg-gray page-pad">${S.sys.map((x) => `<div style="background:#fff;border-radius:12px;padding:14px;margin-bottom:10px"><small style="color:var(--muted)">${x.t}</small><div style="margin-top:6px">${esc(x.text)}</div></div>`).join("")}</div>`;
  });

  // ======================= 聊天 =======================
  function pushMsg(uid, msg) {
    (S.chats[uid] = S.chats[uid] || []).push({ ...msg, t: now() });
    const preview = msg.text || (msg.img ? "[图片]" : msg.gift ? `[礼物] ${msg.gift.name}` : msg.call ? `[${msg.call}]` : "");
    const c = S.convs.find((x) => x.uid === uid);
    if (c) { c.last = preview; c.time = now(); S.convs.splice(S.convs.indexOf(c), 1); S.convs.unshift(c); }
    else S.convs.unshift({ uid, last: preview, time: now(), unread: 0 });
    save();
  }
  function msgHtml(m, u) {
    const who = m.me ? meUser() : u;
    const body = m.img ? `<img class="img" src="${m.img}" alt="">`
      : m.gift ? `<div class="gift-bub"><span class="g">${m.gift.icon}</span>送出 ${m.gift.name}</div>`
      : m.call ? `<div class="bub call-bub">${ic("video", "i")}${esc(m.call)}</div>`
      : `<div class="bub">${esc(m.text)}</div>`;
    return `<div class="m ${m.me ? "me" : ""}">${av(who, 40)}${body}</div>`;
  }
  route(/^\/chat\/(\d+)$/, (id) => {
    const u = U(id);
    const c = S.convs.find((x) => x.uid === u.id);
    if (c) c.unread = 0;
    if (!S.chats[u.id] && c) S.chats[u.id] = [{ me: false, text: c.last, t: c.time }];
    save();
    const msgs = S.chats[u.id] || [];
    return `<div class="chat-wrap">${pageHd(`${esc(u.name)}<small style="display:block;font-size:11px;color:${u.status === "online" ? "var(--green)" : "var(--muted)"};font-weight:400">${statusText(u)}</small>`,
      `<button data-act="chatMore" data-uid="${u.id}">${ic("more")}</button>`)}
      <div class="chat-list" id="chatList"><div class="chat-tip">平台倡导文明交友，请勿私下转账、点击陌生链接，谨防诈骗</div>${msgs.map((m) => msgHtml(m, u)).join("")}</div>
      <form class="chat-bar" id="chatForm"><div class="row1"><input id="chatIn" maxlength="500" placeholder="说点什么…" autocomplete="off"><button class="send">发送</button></div>
        <div class="row2"><label for="chatImg" style="display:flex;flex-direction:column;align-items:center;font-size:11px;gap:2px;cursor:pointer">${ic("image")}图片</label><input type="file" id="chatImg" accept="image/*" hidden>
        <button type="button" data-act="chatGift" data-uid="${u.id}">${ic("gift")}礼物</button>
        <button type="button" class="vc" data-act="call" data-uid="${u.id}">${ic("video")}视频通话</button></div></form></div>`;
  }, {
    mount: (id) => {
      const u = U(id);
      const list = $("#chatList");
      const add = (m) => { list.insertAdjacentHTML("beforeend", msgHtml(m, u)); list.scrollTop = list.scrollHeight; };
      list.scrollTop = list.scrollHeight;
      $("#chatForm").onsubmit = (e) => {
        e.preventDefault();
        const t = $("#chatIn").value.trim();
        if (!t) return;
        $("#chatIn").value = "";
        const m = { me: true, text: t }; pushMsg(u.id, m); add(m); // API: WebSocket 发送
      };
      $("#chatImg").onchange = async (e) => {
        const f = e.target.files[0]; if (!f) return;
        const m = { me: true, img: await shrink(f, 600) }; pushMsg(u.id, m); add(m); e.target.value = "";
      };
      acts.chatGift = () => giftSheet(u, { dark: false, onSent: (g) => { const m = { me: true, gift: g }; pushMsg(u.id, m); add(m); } });
    },
  });
  acts.chatMore = (el) => {
    const u = U(el.dataset.uid);
    actionSheet([
      { text: "查看资料", fn: () => go(`/user/${u.id}`) },
      { text: "清空聊天记录", fn: () => dialog({ title: "清空聊天记录", text: "清空后无法恢复", onOk: () => { S.chats[u.id] = []; save(); render(); } }) },
      { text: "举报", fn: () => reportSheet(u) },
      { text: "拉黑", danger: true, fn: () => dialog({ title: "拉黑", text: `拉黑后将不再收到 ${esc(u.name)} 的消息和来电`, onOk: () => toast("已拉黑") }) },
    ]);
  };

  route(/^\/service$/, () => `<div class="chat-wrap">${pageHd("SeeU 客服")}<div class="chat-list" id="svcList">
      ${S.service.map((m) => m.me ? `<div class="m me">${av(meUser(), 40)}<div class="bub">${esc(m.text)}</div></div>` : `<div class="m"><div class="av sys-av" style="width:40px;height:40px;background:linear-gradient(135deg,#ffb3cf,#c9a5ff)">${ic("comment")}</div><div class="bub">${esc(m.text)}</div></div>`).join("")}
      <div class="opts" style="padding-left:48px">${["如何充值", "通话怎么收费", "如何提现", "举报投诉"].map((q) => `<button data-act="faq" data-q="${q}">${q}</button>`).join("")}</div></div>
      <form class="chat-bar" id="svcForm"><div class="row1"><input id="svcIn" placeholder="描述你遇到的问题…"><button class="send">发送</button></div></form></div>`, {
    mount: () => {
      $("#svcList").scrollTop = 1e6;
      $("#svcForm").onsubmit = (e) => {
        e.preventDefault();
        const t = $("#svcIn").value.trim(); if (!t) return;
        S.service.push({ me: true, text: t, t: today() }, { text: "已收到，客服会在 10 分钟内回复你（演示）", t: today() }); save(); render();
      };
    },
  });
  acts.faq = (el) => {
    const A = { 如何充值: "「我的」→「充值」，选择金额并用微信或支付宝支付即可。", 通话怎么收费: "按对方设置的价格每分钟扣金币，不足 1 分钟按 1 分钟计。", 如何提现: "主播收益可在「我的钱包」申请提现，1-3 个工作日到账。", 举报投诉: "在对方主页或聊天页右上角「…」选择举报，我们会 24 小时内处理。" };
    S.service.push({ me: true, text: el.dataset.q, t: today() }, { text: A[el.dataset.q], t: today() }); save(); render();
  };

  // ======================= 我的 =======================
  route(/^\/me$/, () => {
    const me = meUser();
    const cell = (go, icon, text, act) => `<a ${act ? `data-act="${act}"` : `data-go="${go}"`}>${ic(icon)}<span>${text}</span></a>`;
    return `<div class="me-hd"><a data-go="/edit">${av(me, 84)}</a><div><div class="nm">${esc(me.name)}${S.me.vip ? '<span class="tag vip">VIP</span>' : ""}<span class="tag lv">◆ Lv${me.level}</span></div><div class="id">ID：${me.id}</div></div></div>
      <div class="me-stats"><a data-go="/follows/following"><b>${S.follows.length + 9}</b>关注</a><i class="sep"></i><a data-go="/follows/fans"><b>${me.fans}</b>粉丝</a><a class="edit" data-go="/edit">编辑个人资料 ›</a></div>
      <div class="quick">
        <a data-go="/recharge"><span class="qi" style="background:linear-gradient(135deg,#7cc8ff,#4f9bff)">${ic("coinCard")}</span><b>充值</b><small>余额 ${S.me.coins}</small></a>
        <a data-go="/earn"><span class="qi" style="background:linear-gradient(135deg,#ff8a7a,#ff4d6a)">${ic("gift")}</span><b>免费赚金币</b><small>分享获收益</small></a>
        <a data-go="/vip"><span class="qi" style="background:linear-gradient(135deg,#c58bff,#8d5cff)">${ic("crown")}</span><b>VIP</b><small>更多特权</small></a>
        <a data-go="/invite"><span class="qi" style="background:linear-gradient(135deg,#ff9ac0,#ff5c9a)">${ic("plus")}</span><b>邀请好友得现金</b><small>得现金</small></a>
      </div>
      <div class="sec-title">设置中心</div>
      <div class="icon-grid">
        <a data-act="dnd"><i class="switch ${S.settings.dnd ? "on" : ""}"></i><span>免打扰</span></a>
        ${cell("/verify", "camera", S.verified ? "已认证" : "视频认证")}${cell("/beauty", "beautyStar", "美颜设置")}${cell("/privacy", "lock", "隐私设置")}
      </div>
      <div class="sec-title">与我相关</div>
      <div class="icon-grid">${cell("/wallet", "wallet", "我的钱包")}${cell("/myposts", "community", "我的动态")}${cell("/guard", "guard", "我的守护")}${cell("/mygifts", "gift", "我的礼物")}
        ${cell("/rates", "tag", "通话评价")}${cell("/visitors", "clock", "访问足迹")}</div>
      <div class="sec-title">其他</div>
      <div class="icon-grid">${cell("/games", "game", "游戏技能")}${cell("/gameorders", "doc", "游戏订单")}${cell("/language", "lang", "语言设置")}${cell("/settings", "gear", "其他设置")}</div>
      <div style="height:30px"></div>`;
  }, { tab: "me" });
  acts.dnd = () => {
    S.settings.dnd = !S.settings.dnd; save(); render();
    toast(S.settings.dnd ? "免打扰已开启，将不会收到视频来电" : "免打扰已关闭");
  };

  // 编辑资料
  route(/^\/edit$/, () => {
    const me = S.me;
    return `${pageHd("编辑资料", `<button data-act="saveProfile" style="color:var(--c2);font-weight:600">保存</button>`)}<div class="bg-gray">
      <label class="form-row" for="avFile"><span style="flex:1">头像</span>${av(meUser(), 56).replace('class="av"', 'class="av" id="avPrev"')}${ic("chev", "i chev")}</label><input type="file" id="avFile" accept="image/*" hidden>
      <div class="form-row"><label>昵称</label><input id="eName" maxlength="12" value="${esc(me.name)}"></div>
      <div class="form-row"><label>性别</label><select id="eSex"><option value="m" ${me.sex === "m" ? "selected" : ""}>男</option><option value="f" ${me.sex === "f" ? "selected" : ""}>女</option></select></div>
      <div class="form-row"><label>年龄</label><input id="eAge" type="number" min="18" max="80" value="${me.age}"></div>
      <div class="form-row"><label>城市</label><input id="eCity" maxlength="10" value="${esc(me.city)}"></div>
      <div class="form-row" style="align-items:flex-start"><label>个性签名</label><textarea id="eSign" maxlength="60">${esc(me.sign)}</textarea></div></div>`;
  }, {
    mount: () => {
      $("#avFile").onchange = async (e) => {
        const f = e.target.files[0]; if (!f) return;
        S.ui.avatarDraft = await shrink(f, 300);
        $("#avPrev").style.backgroundImage = `url("${S.ui.avatarDraft}")`;
      };
    },
  });
  acts.saveProfile = () => {
    const name = $("#eName").value.trim();
    const age = +$("#eAge").value;
    if (!name) return toast("昵称不能为空");
    if (!(age >= 18 && age <= 80)) return toast("年龄需在 18-80 之间");
    Object.assign(S.me, { name, age, sex: $("#eSex").value, city: $("#eCity").value.trim() || S.me.city, sign: $("#eSign").value.trim() });
    if (S.ui.avatarDraft) { S.me.avatar = S.ui.avatarDraft; delete S.ui.avatarDraft; }
    save(); toast("资料已保存"); back("/me"); // API: PUT /me
  };

  route(/^\/follows\/(\w+)$/, (kind) => {
    const list = kind === "following" ? M.users.filter((u) => S.follows.includes(u.id)) : M.users.filter((u) => u.seed % 2 === 0).slice(0, 8);
    return `${pageHd(kind === "following" ? "我的关注" : "我的粉丝")}${list.length ? `<div class="list">${list.map((u) => {
      const f = S.follows.includes(u.id);
      return `<div class="cell"><a data-go="/user/${u.id}">${av(u, 46)}</a><div class="grow">${esc(u.name)}<small>${esc(u.sign)}</small></div><button class="btn ${f ? "ghost" : "primary"}" style="height:30px;font-size:13px;padding:0 14px" data-act="follow" data-uid="${u.id}">${f ? "已关注" : kind === "fans" ? "回关" : "关注"}</button></div>`;
    }).join("")}</div>` : empty("", "还没有关注任何人", "去发现页看看吧")}`;
  });

  // 充值
  route(/^\/recharge$/, () => `${pageHd("充值", `<a data-go="/wallet">明细</a>`)}
    <div class="balance-card"><small>金币余额</small><b>${S.me.coins}</b><small>1 元 = 10 金币 · 视频通话、送礼物使用</small></div>
    <div class="packs">${M.packs.map((p, i) => `<button class="pack ${i === 2 ? "on" : ""}" data-act="pickPack" data-i="${i}">${p.hot ? '<span class="hot">热门</span>' : ""}<b>${p.coins}<small>金币</small></b><span>¥${p.yuan}</span>${p.bonus ? `<div style="font-size:11px;color:var(--red);margin-top:2px">加送 ${p.bonus}</div>` : ""}</button>`).join("")}</div>
    <div class="sec-title" style="padding-top:22px">支付方式</div>
    <div class="pay-ways"><button class="pay-way on" data-act="pickPay" data-v="wx" style="width:100%"><span class="pi" style="background:#09bb07">微</span>微信支付<i class="radio"></i></button>
      <button class="pay-way" data-act="pickPay" data-v="ali" style="width:100%"><span class="pi" style="background:#1677ff">支</span>支付宝<i class="radio"></i></button></div>
    <div class="agree">充值即代表同意 <a>《充值服务协议》</a>，未成年人禁止充值。如遇问题请联系客服。</div>
    <div style="height:90px"></div>
    <div class="bottom-bar"><button class="btn primary" style="flex:1" data-act="pay" id="payBtn">立即支付 ¥${M.packs[2].yuan}</button></div>`,
  { mount: () => { S.ui.pack = 2; S.ui.pay = "wx"; } });
  acts.pickPack = (el) => {
    S.ui.pack = +el.dataset.i;
    document.querySelectorAll(".pack").forEach((p) => p.classList.toggle("on", p === el));
    $("#payBtn").textContent = `立即支付 ¥${M.packs[S.ui.pack].yuan}`;
  };
  acts.pickPay = (el) => { S.ui.pay = el.dataset.v; document.querySelectorAll(".pay-way").forEach((p) => p.classList.toggle("on", p === el)); };
  acts.pay = () => {
    const p = M.packs[S.ui.pack];
    // API: POST /orders -> 拉起微信/支付宝支付 -> 支付回调后服务端加金币
    dialog({
      title: "演示支付", text: `支付接口接入后端后才会真正扣款。演示模式下直接到账 ${p.coins + (p.bonus || 0)} 金币。`, ok: "模拟支付成功",
      onOk: () => {
        addLedger("充值", p.coins + (p.bonus || 0));
        S.sys.unshift({ t: today(), text: `金额：${p.yuan.toFixed(2)}；充值：${p.coins + (p.bonus || 0)}；账户余额：${S.me.coins}`, unread: true });
        save(); toast("充值成功"); render();
      },
    });
  };

  route(/^\/wallet$/, () => `${pageHd("我的钱包")}
    <div class="balance-card"><small>金币余额</small><b>${S.me.coins}</b><div style="display:flex;gap:10px;margin-top:12px"><button class="btn" style="background:#fff;color:#ff7a59;height:36px;font-size:14px" data-go="/recharge">充值</button><button class="btn" style="background:rgba(255,255,255,.25);color:#fff;height:36px;font-size:14px" data-act="withdraw">提现</button></div></div>
    <div class="sec-title" style="padding-top:6px">收支明细</div>
    <div class="list">${S.ledger.map((l) => `<div class="cell"><div class="grow">${esc(l.title)}<small>${l.t}</small></div><b style="color:${l.amount > 0 ? "var(--green)" : "var(--text)"}">${l.amount > 0 ? "+" : ""}${l.amount}</b></div>`).join("")}</div>`);
  acts.withdraw = () => dialog({ title: "提现", text: "普通用户充值的金币不可提现；成为认证主播后，收到的礼物和通话收益可以提现。", cancel: "", ok: "知道了" });

  route(/^\/earn$/, () => {
    const signed = S.signed === today();
    const task = (icon, title, reward, btn, act, done) => `<div class="task"><span class="ti2">${icon}</span><div class="grow"><b>${title}</b><small>${reward}</small></div><button class="btn ${done ? "ghost" : "primary"}" ${done ? "disabled" : ""} data-act="${act}">${btn}</button></div>`;
    return `${pageHd("免费赚金币")}<div class="balance-card" style="background:linear-gradient(135deg,#ff8a7a,#ff4d6a)"><small>当前金币</small><b>${S.me.coins}</b><small>完成任务领取金币，可用于视频通话</small></div>
      ${task("📅", "每日签到", "+1 金币", signed ? "已签到" : "签到", "signIn", signed)}
      ${task("👤", "完善个人资料", "+5 金币", S.ui.taskProfile ? "已领取" : "去完善", "taskProfile", S.ui.taskProfile)}
      ${task("📷", "完成视频认证", "+10 金币", S.verified ? "已完成" : "去认证", "taskVerify", S.verified)}
      ${task("🔗", "分享给好友", "+2 金币 / 每日", S.ui.shared === today() ? "已分享" : "去分享", "taskShare", S.ui.shared === today())}
      ${task("🎁", "邀请好友注册", "+50 金币 / 人", "去邀请", "taskInvite", false)}`;
  });
  acts.signIn = () => {
    S.signed = today(); addLedger("签到奖励", 1);
    S.service.unshift({ t: today(), text: "签到成功，获赠 1 币，快去免费打给钟意的人吧！" }); save();
    toast("签到成功 +1 金币"); render();
  };
  acts.taskProfile = () => { if (!S.ui.taskProfile) { S.ui.taskProfile = true; addLedger("完善资料奖励", 5); } go("/edit"); };
  acts.taskVerify = () => go("/verify");
  acts.taskShare = () => { acts.share(); if (S.ui.shared !== today()) { S.ui.shared = today(); addLedger("分享奖励", 2); } setTimeout(render, 300); };
  acts.taskInvite = () => go("/invite");

  route(/^\/vip$/, () => `${pageHd("VIP 会员")}<div class="vip-hero"><h2>👑 SeeU VIP</h2><p>${S.me.vip ? `会员有效期至 ${S.vipUntil}` : "开通会员，畅享专属特权"}</p></div>
    <div class="vip-plans">${[["1个月", 30, 45], ["3个月", 78, 135], ["12个月", 258, 540]].map(([t, p, o], i) => `<button class="vip-plan ${i === 1 ? "on" : ""}" data-act="pickVip" data-p="${p}">${t}<b>¥${p}</b><s>¥${o}</s></button>`).join("")}</div>
    <div class="perks">${[["👑", "尊贵标识"], ["💬", "消息置顶"], ["👀", "查看访客"], ["💰", "通话 9 折"], ["🎁", "每日赠 10 币"], ["🕶️", "隐身访问"]].map(([i, t]) => `<div><i>${i}</i>${t}</div>`).join("")}</div>
    <div class="page-pad"><button class="btn block" style="background:linear-gradient(135deg,#f4d8a0,#d9a85b);color:#4a3418" data-act="buyVip" id="vipBtn">${S.me.vip ? "续费" : "立即开通"} ¥78</button></div>`,
  { mount: () => (S.ui.vipPrice = 78) });
  acts.pickVip = (el) => { S.ui.vipPrice = +el.dataset.p; document.querySelectorAll(".vip-plan").forEach((p) => p.classList.toggle("on", p === el)); $("#vipBtn").textContent = `${S.me.vip ? "续费" : "立即开通"} ¥${el.dataset.p}`; };
  acts.buyVip = () => dialog({ title: "演示支付", text: `开通 VIP ¥${S.ui.vipPrice}。接入支付后会真正扣款。`, ok: "模拟支付成功", onOk: () => { S.me.vip = true; save(); toast("VIP 已开通"); render(); } });

  route(/^\/invite$/, () => `${pageHd("邀请好友")}<div class="invite-hero"><h2>邀请好友 得现金</h2><p>好友注册并完成首充，你得 50 金币 + 充值额 10% 返现</p><div class="code">${String(S.me.id).slice(-6)}</div></div>
    <div class="steps"><div><i>1</i>分享邀请链接</div><div><i>2</i>好友注册登录</div><div><i>3</i>好友首充</div><div><i>4</i>奖励到账</div></div>
    <div class="page-pad" style="display:flex;gap:12px"><button class="btn ghost" style="flex:1" data-act="copyCode">复制邀请码</button><button class="btn pink" style="flex:1" data-act="share">分享给好友</button></div>
    <div class="sec-title" style="padding-top:8px">我邀请的人</div>${empty("", "还没有邀请到好友", "快去分享吧")}`);
  acts.copyCode = () => navigator.clipboard?.writeText(String(S.me.id).slice(-6)).then(() => toast("邀请码已复制"), () => toast("邀请码：" + String(S.me.id).slice(-6)));

  // 视频认证 / 美颜：都需要摄像头
  function cameraInto(videoSel, phSel) {
    let stream;
    navigator.mediaDevices?.getUserMedia?.({ video: { facingMode: "user" } })
      .then((s) => { stream = s; const v = $(videoSel); if (v) v.srcObject = s; else s.getTracks().forEach((t) => t.stop()); $(phSel)?.classList.add("hidden"); })
      .catch(() => { const p = $(phSel); if (p) p.textContent = "未获得摄像头权限，请在浏览器设置中允许"; });
    onLeave(() => stream?.getTracks().forEach((t) => t.stop()));
  }
  route(/^\/verify$/, () => `${pageHd("视频认证")}<div class="beauty-preview" style="border-radius:0"><video id="vfVideo" autoplay muted playsinline></video><div class="ph" id="vfPh">正在打开摄像头…</div>
      <div style="position:absolute;inset:12% 22%;border:3px dashed rgba(255,255,255,.7);border-radius:50%"></div></div>
    <div class="page-pad" style="text-align:center"><b style="font-size:18px" id="vfStep">${S.verified ? "你已完成视频认证 ✓" : "请正对屏幕，保持光线充足"}</b>
      <p style="color:var(--muted);font-size:13px">认证后主页显示「已认证」标识，获得更多推荐。认证视频仅用于审核，不会公开。</p>
      ${S.verified ? "" : '<button class="btn primary block" data-act="doVerify" id="vfBtn">开始认证</button>'}</div>`,
  { mount: () => cameraInto("#vfVideo", "#vfPh") });
  acts.doVerify = () => {
    const steps = ["请眨眨眼", "请缓慢向左转头", "请缓慢向右转头", "正在提交审核…"];
    $("#vfBtn").disabled = true;
    steps.forEach((s, i) => setTimeout(() => { const el = $("#vfStep"); if (el) el.textContent = s; }, i * 1300));
    const t = setTimeout(() => { // API: 上传认证视频 -> 人工/自动审核
      S.verified = true; addLedger("视频认证奖励", 10); toast("认证成功 +10 金币"); render();
    }, steps.length * 1300);
    onLeave(() => clearTimeout(t));
  };

  route(/^\/beauty$/, () => {
    const b = S.beauty;
    const row = (k, t) => `<div class="slider-row"><label>${t}</label><input type="range" min="0" max="100" value="${b[k]}" data-k="${k}"><span id="bv_${k}">${b[k]}</span></div>`;
    return `${pageHd("美颜设置", `<button data-act="beautyReset">重置</button>`)}<div class="beauty-preview"><video id="bfVideo" autoplay muted playsinline></video><div class="ph" id="bfPh">正在打开摄像头…</div></div>
      <div style="padding:10px 0">${row("smooth", "磨皮")}${row("white", "美白")}${row("ruddy", "红润")}${row("slim", "瘦脸")}</div>
      <div class="page-pad" style="padding-top:0"><button class="btn primary block" data-act="beautySave">保存</button></div>`;
  }, {
    mount: () => {
      cameraInto("#bfVideo", "#bfPh");
      const apply = () => {
        const b = S.beauty;
        $("#bfVideo").style.filter = `blur(${b.smooth / 160}px) brightness(${1 + b.white / 400}) saturate(${1 + b.ruddy / 250}) contrast(${1 - b.smooth / 1000})`;
      };
      document.querySelectorAll("[data-k]").forEach((r) => (r.oninput = () => { S.beauty[r.dataset.k] = +r.value; $(`#bv_${r.dataset.k}`).textContent = r.value; apply(); }));
      apply();
    },
  });
  acts.beautyReset = () => { S.beauty = fresh().beauty; save(); render(); };
  acts.beautySave = () => { S.beautyOn = true; save(); toast("美颜参数已保存，视频通话时生效"); back("/me"); };

  const switchCell = (k, title, desc) => `<div class="cell" data-act="toggleSet" data-k="${k}"><div class="grow">${title}${desc ? `<small>${desc}</small>` : ""}</div><i class="switch ${S.settings[k] ? "on" : ""}"></i></div>`;
  acts.toggleSet = (el) => {
    const k = el.dataset.k;
    if (k === "stealth" && !S.me.vip) return dialog({ title: "VIP 特权", text: "隐身访问是 VIP 专属功能", ok: "去开通", onOk: () => go("/vip") });
    S.settings[k] = !S.settings[k]; save();
    el.querySelector(".switch").classList.toggle("on", S.settings[k]);
  };
  route(/^\/privacy$/, () => `${pageHd("隐私设置")}<div class="list">${switchCell("hideDistance", "隐藏我的距离", "别人看不到你和他的距离")}${switchCell("hideNearby", "不在「附近」中展示我")}${switchCell("stealth", "隐身访问", "访问别人主页不留下足迹（VIP）")}</div>
    <div class="group-gap"></div><div class="list"><a class="cell" data-act="blacklist"><div class="grow">黑名单</div>${ic("chev", "i chev")}</a></div>`);
  acts.blacklist = () => dialog({ title: "黑名单", text: "暂无拉黑的用户", cancel: "", ok: "好的" });

  route(/^\/settings$/, () => `${pageHd("其他设置")}<div class="list">${switchCell("notify", "新消息通知")}
      <a class="cell" data-act="clearCache"><div class="grow">清除缓存</div><span class="val">2.3MB</span>${ic("chev", "i chev")}</a>
      <a class="cell" data-act="doc" data-t="用户协议"><div class="grow">用户协议</div>${ic("chev", "i chev")}</a>
      <a class="cell" data-act="doc" data-t="隐私政策"><div class="grow">隐私政策</div>${ic("chev", "i chev")}</a>
      <a class="cell" data-act="doc" data-t="关于我们"><div class="grow">关于我们</div><span class="val">v0.1 设计预览</span>${ic("chev", "i chev")}</a></div>
    <div class="page-pad"><button class="btn ghost block" data-act="resetDemo">重置演示数据</button><div style="height:10px"></div><button class="btn block" style="background:#fff;color:var(--red);border:1px solid #ffd6dc" data-act="logout">退出登录</button></div>`);
  acts.clearCache = () => toast("缓存已清除");
  acts.doc = (el) => dialog({ title: el.dataset.t, text: "正式文本在上线前由法务撰写，此处为占位。", cancel: "", ok: "好的" });
  acts.resetDemo = () => dialog({ title: "重置演示数据", text: "清空本机保存的聊天、关注、余额等演示数据？", onOk: () => { S = fresh(); save(); toast("已重置"); go("/me"); } });
  acts.logout = () => dialog({ title: "退出登录", text: "确定要退出当前账号吗？", onOk: () => toast("登录功能接入后端后可用") });

  route(/^\/language$/, () => `${pageHd("语言设置")}<div class="list">${["简体中文", "繁體中文", "English"].map((l) => `<a class="cell" data-act="setLang" data-v="${l}"><div class="grow">${l}</div>${S.settings.lang === l ? `<span style="color:var(--c1)">${ic("check")}</span>` : ""}</a>`).join("")}</div>`);
  acts.setLang = (el) => { S.settings.lang = el.dataset.v; save(); render(); if (el.dataset.v !== "简体中文") toast("多语言版本开发中，当前仍显示简体中文"); };

  route(/^\/myposts$/, () => {
    const idx = S.posts.map((p, i) => i).filter((i) => S.posts[i].uid === "me");
    return `${pageHd("我的动态", `<a data-go="/publish">发布</a>`)}${idx.length ? idx.map((i) => postHtml(S.posts[i], i)).join("") : empty("", "你还没有发布过动态", "点右上角发布第一条吧")}`;
  });
  route(/^\/guard$/, () => `${pageHd("我的守护")}${empty("", "还没有守护任何人", "给喜欢的人送礼物累计到 1314 金币即可成为她的守护")}`);
  route(/^\/mygifts$/, () => {
    const sent = S.ledger.filter((l) => l.title.startsWith("送礼物"));
    return `${pageHd("我的礼物")}${sent.length ? `<div class="list">${sent.map((l) => `<div class="cell"><div class="grow">${esc(l.title.replace("送礼物·", "送出 "))}<small>${l.t}</small></div><span class="val">${l.amount} 金币</span></div>`).join("")}</div>` : empty("", "还没有送出或收到礼物")}`;
  });
  route(/^\/rates$/, () => `${pageHd("通话评价")}${S.ratings.length ? `<div class="list">${S.ratings.map((r) => { const u = U(r.uid); return `<div class="cell">${av(u, 46)}<div class="grow">${esc(u.name)} <span style="color:#ffb020">${"★".repeat(r.stars)}</span><small>${r.tags.join(" · ") || "无标签"} · ${r.t}</small></div></div>`; }).join("")}</div>` : empty("", "还没有通话评价", "视频通话结束后可以评价对方")}`);
  route(/^\/visitors$/, () => {
    const list = M.users.slice(4, 12);
    return `${pageHd("访问足迹")}<div class="list">${list.map((u, i) => {
      const hidden = !S.me.vip && i > 2;
      return `<a class="cell" ${hidden ? 'data-go="/vip"' : `data-go="/user/${u.id}"`}><div style="${hidden ? "filter:blur(5px)" : ""}">${av(u, 46)}</div><div class="grow">${hidden ? "开通 VIP 查看" : esc(u.name)}<small>${i + 1} 小时前看过你</small></div>${ic("chev", "i chev")}</a>`;
    }).join("")}</div>`;
  });
  route(/^\/games$/, () => `${pageHd("游戏技能")}${empty("", "还没有添加游戏技能", "添加后可以接陪玩订单")}<div class="page-pad"><button class="btn primary block" data-act="soon">添加技能</button></div>`);
  route(/^\/gameorders$/, () => `${pageHd("游戏订单")}${empty("", "暂无订单")}`);
  acts.soon = () => toast("该功能下一版上线");

  // ======================= 启动 =======================
  render();
})();
