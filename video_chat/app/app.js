// SeeU 视频交友 · 前端（已对接后端）
// 数据全部来自 /api 接口；实时消息、来电、通话信令走 WebSocket；音视频走 WebRTC。
(() => {
  const API = (window.SEEU_API || "").replace(/\/$/, "");
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
    voice: '<path d="M8 9a5 5 0 0 1 0 6M11.5 6.5a9 9 0 0 1 0 11M4.5 11.5h.01"/>',
    keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
    phoneIn: '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>',
    logo: '<path d="M3 7a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM17 10l4-2.5v9L17 14z" fill="currentColor" stroke="none"/>',
  };
  const ic = (name, cls = "i") => `<svg class="${cls}" viewBox="0 0 24 24">${P[name] || ""}</svg>`;

  // ======================= 基础工具 =======================
  const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch {} },
  };
  let TOKEN = store.get("seeu.token");
  const S = { me: null, counts: { following: 0, fans: 0 }, gifts: null, config: null, unreadConv: 0, unreadSys: 0 };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const media = (u) => (!u ? "" : u.startsWith("/uploads") ? API + u : u);
  const mmss = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  const bgOf = (u, v = 0) => (v === 0 && u.avatar ? `url('${media(u.avatar)}')` : u.photos?.[v - 1] ? `url('${media(u.photos[v - 1])}')` : window.ART(u.id, v));
  const av = (u, size, extra = "", attrs = "") =>
    `<div class="av" ${attrs} style="width:${size}px;height:${size}px;background-image:${bgOf(u)}">${extra}</div>`;
  const sexTag = (u) => `<span class="tag sex ${u.sex === "m" ? "m" : ""}">${u.sex === "m" ? "♂" : "♀"}${u.age}</span>`;
  const statusText = (u) => ({ online: "在线", busy: "通话中", offline: "离线" }[u.status] || "");
  const loading = '<div class="spinner"></div>';

  class ApiError extends Error {
    constructor(status, detail) {
      super(typeof detail === "string" ? detail : detail?.msg || "请求失败");
      this.status = status;
      this.code = typeof detail === "object" ? detail?.code : undefined;
      this.detail = detail;
    }
  }
  async function api(path, { method = "GET", body, form } = {}) {
    const headers = {};
    if (TOKEN) headers.Authorization = `Bearer ${TOKEN}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let res;
    try {
      res = await fetch(API + path, { method, headers, body: form || (body !== undefined ? JSON.stringify(body) : undefined) });
    } catch {
      throw new ApiError(0, "网络连接失败，请检查网络");
    }
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && !path.startsWith("/api/auth")) { logout(false); throw new ApiError(401, "请先登录"); }
    if (!res.ok) {
      const d = data.detail;
      throw new ApiError(res.status, Array.isArray(d) ? "填写的内容格式不对" : d || "请求失败");
    }
    return data;
  }
  function fail(e) {
    if (e?.code === "coins") return dialog({ title: "金币不足", text: e.message + "，充值后即可继续。", ok: "去充值", onOk: () => go("/recharge") });
    if (e?.code === "vip") return dialog({ title: "VIP 特权", text: e.message, ok: "去开通", onOk: () => go("/vip") });
    toast(e?.message || "出错了");
  }
  async function upload(kind, file, name) {
    const fd = new FormData();
    fd.append("kind", kind);
    fd.append("file", file, name || file.name);
    return (await api("/api/upload", { method: "POST", form: fd })).url;
  }
  // 压缩图片后上传
  function shrink(file, max) {
    return new Promise((res, rej) => {
      const img = new Image();
      img.onload = () => {
        const k = Math.min(1, max / Math.max(img.width, img.height));
        const c = document.createElement("canvas");
        c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(img.src);
        c.toBlob((b) => (b ? res(b) : rej(new Error("图片处理失败"))), "image/jpeg", 0.85);
      };
      img.onerror = () => rej(new ApiError(0, "无法读取这张图片"));
      img.src = URL.createObjectURL(file);
    });
  }
  const uploadImage = async (file, max = 1280) => upload("image", await shrink(file, max), "img.jpg");

  // ======================= 提示 / 弹层 =======================
  let toastT;
  function toast(t) {
    const el = $("#toast");
    el.textContent = t; el.classList.remove("hidden");
    clearTimeout(toastT); toastT = setTimeout(() => el.classList.add("hidden"), 2000);
  }
  const layer = $("#layer");
  const closeLayer = () => { layer.innerHTML = ""; };
  function openSheet(html, { dark = false } = {}) {
    layer.innerHTML = `<div class="mask"><div class="sheet ${dark ? "dark" : ""}">${html}</div></div>`;
    const mask = layer.firstChild;
    mask.addEventListener("click", (e) => { if (e.target === mask) closeLayer(); });
    return mask.firstChild;
  }
  function dialog({ title, text, ok = "确定", cancel = "取消", onOk, onCancel }) {
    layer.innerHTML = `<div class="mask center"><div class="dialog"><h3>${title}</h3><p>${text}</p>
      <div class="btns">${cancel ? `<button class="btn ghost" data-x="c">${cancel}</button>` : ""}<button class="btn primary" data-x="o">${ok}</button></div></div></div>`;
    layer.querySelector('[data-x="o"]').onclick = () => { closeLayer(); onOk?.(); };
    layer.querySelector('[data-x="c"]')?.addEventListener("click", () => { closeLayer(); onCancel?.(); });
  }
  function actionSheet(items) {
    const sh = openSheet(`<div class="action-list">${items.map((it, i) =>
      `<button data-i="${i}" class="${it.danger ? "danger" : ""}">${it.text}</button>`).join("")}<button class="cancel">取消</button></div>`);
    sh.querySelectorAll("[data-i]").forEach((b) => (b.onclick = () => { closeLayer(); items[+b.dataset.i].fn?.(); }));
    sh.querySelector(".cancel").onclick = closeLayer;
  }
  function reportSheet(type, id) {
    actionSheet(["色情低俗", "诈骗/引导转账", "广告骚扰", "辱骂攻击", "其他"].map((r) => ({
      text: r, fn: () => api("/api/reports", { method: "POST", body: { type, id, reason: r } }).then(() => toast("举报已提交，我们会尽快处理"), fail),
    })));
  }
  function userMenu(u, extra = []) {
    actionSheet([
      ...extra,
      { text: "举报", fn: () => reportSheet("user", u.id) },
      { text: u.blocked ? "取消拉黑" : "拉黑", danger: !u.blocked, fn: () => (u.blocked ? doBlock(u) : dialog({ title: "拉黑", text: `拉黑后将不再收到 ${esc(u.name)} 的消息和来电`, onOk: () => doBlock(u) })) },
    ]);
  }
  async function doBlock(u) {
    try { const r = await api(`/api/users/${u.id}/block`, { method: "POST" }); toast(r.blocked ? "已拉黑" : "已取消拉黑"); render(); } catch (e) { fail(e); }
  }

  async function loadGifts() { return (S.gifts ||= await api("/api/gifts")); }
  async function giftSheet(u, { dark = true, callId = null, onSent } = {}) {
    const gifts = await loadGifts().catch((e) => fail(e));
    if (!gifts) return;
    let sel = gifts[0];
    const sh = openSheet(`<h3>送礼物给 ${esc(u.name)}</h3>
      <div class="gifts">${gifts.map((g, i) => `<button data-g="${i}" class="${i === 0 ? "on" : ""}"><span class="g">${g.icon}</span><b>${g.name}</b>${g.price}金币</button>`).join("")}</div>
      <div class="gift-foot"><span class="coin">余额 <span class="coinsNow">${S.me.coins}</span> 金币<a id="gTop">充值 ›</a></span><button class="btn pink" id="gSend">赠送</button></div>`, { dark });
    sh.querySelectorAll("[data-g]").forEach((b) => (b.onclick = () => {
      sh.querySelectorAll("[data-g]").forEach((x) => x.classList.remove("on"));
      b.classList.add("on"); sel = gifts[+b.dataset.g];
    }));
    sh.querySelector("#gTop").onclick = () => { closeLayer(); go("/recharge"); };
    sh.querySelector("#gSend").onclick = async () => {
      try {
        const r = await api("/api/gifts/send", { method: "POST", body: { to: u.id, giftId: sel.id, callId } });
        setCoins(r.coins); closeLayer(); onSent?.(sel, r.message);
      } catch (e) { closeLayer(); fail(e); }
    };
  }
  function setCoins(n) {
    if (!S.me) return;
    S.me.coins = n;
    document.querySelectorAll(".coinsNow").forEach((el) => (el.textContent = n));
  }

  // ======================= 路由 =======================
  let cleanups = [];
  const onLeave = (fn) => cleanups.push(fn);
  const go = (path) => { if (location.hash === "#" + path) render(); else location.hash = "#" + path; };
  const back = (fallback = "/discover") => (history.length > 1 ? history.back() : go(fallback));
  const routes = [];
  const route = (re, fn, opts = {}) => routes.push({ re, fn, ...opts });
  let renderSeq = 0;

  async function render() {
    const seq = ++renderSeq;
    cleanups.forEach((f) => { try { f(); } catch {} });
    cleanups = [];
    closeLayer();
    const path = location.hash.slice(1) || "/discover";
    const r = routes.find((x) => x.re.test(path));
    if (!r) return go("/discover");
    if (!r.public && !TOKEN) return go("/login");
    if (!r.public && !S.me) {
      try { await boot(); } catch (e) {
        if (seq === renderSeq && e.status !== 401) view.innerHTML = `<div class="empty"><b>${esc(e.message)}</b><p><button class="btn primary" data-act="reload">重试</button></p></div>`;
        return;
      }
    }
    const args = path.match(r.re).slice(1);
    appEl.classList.toggle("no-tab", !r.tab);
    appEl.classList.remove("dark-tab");
    document.querySelectorAll("#tabbar a").forEach((a) => a.classList.toggle("on", a.dataset.tab === r.tab));
    const slow = setTimeout(() => { if (seq === renderSeq) view.innerHTML = loading; }, 200);
    let html;
    try { html = await r.fn(...args); } catch (e) {
      clearTimeout(slow);
      if (seq !== renderSeq || e.status === 401) return;
      view.innerHTML = `<div class="page-hd"><button class="back" data-act="back">${ic("back")}</button><h2></h2><div class="right"></div></div><div class="empty"><b>${esc(e.message)}</b><p><button class="btn primary" data-act="reload">重试</button></p></div>`;
      return;
    }
    clearTimeout(slow);
    if (seq !== renderSeq) return;
    view.scrollTop = 0;
    view.innerHTML = html || "";
    try { await r.mount?.(...args); } catch (e) { fail(e); }
    updateBadge();
  }
  addEventListener("hashchange", render);

  const acts = {};
  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-act]");
    if (a && document.body.contains(a)) { e.preventDefault(); acts[a.dataset.act]?.(a, e); return; }
    const g = e.target.closest("[data-go]");
    if (g) { e.preventDefault(); go(g.dataset.go); }
  });
  acts.back = () => back();
  acts.reload = () => render();

  const TAB_ICON = { discover: "discover", community: "community", messages: "messages", me: "me" };
  document.querySelectorAll("#tabbar a").forEach((a) => {
    a.querySelector(".ti").innerHTML = ic(TAB_ICON[a.dataset.tab]);
    a.onclick = () => go("/" + a.dataset.tab);
  });
  function updateBadge() {
    const n = S.unreadConv + S.unreadSys;
    const b = $("#msgBadge");
    b.textContent = n > 99 ? "99+" : n;
    b.classList.toggle("hidden", !n);
  }
  async function refreshUnread() {
    if (!TOKEN) return;
    try {
      const [convs, nu] = await Promise.all([api("/api/conversations"), api("/api/notices-unread")]);
      S.unreadConv = convs.reduce((s, c) => s + c.unread, 0);
      S.unreadSys = nu.system + nu.service;
      updateBadge();
    } catch {}
  }

  const pageHd = (title, right = "") =>
    `<header class="page-hd"><button class="back" data-act="back">${ic("back")}</button><h2>${title}</h2><div class="right">${right}</div></header>`;
  const emptyIll = `<svg class="ill" viewBox="0 0 200 150"><ellipse cx="100" cy="138" rx="80" ry="8" fill="#f1ede8"/>
    <path d="M40 70h120v60H40z" fill="#f3dccb"/><path d="M40 70l20-18h120l-20 18z" fill="#f8e8dc"/><path d="M40 70l-14 16h120l14-16z" fill="#eacdb8"/>
    <ellipse cx="78" cy="56" rx="30" ry="34" fill="#fff" stroke="#e7e2dc" stroke-width="2"/><circle cx="68" cy="52" r="5" fill="none" stroke="#c9bfb6" stroke-width="2"/><circle cx="88" cy="52" r="5" fill="none" stroke="#c9bfb6" stroke-width="2"/>
    <path d="M74 64q4 4 8 0" stroke="#f08aa0" stroke-width="2" fill="none"/><rect x="128" y="18" width="50" height="32" rx="16" fill="#fff" stroke="#e7e2dc" stroke-width="2"/>
    <circle cx="143" cy="34" r="3" fill="#d6cec6"/><circle cx="153" cy="34" r="3" fill="#d6cec6"/><circle cx="163" cy="34" r="3" fill="#d6cec6"/></svg>`;
  const empty = (title, ...lines) => `<div class="empty">${title ? `<b>${title}</b>` : ""}${emptyIll}${lines.map((l) => `<p>${l}</p>`).join("")}</div>`;

  // ======================= 登录 =======================
  async function boot() {
    const [me, counts, config] = await Promise.all([api("/api/me"), api("/api/me/counts"), api("/api/config")]);
    S.me = me; S.counts = counts; S.config = config;
    connectWS();
    refreshUnread();
  }
  function logout(confirmFirst = true) {
    const doIt = () => {
      TOKEN = null; S.me = null; store.set("seeu.token", null);
      try { ws?.close(); } catch {}
      go("/login");
    };
    confirmFirst ? dialog({ title: "退出登录", text: "确定要退出当前账号吗？", onOk: doIt }) : doIt();
  }

  route(/^\/login$/, () => {
    const invite = new URLSearchParams(location.search).get("invite") || "";
    return `<div class="login"><div class="logo">${ic("logo")}</div><h1>欢迎来到 SeeU</h1><p class="sub">真实认证 · 视频交友</p>
      <form id="loginForm">
        <div class="field"><span>+86</span><input id="lPhone" type="tel" maxlength="11" placeholder="请输入手机号" autocomplete="tel"></div>
        <div class="field"><input id="lCode" inputmode="numeric" maxlength="6" placeholder="验证码" autocomplete="one-time-code"><button type="button" id="lSend">获取验证码</button></div>
        <div class="field"><input id="lInvite" maxlength="6" placeholder="邀请码（选填）" value="${esc(invite)}"></div>
        <label class="agree-row"><input type="checkbox" id="lAgree">我已阅读并同意《用户协议》《隐私政策》，并确认已年满 18 周岁</label>
        <button class="btn primary block" id="lGo">登录 / 注册</button>
      </form></div>`;
  }, {
    public: true,
    mount: () => {
      appEl.classList.add("no-tab");
      let timer;
      onLeave(() => clearInterval(timer));
      $("#lSend").onclick = async () => {
        const phone = $("#lPhone").value.trim();
        if (!/^1\d{10}$/.test(phone)) return toast("请输入正确的手机号");
        try {
          const r = await api("/api/auth/sms", { method: "POST", body: { phone } });
          if (r.devCode) { $("#lCode").value = r.devCode; toast(`开发模式验证码：${r.devCode}`); } else toast("验证码已发送");
          let n = 60;
          const btn = $("#lSend");
          btn.disabled = true;
          timer = setInterval(() => { btn.textContent = `${--n}s 后重发`; if (n <= 0) { clearInterval(timer); btn.disabled = false; btn.textContent = "获取验证码"; } }, 1000);
        } catch (e) { fail(e); }
      };
      $("#loginForm").onsubmit = async (e) => {
        e.preventDefault();
        if (!$("#lAgree").checked) return toast("请先阅读并同意用户协议");
        try {
          const r = await api("/api/auth/login", { method: "POST", body: { phone: $("#lPhone").value.trim(), code: $("#lCode").value.trim(), invite: $("#lInvite").value.trim() } });
          TOKEN = r.token; store.set("seeu.token", TOKEN); S.me = null;
          go("/discover");
        } catch (err) { fail(err); }
      };
    },
  });

  // ======================= 实时通道 =======================
  let ws, wsRetry = 1000, pingT;
  function connectWS() {
    if (!TOKEN || (ws && ws.readyState <= 1)) return;
    const base = API ? API.replace(/^http/, "ws") : location.origin.replace(/^http/, "ws");
    ws = new WebSocket(`${base}/api/ws?token=${encodeURIComponent(TOKEN)}`);
    ws.onopen = () => { wsRetry = 1000; clearInterval(pingT); pingT = setInterval(() => wsSend({ type: "ping" }), 25000); };
    ws.onmessage = (e) => { try { onWs(JSON.parse(e.data)); } catch (err) { console.error(err); } };
    ws.onclose = (e) => {
      clearInterval(pingT);
      if (e.code === 4401 || e.code === 4403) return logout(false);
      if (TOKEN) setTimeout(connectWS, (wsRetry = Math.min(wsRetry * 2, 30000)));
    };
  }
  const wsSend = (m) => { if (ws?.readyState === 1) ws.send(JSON.stringify(m)); };
  document.addEventListener("visibilitychange", () => { if (!document.hidden) connectWS(); });

  const chatHooks = new Set();   // 打开的聊天页监听新消息
  function onWs(m) {
    switch (m.type) {
      case "message": {
        const mine = m.message.from === S.me?.id;
        let handled = false;
        chatHooks.forEach((h) => { if (h(m.message)) handled = true; });
        if (!mine && !handled) {
          S.unreadConv++; updateBadge();
          if (/^\/messages(\/list)?$/.test(location.hash.slice(1))) render();
          else if (m.message.kind !== "call") toast(`${m.from?.name || "新消息"}：${preview(m.message)}`);
        }
        break;
      }
      case "call_invite": return onInvite(m);
      case "call_accepted": return rtcAccepted(m.call);
      case "call_ended": return rtcEnded(m);
      case "signal": return rtcSignal(m);
      case "call_low_balance": return toast("余额只够 1 分钟了，请及时充值");
      case "balance": return setCoins(m.coins);
      case "notice": return toast(m.text);
    }
  }
  const preview = (msg) => ({ text: msg.content, image: "[图片]", voice: "[语音]", gift: `[礼物] ${msg.content}`, call: `[${msg.content}]` }[msg.kind] || "");

  // ======================= 发现 =======================
  const DISC_TABS = [["recommend", "推荐"], ["square", "广场"], ["active", "活跃"], ["nearby", "附近"]];
  const CHIPS = {
    recommend: [["all", "所有", "#ff6fa3", "▦"], ["new", "新人", "#5b8cff", "新"], ["city", "同城", "#36c6f4", "城"], ["close", "亲密度", "#7a8cff", "♥"]],
    square: [["all", "所有", "#ff6fa3", "▦"], ["verified", "认证", "#36c6f4", "✓"], ["new", "新人", "#5b8cff", "新"], ["city", "同城", "#36c6f4", "城"]],
    active: [["all", "所有", "#ff6fa3", "▦"], ["online", "在线", "#2ecc71", "●"], ["new", "新人", "#5b8cff", "新"]],
    nearby: [["all", "所有", "#ff6fa3", "▦"]],
  };
  const ui = { chip: {}, filter: { age: "不限", price: "不限", online: false, sameCity: false }, feedVerified: false };
  function cardHtml(u) {
    const badge = u.verified ? "verified" : u.isNew ? "newbie" : "";
    const label = { verified: `${ic("play", "i")}已认证`, newbie: "新人" }[badge];
    const price = u.status === "busy" ? `<span class="price busy" data-act="call" data-uid="${u.id}">${ic("videoFill")}通话中</span>`
      : u.status === "offline" ? `<span class="price off" data-act="call" data-uid="${u.id}">${ic("videoFill")}离线</span>`
      : `<span class="price" data-act="call" data-uid="${u.id}">${ic("videoFill")}${u.price}金币</span>`;
    return `<a class="card" data-go="/user/${u.id}" style="background:${bgOf(u, u.photos?.length ? 1 : 0)} center/cover">
      ${badge ? `<span class="badge-tl ${badge}">${label.replace('class="i"', 'class="i" style="width:12px;height:12px"')}</span>` : ""}
      ${u.status === "online" ? '<i class="online-dot"></i>' : ""}<div class="shade"></div>
      <div class="nm">${esc(u.name)}</div><div class="meta"><span class="rate">★${u.rating.toFixed(1)}</span>${esc(u.city)}</div>${price}</a>`;
  }
  let bannersCache;
  const bannerHtml = (bs) => `<div class="banner" id="banner"><div class="slides">${bs.map((b) =>
    `<div class="slide" data-go="${b.go}" style="background:${b.bg}"><h3>${esc(b.title)}</h3><p>${esc(b.sub)}</p><span class="date">${esc(b.tag)}</span></div>`).join("")}</div>
    <span class="tagline">视频速配交友</span><div class="dots">${bs.map((_, i) => `<i class="${i ? "" : "on"}"></i>`).join("")}</div></div>`;
  function startBanner() {
    const b = $("#banner");
    if (!b) return;
    const n = b.querySelectorAll(".slide").length;
    let i = 0;
    const t = setInterval(() => {
      i = (i + 1) % n;
      b.querySelector(".slides").style.transform = `translateX(-${i * 100}%)`;
      b.querySelectorAll(".dots i").forEach((d, k) => d.classList.toggle("on", k === i));
    }, 3500);
    onLeave(() => clearInterval(t));
  }

  route(/^\/discover(?:\/(\w+))?$/, async (tab = "recommend") => {
    const chip = ui.chip[tab] || "all";
    const f = ui.filter;
    const qs = new URLSearchParams({ tab, chip, age: f.age === "不限" ? "" : f.age, price: f.price === "不限" ? "" : f.price, online: f.online, sameCity: f.sameCity });
    const [list, banners] = await Promise.all([api(`/api/hosts?${qs}`), bannersCache || api("/api/banners")]);
    bannersCache = banners;
    const cards = list.map(cardHtml);
    cards.splice(Math.min(4, cards.length), 0, bannerHtml(banners));
    return `<div class="top"><div class="top-row"><nav class="big-tabs">${DISC_TABS.map(([k, t]) =>
      `<a data-go="/discover/${k}" class="${k === tab ? "on" : ""}">${t}</a>`).join("")}</nav>
      <a class="top-act crown" data-go="/rank">${ic("crown")}排行</a><a class="top-act" data-act="filter">${ic("filter")}筛选</a></div></div>
      <div class="chips">${CHIPS[tab].map(([k, t, c, s]) => `<button class="chip ${k === chip ? "on" : ""}" data-act="chip" data-tab="${tab}" data-k="${k}"><span class="ci" style="background:${c}">${s}</span>${t}</button>`).join("")}</div>
      <div class="cards">${cards.join("")}${list.length ? "" : `<div style="grid-column:1/-1">${empty("", tab === "nearby" ? "同城暂时没有人" : "暂时没有符合条件的人", "换个筛选条件试试")}</div>`}</div>`;
  }, { tab: "discover", mount: () => { startBanner(); const t = setInterval(() => !layer.innerHTML && render(), 60000); onLeave(() => clearInterval(t)); } });

  acts.chip = (el) => { ui.chip[el.dataset.tab] = el.dataset.k; render(); };
  acts.filter = () => {
    const f = { ...ui.filter };
    const opt = (key, vals) => `<div class="opts">${vals.map((v) => `<button data-k="${key}" data-v="${v}" class="${f[key] === v ? "on" : ""}">${v}</button>`).join("")}</div>`;
    const sh = openSheet(`<h3>筛选</h3>
      <div class="s-row"><label>年龄</label>${opt("age", ["不限", "18-22", "23-27", "28以上"])}</div>
      <div class="s-row"><label>通话价格（金币/分钟）</label>${opt("price", ["不限", "30以下", "30-60", "60以上"])}</div>
      <div class="s-row"><label>其他</label><div class="opts"><button data-b="online" class="${f.online ? "on" : ""}">只看在线</button><button data-b="sameCity" class="${f.sameCity ? "on" : ""}">只看同城</button></div></div>
      <div class="btns"><button class="btn ghost" id="fReset">重置</button><button class="btn primary" id="fOk">确定</button></div>`);
    sh.querySelectorAll("[data-k]").forEach((b) => (b.onclick = () => {
      f[b.dataset.k] = b.dataset.v;
      sh.querySelectorAll(`[data-k="${b.dataset.k}"]`).forEach((x) => x.classList.toggle("on", x === b));
    }));
    sh.querySelectorAll("[data-b]").forEach((b) => (b.onclick = () => { f[b.dataset.b] = !f[b.dataset.b]; b.classList.toggle("on"); }));
    sh.querySelector("#fReset").onclick = () => { ui.filter = { age: "不限", price: "不限", online: false, sameCity: false }; closeLayer(); render(); };
    sh.querySelector("#fOk").onclick = () => { ui.filter = f; closeLayer(); render(); };
  };

  route(/^\/rank(?:\/(\w+))?(?:\/(\w+))?$/, async (kind = "charm", period = "day") => {
    const list = await api(`/api/rank?kind=${kind}&period=${period}`);
    const label = kind === "charm" ? "魅力值" : "贡献值";
    const top = [list[1], list[0], list[2]];
    return `${pageHd("排行榜")}
      <div class="rank-hero"><div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">
        <div class="seg"><button data-go="/rank/charm/${period}" class="${kind === "charm" ? "on" : ""}">魅力榜</button><button data-go="/rank/rich/${period}" class="${kind === "rich" ? "on" : ""}">富豪榜</button></div>
        <div class="seg">${[["day", "日榜"], ["week", "周榜"], ["month", "月榜"]].map(([k, t]) => `<button data-go="/rank/${kind}/${k}" class="${k === period ? "on" : ""}">${t}</button>`).join("")}</div></div>
        <div class="podium">${top.map((u, k) => { const rank = [2, 1, 3][k]; return u ? `<div class="p${rank}" data-go="/user/${u.id}"><span class="crown">${["🥇", "🥈", "🥉"][rank - 1]}</span>${av(u, rank === 1 ? 82 : 64)}<b>${esc(u.name)}</b><span>${label} ${u.score}</span></div>` : `<div class="p${rank}"></div>`; }).join("")}</div></div>
      <div class="rank-list list">${list.length > 3 ? list.slice(3).map((u, i) => `<a class="cell" data-go="/user/${u.id}"><span class="rank-no">${i + 4}</span>${av(u, 46)}<div class="grow">${esc(u.name)}<small>${esc(u.city)}</small></div><span class="val">${label} ${u.score}</span></a>`).join("")
        : list.length ? "" : empty("", "榜单还在统计中", "送礼物和视频通话都会计入榜单")}</div>`;
  });

  // ======================= 用户主页 =======================
  let curUser = null;
  route(/^\/user\/(\d+)$/, async (id) => {
    const u = (curUser = await api(`/api/users/${id}`));
    if (u.id === S.me.id) { setTimeout(() => go("/me"), 0); return ""; }
    const dot = { online: "", busy: "busy", offline: "off" }[u.status];
    const nPhotos = 1 + (u.photos?.length || 0);
    return `<header class="page-hd clear"><button class="back" data-act="back">${ic("back")}</button><h2></h2><div class="right"><button data-act="userMore" style="color:#fff">${ic("more")}</button></div></header>
      <div class="u-hero" id="hero" data-photo="0" style="background-image:${bgOf(u)}"><span class="live"><i class="${dot}"></i>${statusText(u)}</span><span class="pager" id="pager">1/${nPhotos}</span></div>
      <div class="u-body">
        <div class="u-name">${esc(u.name)} ${sexTag(u)} ${u.verified ? '<span class="tag ver">已认证</span>' : ""}${u.vip ? '<span class="tag vip">VIP</span>' : ""}</div>
        <div class="u-sub"><span>${ic("location", "i")} ${esc(u.city || "未知")}${u.sameCity && !u.hideDistance ? " · 同城" : ""}</span><span>ID: ${u.id}</span></div>
        <div class="u-stats"><div><b>${u.fans}</b><span>粉丝</span></div><div><b>${u.answerRate}%</b><span>接通率</span></div><div><b>${u.rating.toFixed(1)}</b><span>评分</span></div><div><b>${u.isHost ? u.price : "-"}</b><span>金币/分钟</span></div></div>
        <div class="u-sec"><h4>个性签名</h4><div class="u-sign">${esc(u.sign || "这个人很懒，什么都没写")}</div></div>
        ${u.labels?.length ? `<div class="u-sec"><h4>标签</h4><div class="labels">${u.labels.map((l) => `<span>${esc(l)}</span>`).join("")}</div></div>` : ""}
        <div class="u-sec"><h4>${u.sex === "m" ? "他" : "她"}的动态</h4>${u.thumbs.length ? `<div class="thumbs">${u.thumbs.map((t) => `<div data-act="viewImg" data-src="${esc(media(t))}" style="background-image:url('${esc(media(t))}')"></div>`).join("")}</div>` : '<p style="color:var(--muted);margin:0">还没有发布动态</p>'}</div>
        <div class="u-sec"><h4>收到的礼物</h4>${u.gifts.length ? `<div class="giftwall">${u.gifts.map((g) => `<div><div class="g">${g.icon}</div>${g.name} ×${g.count}</div>`).join("")}</div>` : '<p style="color:var(--muted);margin:0">还没有收到礼物</p>'}</div>
      </div>
      <div class="bottom-bar"><button class="mini ${u.followed ? "on" : ""}" data-act="follow" data-uid="${u.id}">${ic("heart")}${u.followed ? "已关注" : "关注"}</button>
        <button class="btn ghost" data-go="/chat/${u.id}">${ic("comment")}私信</button>
        ${u.isHost ? `<button class="btn ghost" style="flex:.7" data-act="call" data-media="voice" data-uid="${u.id}">${ic("phone")}语音</button>` : ""}
        <button class="btn primary" data-act="call" data-uid="${u.id}">${ic("video")}${u.status === "busy" ? "通话中" : u.isHost ? `视频 ${u.price}币/分` : "视频通话"}</button></div>`;
  }, {
    mount: () => {
      const u = curUser; const hero = $("#hero");
      if (!hero) return;
      const n = 1 + (u.photos?.length || 0);
      hero.onclick = () => {
        const k = (+hero.dataset.photo + 1) % n;
        hero.dataset.photo = k;
        hero.style.backgroundImage = bgOf(u, k);
        $("#pager").textContent = `${k + 1}/${n}`;
      };
    },
  });
  acts.userMore = () => userMenu(curUser);
  acts.viewImg = (el) => {
    layer.innerHTML = `<div class="mask center" style="background:rgba(0,0,0,.92)"><img src="${el.dataset.src}" style="max-width:100%;max-height:100%"></div>`;
    layer.firstChild.onclick = closeLayer;
  };
  acts.follow = async (el) => {
    try {
      const r = await api(`/api/users/${el.dataset.uid}/follow`, { method: "POST" });
      S.counts.following += r.followed ? 1 : -1;
      toast(r.followed ? "关注成功" : "已取消关注");
      if (el.closest(".call")) { el.textContent = r.followed ? "已关注" : "+关注"; return; }
      render();
    } catch (e) { fail(e); }
  };

  // ======================= 动态 / 小视频 =======================
  const COMM_TABS = [["feed", "动态"], ["reels", "小视频"], ["city", "同城"], ["follow", "关注"]];
  let postsCache = [];
  function postHtml(p) {
    const u = p.user;
    const pics = p.images.length === 1 ? `<div class="pic" data-act="viewImg" data-src="${esc(media(p.images[0]))}" style="background-image:url('${esc(media(p.images[0]))}')"></div>`
      : p.images.length ? `<div class="pics">${p.images.map((s) => `<div data-act="viewImg" data-src="${esc(media(s))}" style="background-image:url('${esc(media(s))}')"></div>`).join("")}</div>` : "";
    return `<article class="post"><div class="post-hd"><a data-go="${p.mine ? "/me" : `/user/${u.id}`}">${av(u, 48)}</a>
      <div class="who"><b>${esc(u.name)}</b>${sexTag(u)}${u.verified ? '<span class="tag ver">已认证</span>' : ""}${u.vip ? '<span class="tag vip">VIP</span>' : ""}</div>
      <button class="more" data-act="postMore" data-id="${p.id}">${ic("more")}</button></div>
      ${p.text ? `<div class="txt">${esc(p.text)}</div>` : ""}${pics}
      <div class="time">${p.time}${p.location ? ` · ${esc(p.location)}` : ""}</div>
      <div class="acts"><button data-act="like" data-id="${p.id}" class="${p.liked ? "liked" : ""}">${ic("heart")}<span>${p.likes}</span></button>
      <button data-act="comments" data-id="${p.id}">${ic("comment")}<span>${p.comments || "评论"}</span></button></div></article>`;
  }
  route(/^\/community(?:\/(\w+))?$/, async (tab = "feed") => {
    const head = (dark) => `<div class="top ${dark ? "reels-top" : ""}"><div class="top-row"><nav class="big-tabs">${COMM_TABS.map(([k, t]) =>
      `<a data-go="/community/${k}" class="${k === tab ? "on" : ""}">${t}</a>`).join("")}</nav>
      ${dark ? `<button class="pub-btn" data-go="/publish/reel">+发布</button>` : `<a class="top-act" data-act="feedFilter">${ic("filter")}筛选</a>`}</div></div>`;
    if (tab === "reels") {
      const reels = (postsCache = await api("/api/posts?kind=reel"));
      if (!reels.length) return `<div class="reels" style="display:grid;place-items:center;color:#aaa">还没有小视频，点右上角发布第一条</div>${head(true)}`;
      return `<div class="reels">${reels.map((r) => `<section class="reel"><video src="${esc(media(r.video))}" loop muted playsinline preload="metadata"></video>
          <div class="info"><b>@${esc(r.user.name)}</b><p>${esc(r.text)}</p></div>
          <div class="side"><a data-go="/user/${r.user.id}" style="position:relative">${av(r.user, 56).replace('class="av"', 'class="av rav"')}</a>
            <button data-act="like" data-id="${r.id}" class="${r.liked ? "liked" : ""}">${ic("heart")}<span>${r.likes}</span></button>
            <button data-act="comments" data-id="${r.id}">${ic("comment")}<span>${r.comments}</span></button>
            <button data-act="share">${ic("share")}分享</button></div>
          ${r.mine ? "" : `<button class="dm" data-go="/chat/${r.user.id}">私信聊天<i>${ic("comment")}</i></button>`}</section>`).join("")}</div>${head(true)}`;
    }
    const list = (postsCache = await api(`/api/posts?tab=${tab}&verified=${ui.feedVerified}`));
    return `${head(false)}${list.length ? list.map(postHtml).join("") : empty(tab === "follow" ? "关注的人还没有发布动态" : "还没有动态", "点右下角发布第一条吧")}
      <div style="height:90px"></div><button class="fab" data-go="/publish/post">${ic("plane")}发布</button>`;
  }, {
    tab: "community",
    mount: (tab = "feed") => {
      if (tab !== "reels") return;
      appEl.classList.add("dark-tab");
      const io = new IntersectionObserver((es) => es.forEach((e) => { const v = e.target.querySelector("video"); if (!v) return; e.isIntersecting ? v.play().catch(() => {}) : v.pause(); }), { threshold: 0.6 });
      document.querySelectorAll(".reel").forEach((r) => { io.observe(r); r.querySelector("video")?.addEventListener("click", (ev) => { const v = ev.target; v.muted = !v.muted; toast(v.muted ? "已静音" : "已打开声音"); }); });
      onLeave(() => io.disconnect());
    },
  });
  acts.like = async (el) => {
    try {
      const r = await api(`/api/posts/${el.dataset.id}/like`, { method: "POST" });
      el.classList.toggle("liked", r.liked); el.querySelector("span").textContent = r.likes;
    } catch (e) { fail(e); }
  };
  acts.share = async () => {
    try { await api("/api/tasks/share", { method: "POST" }); } catch {}
    const link = `${location.origin}${location.pathname}?invite=${S.me.inviteCode}`;
    if (navigator.share) return navigator.share({ title: "SeeU 视频交友", text: "来 SeeU 一起视频聊天", url: link }).catch(() => {});
    navigator.clipboard?.writeText(link).then(() => toast("邀请链接已复制，快去分享吧"), () => toast(link));
  };
  acts.feedFilter = () => actionSheet([
    { text: `${ui.feedVerified ? "" : "✓ "}全部动态`, fn: () => { ui.feedVerified = false; render(); } },
    { text: `${ui.feedVerified ? "✓ " : ""}只看已认证`, fn: () => { ui.feedVerified = true; render(); } },
  ]);
  acts.postMore = (el) => {
    const p = postsCache.find((x) => x.id === +el.dataset.id);
    if (!p) return;
    if (p.mine) return actionSheet([{ text: "删除", danger: true, fn: () => dialog({ title: "删除", text: "删除后无法恢复", onOk: () => api(`/api/posts/${p.id}`, { method: "DELETE" }).then(render, fail) }) }]);
    actionSheet([{ text: "举报这条内容", fn: () => reportSheet("post", p.id) }, { text: "不感兴趣", fn: () => toast("将减少此类推荐") }, { text: `拉黑 ${esc(p.user.name)}`, danger: true, fn: () => doBlock(p.user) }]);
  };
  acts.comments = async (el) => {
    const id = el.dataset.id;
    let list;
    try { list = await api(`/api/posts/${id}/comments`); } catch (e) { return fail(e); }
    const draw = () => list.length ? list.map((c) => `<div style="padding:10px 0;border-bottom:1px solid var(--line)"><b style="font-weight:500;color:var(--text-2)">${esc(c.user.name)}</b>：${esc(c.text)} <small style="color:var(--muted)">${c.time}</small></div>`).join("")
      : '<p style="text-align:center;color:var(--muted);padding:20px 0">还没有评论，快来抢沙发</p>';
    const sh = openSheet(`<h3>评论 ${list.length || ""}</h3><div id="cList" style="max-height:40vh;overflow:auto">${draw()}</div>
      <form id="cForm" style="display:flex;gap:8px;margin-top:12px"><input id="cIn" maxlength="200" placeholder="说点什么…" style="flex:1;height:40px;border:0;border-radius:20px;background:var(--bg-2);padding:0 14px;outline:none"><button class="btn primary" style="height:40px">发送</button></form>`);
    sh.querySelector("#cForm").onsubmit = async (e) => {
      e.preventDefault();
      const t = sh.querySelector("#cIn").value.trim();
      if (!t) return;
      try {
        list = await api(`/api/posts/${id}/comments`, { method: "POST", body: { text: t } });
        sh.querySelector("#cIn").value = "";
        sh.querySelector("#cList").innerHTML = draw();
        sh.querySelector("h3").textContent = `评论 ${list.length}`;
        el.querySelector("span").textContent = list.length;
      } catch (err) { fail(err); }
    };
  };

  // 发布动态 / 小视频
  let draft = { images: [], video: "" };
  route(/^\/publish\/(post|reel)$/, (kind) => {
    draft = { images: [], video: "", kind };
    return `${pageHd(kind === "reel" ? "发布小视频" : "发布动态", `<button class="btn primary" style="height:32px;font-size:14px;padding:0 16px" data-act="doPublish" id="pubBtn">发布</button>`)}
      <div class="publish-box"><textarea id="pubText" maxlength="500" placeholder="${kind === "reel" ? "说说这个视频…" : "分享你的生活，认识更多有趣的人…"}"></textarea>
      <div class="pub-imgs" id="pubImgs"></div><input type="file" id="pubFile" accept="${kind === "reel" ? "video/*" : "image/*"}" ${kind === "reel" ? "" : "multiple"} hidden></div>
      <div class="group-gap"></div>
      <div class="list"><a class="cell" data-act="pubLoc">${ic("location")}<div class="grow">所在位置</div><span class="val" id="pubLocV">${esc(S.me.city || "不显示位置")}</span>${ic("chev", "i chev")}</a>
      <a class="cell" data-act="pubWho">${ic("lock")}<div class="grow">谁可以看</div><span class="val" id="pubWhoV" data-v="all">所有人</span>${ic("chev", "i chev")}</a></div>`;
  }, { mount: () => drawPub() });
  function drawPub() {
    const box = $("#pubImgs");
    if (!box) return;
    if (draft.kind === "reel") {
      box.innerHTML = draft.video ? `<div style="grid-column:1/-1;aspect-ratio:auto;background:#000;border-radius:10px"><video src="${esc(media(draft.video))}" controls playsinline style="width:100%;max-height:300px;display:block"></video></div>`
        : `<label class="add" for="pubFile">${ic("video")}</label>`;
    } else {
      box.innerHTML = draft.images.map((src, i) => `<div style="background-image:url('${esc(media(src))}')"><span class="del" data-act="pubDel" data-i="${i}">×</span></div>`).join("")
        + (draft.images.length < 9 ? `<label class="add" for="pubFile">${ic("plus")}</label>` : "");
    }
    $("#pubFile").onchange = async (e) => {
      const files = [...e.target.files];
      e.target.value = "";
      toast("上传中…");
      try {
        if (draft.kind === "reel") draft.video = await upload("video", files[0]);
        else for (const f of files.slice(0, 9 - draft.images.length)) draft.images.push(await uploadImage(f));
        drawPub(); toast("上传完成");
      } catch (err) { fail(err); }
    };
  }
  acts.pubDel = (el) => { draft.images.splice(+el.dataset.i, 1); drawPub(); };
  acts.pubLoc = () => actionSheet([S.me.city, "不显示位置"].filter(Boolean).map((t) => ({ text: t, fn: () => ($("#pubLocV").textContent = t) })));
  acts.pubWho = () => actionSheet([["all", "所有人"], ["fans", "仅关注我的人"], ["self", "仅自己"]].map(([v, t]) => ({ text: t, fn: () => { const el = $("#pubWhoV"); el.textContent = t; el.dataset.v = v; } })));
  acts.doPublish = async () => {
    const loc = $("#pubLocV").textContent;
    const body = { kind: draft.kind, text: $("#pubText").value.trim(), images: draft.images, video: draft.video,
      location: loc === "不显示位置" ? "" : loc, visibility: $("#pubWhoV").dataset.v };
    $("#pubBtn").disabled = true;
    try { await api("/api/posts", { method: "POST", body }); toast("发布成功"); go(draft.kind === "reel" ? "/community/reels" : "/community/feed"); }
    catch (e) { fail(e); $("#pubBtn").disabled = false; }
  };

  // ======================= 消息 =======================
  route(/^\/messages(?:\/(\w+))?$/, async (tab = "list") => {
    const head = `<div class="top"><div class="top-row"><nav class="big-tabs"><a data-go="/messages/list" class="${tab === "list" ? "on" : ""}">消息</a><a data-go="/messages/calls" class="${tab === "calls" ? "on" : ""}">通话记录</a></nav>
      ${tab === "list" ? `<a class="top-act" data-act="clearUnread">${ic("clear")}清空</a>` : ""}</div></div>`;
    if (tab === "calls") {
      const calls = await api("/api/calls");
      const st = (c) => c.status === "ended" ? `${c.media === "video" ? "视频" : "语音"}通话 ${mmss(c.seconds)}` : { rejected: "已拒绝", canceled: "已取消", missed: c.outgoing ? "对方未接听" : "未接来电" }[c.status] || "通话中";
      return head + (calls.length ? `<div class="list">${calls.map((c) => `<div class="cell"><a data-go="/user/${c.peer.id}">${av(c.peer, 50)}</a><div class="grow">${esc(c.peer.name)}<small style="color:${c.status === "ended" ? "var(--muted)" : "var(--red)"}">${c.outgoing ? "↗" : "↙"} ${st(c)} · ${c.time}</small></div><button data-act="call" data-uid="${c.peer.id}" data-media="${c.media}" style="color:var(--c2)">${ic(c.media === "video" ? "video" : "phone")}</button></div>`).join("")}</div>`
        : `<p style="text-align:center;color:var(--text-2);font-weight:600;margin:24px 0 0">已经全部加载完毕</p>${empty("", "暂时没有找到通话记录", "去拨打视频试试吧")}`);
    }
    const [convs, online, nu] = await Promise.all([api("/api/conversations"), api("/api/online-users"), api("/api/notices-unread")]);
    S.unreadConv = convs.reduce((s, c) => s + c.unread, 0); S.unreadSys = nu.system + nu.service;
    return `${head}
      <div class="notice-banner" data-go="/antifraud">防范电信诈骗宣传手册</div>
      ${online.length ? `<div class="online-row">${online.map((u) => `<a data-go="/user/${u.id}">${av(u, 64, '<i class="on-dot"></i>')}${esc(u.name)}</a>`).join("")}</div>` : ""}
      <div class="conv-list">
        <a class="conv pinned" data-go="/service"><div class="av sys-av" style="width:58px;height:58px;background:linear-gradient(135deg,#ffb3cf,#c9a5ff)">${ic("comment")}</div><div class="body"><div class="l1"><b>SeeU 客服</b><span class="tag official">官方</span></div><div class="l2"><span>有问题随时找我</span>${nu.service ? `<i class="dot-badge">${nu.service}</i>` : ""}</div></div></a>
        <a class="conv pinned" data-go="/system"><div class="av sys-av" style="width:58px;height:58px">${ic("bellFill")}</div><div class="body"><div class="l1"><b>系统消息</b></div><div class="l2"><span>充值到账、审核结果等通知</span>${nu.system ? `<i class="dot-badge">${nu.system}</i>` : ""}</div></div></a>
        ${convs.map((c) => `<a class="conv" data-go="/chat/${c.user.id}">${av(c.user, 58, c.user.status === "online" ? '<i class="on-dot"></i>' : "")}<div class="body"><div class="l1"><b>${esc(c.user.name)}</b><time>${c.last.time}</time></div><div class="l2"><span>${c.last.from === S.me.id ? "我：" : ""}${esc(preview(c.last))}</span>${c.unread ? `<i class="dot-badge">${c.unread}</i>` : ""}</div></div></a>`).join("")}
      </div>${convs.length ? "" : '<p style="text-align:center;color:var(--muted);padding:30px 0">还没有聊天，去发现页打个招呼吧</p>'}`;
  }, { tab: "messages" });
  acts.clearUnread = () => dialog({ title: "清空未读", text: "将所有消息标记为已读？", onOk: () => api("/api/conversations/read-all", { method: "POST" }).then(render, fail) });

  route(/^\/antifraud$/, () => `${pageHd("防范电信诈骗")}<div class="page-pad" style="line-height:1.8">
    <div class="balance-card" style="margin:0 0 16px;background:linear-gradient(135deg,#c9a5ff,#ff9ac0)"><b style="font-size:22px">守护你的钱包安全</b><small>平台不会以任何理由要求你私下转账</small></div>
    ${["凡是要求「私下转账」「刷单返利」「投资带你赚钱」的，一律是诈骗。", "不要点击聊天中陌生人发来的链接，不要下载陌生 App。", "对方以「见面」「垫付车费」「生病急用钱」为由借钱，请立即停止并举报。",
       "不要向任何人透露验证码、银行卡号、支付密码。", "遇到可疑情况，在对方主页右上角「…」举报，或拨打 96110 反诈专线。"].map((t, i) =>
       `<div style="display:flex;gap:10px;margin-bottom:12px"><span style="flex:none;width:24px;height:24px;border-radius:50%;background:var(--grad);color:#fff;display:grid;place-items:center;font-size:13px">${i + 1}</span><span>${t}</span></div>`).join("")}</div>`);

  route(/^\/system$/, async () => {
    const list = await api("/api/notices/system");
    return `${pageHd("系统消息")}<div class="bg-gray page-pad">${list.length ? list.slice().reverse().map((x) => `<div style="background:#fff;border-radius:12px;padding:14px;margin-bottom:10px"><small style="color:var(--muted)">${x.time}</small><div style="margin-top:6px">${esc(x.text)}</div></div>`).join("") : empty("", "暂无系统消息")}</div>`;
  }, { mount: refreshUnread });

  const svcBubble = (m) => m.me ? `<div class="m me">${av(S.me, 40)}<div class="bub">${esc(m.text)}</div></div>`
    : `<div class="m"><div class="av sys-av" style="width:40px;height:40px;background:linear-gradient(135deg,#ffb3cf,#c9a5ff)">${ic("comment")}</div><div class="bub">${esc(m.text)}</div></div>`;
  route(/^\/service$/, async () => {
    const list = await api("/api/notices/service");
    return `<div class="chat-wrap">${pageHd("SeeU 客服")}<div class="chat-list" id="svcList">
      <div class="m"><div class="av sys-av" style="width:40px;height:40px;background:linear-gradient(135deg,#ffb3cf,#c9a5ff)">${ic("comment")}</div><div class="bub">你好，我是 SeeU 客服，有什么可以帮你？</div></div>
      ${list.map(svcBubble).join("")}
      <div class="opts" style="padding-left:48px">${["怎么充值", "通话怎么收费", "如何提现", "怎么举报"].map((q) => `<button data-act="faq" data-q="${q}">${q}</button>`).join("")}</div></div>
      <form class="chat-bar" id="svcForm"><div class="row1"><input id="svcIn" maxlength="300" placeholder="描述你遇到的问题…"><button class="send">发送</button></div></form></div>`;
  }, {
    mount: () => {
      refreshUnread();
      $("#svcList").scrollTop = 1e6;
      $("#svcForm").onsubmit = (e) => { e.preventDefault(); askService($("#svcIn").value.trim()); };
    },
  });
  async function askService(text) {
    if (!text) return;
    try { await api("/api/notices/service", { method: "POST", body: { text } }); render(); } catch (e) { fail(e); }
  }
  acts.faq = (el) => askService(el.dataset.q);

  // ======================= 聊天 =======================
  let chatPeer = null;
  function msgHtml(m, peer) {
    const mine = m.from === S.me.id;
    const who = mine ? S.me : peer;
    let body;
    if (m.kind === "image") body = `<img class="img" src="${esc(media(m.content))}" alt="" data-act="viewImg" data-src="${esc(media(m.content))}">`;
    else if (m.kind === "voice") body = `<div class="bub voice-bub" data-act="playVoice" data-src="${esc(media(m.content))}">${ic("voice")}<span>${m.extra.duration || 1}″</span><span style="display:inline-block;width:${Math.min(120, 10 + (m.extra.duration || 1) * 4)}px"></span></div>`;
    else if (m.kind === "gift") body = `<div class="gift-bub"><span class="g">${m.extra.gift?.icon || "🎁"}</span>${mine ? "送出" : "收到"} ${esc(m.content)}</div>`;
    else if (m.kind === "call") body = `<div class="bub call-bub">${ic(m.extra.media === "voice" ? "phone" : "video", "i")}${esc(m.content)}</div>`;
    else body = `<div class="bub">${esc(m.content)}</div>`;
    return `<div class="m ${mine ? "me" : ""}" data-mid="${m.id}">${av(who, 40, "", mine ? "" : `data-go="/user/${who.id}"`)}${body}</div>`;
  }
  route(/^\/chat\/(\d+)$/, async (id) => {
    const [peer, msgs] = await Promise.all([api(`/api/users/${id}`), api(`/api/conversations/${id}/messages`)]);
    chatPeer = peer;
    refreshUnread();
    return `<div class="chat-wrap">${pageHd(`${esc(peer.name)}<small style="display:block;font-size:11px;color:${peer.status === "online" ? "var(--green)" : "var(--muted)"};font-weight:400">${statusText(peer)}</small>`,
      `<button data-act="chatMore">${ic("more")}</button>`)}
      <div class="chat-list" id="chatList"><div class="chat-tip">平台倡导文明交友，请勿私下转账、点击陌生链接，谨防诈骗</div>${msgs.map((m) => msgHtml(m, peer)).join("")}</div>
      <form class="chat-bar" id="chatForm"><div class="row1"><button type="button" class="mode-btn" id="modeBtn" title="语音/文字">${ic("voice")}</button>
        <input id="chatIn" maxlength="500" placeholder="说点什么…" autocomplete="off"><button type="button" class="hold-btn hidden" id="holdBtn">按住 说话</button><button class="send" id="sendBtn">发送</button></div>
        <div class="row2"><label for="chatImg" style="display:flex;flex-direction:column;align-items:center;font-size:11px;gap:2px;cursor:pointer">${ic("image")}图片</label><input type="file" id="chatImg" accept="image/*" hidden>
        <button type="button" data-act="chatGift">${ic("gift")}礼物</button>
        <button type="button" data-act="call" data-media="voice" data-uid="${peer.id}">${ic("phone")}语音通话</button>
        <button type="button" class="vc" data-act="call" data-uid="${peer.id}">${ic("video")}视频通话</button></div></form></div>`;
  }, {
    mount: () => {
      const peer = chatPeer;
      const list = $("#chatList");
      const add = (m) => {
        if (list.querySelector(`[data-mid="${m.id}"]`)) return;
        list.insertAdjacentHTML("beforeend", msgHtml(m, peer));
        list.scrollTop = list.scrollHeight;
      };
      list.scrollTop = list.scrollHeight;
      const hook = (m) => {
        const inThis = (m.from === peer.id && m.to === S.me.id) || (m.from === S.me.id && m.to === peer.id);
        if (!inThis) return false;
        add(m);
        if (m.from === peer.id) api(`/api/conversations/${peer.id}/messages?limit=1`).catch(() => {}); // 标记已读
        return true;
      };
      chatHooks.add(hook);
      onLeave(() => chatHooks.delete(hook));
      const send = async (body) => { try { add(await api("/api/messages", { method: "POST", body: { to: peer.id, ...body } })); } catch (e) { fail(e); } };
      $("#chatForm").onsubmit = (e) => {
        e.preventDefault();
        const t = $("#chatIn").value.trim();
        if (!t) return;
        $("#chatIn").value = "";
        send({ kind: "text", content: t });
      };
      $("#chatImg").onchange = async (e) => {
        const f = e.target.files[0]; e.target.value = "";
        if (!f) return;
        try { send({ kind: "image", content: await uploadImage(f, 1080) }); } catch (err) { fail(err); }
      };
      // 文字 / 语音切换
      let voiceMode = false;
      $("#modeBtn").onclick = () => {
        voiceMode = !voiceMode;
        $("#modeBtn").innerHTML = ic(voiceMode ? "keyboard" : "voice");
        $("#chatIn").classList.toggle("hidden", voiceMode);
        $("#sendBtn").classList.toggle("hidden", voiceMode);
        $("#holdBtn").classList.toggle("hidden", !voiceMode);
      };
      setupHoldToTalk($("#holdBtn"), (blob, ext, secs) => upload("voice", blob, `voice.${ext}`).then((url) => send({ kind: "voice", content: url, duration: secs }), fail));
      acts.chatGift = () => giftSheet(peer, { dark: false, onSent: (g, m) => add(m) });
      acts.chatMore = () => userMenu(peer, [
        { text: "查看资料", fn: () => go(`/user/${peer.id}`) },
        { text: "清空聊天记录", fn: () => dialog({ title: "清空聊天记录", text: "只清空你这边的记录，对方不受影响", onOk: () => api(`/api/conversations/${peer.id}`, { method: "DELETE" }).then(render, fail) }) },
      ]);
    },
  });

  // 语音消息：按住录音，上滑取消，最长 60 秒
  function pickAudioType() {
    const types = [["audio/mp4", "m4a"], ["audio/webm;codecs=opus", "webm"], ["audio/webm", "webm"], ["audio/ogg;codecs=opus", "ogg"]];
    return types.find(([t]) => window.MediaRecorder?.isTypeSupported?.(t)) || ["", "webm"];
  }
  function setupHoldToTalk(btn, onDone) {
    let rec, stream, chunks, start, startY, cancel, hint, maxT;
    const finish = () => {
      clearTimeout(maxT);
      btn.classList.remove("rec"); btn.textContent = "按住 说话";
      hint?.remove(); hint = null;
      if (rec && rec.state !== "inactive") rec.stop();
    };
    btn.addEventListener("pointerdown", async (e) => {
      e.preventDefault();
      if (!window.MediaRecorder) return toast("当前环境不支持录音");
      startY = e.clientY; cancel = false;
      try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch { return toast("没有麦克风权限，请在设置里允许"); }
      const [type, ext] = pickAudioType();
      chunks = [];
      rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
      rec.ondataavailable = (ev) => ev.data.size && chunks.push(ev.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const secs = Math.round((Date.now() - start) / 1000);
        if (cancel) return toast("已取消发送");
        if (secs < 1) return toast("说话时间太短");
        onDone(new Blob(chunks, { type: rec.mimeType || type }), ext, Math.min(60, secs));
      };
      rec.start();
      start = Date.now();
      btn.classList.add("rec"); btn.textContent = "松开 发送";
      hint = document.createElement("div"); hint.className = "rec-hint"; hint.innerHTML = "<b>🎙</b>手指上滑，取消发送";
      document.body.appendChild(hint);
      maxT = setTimeout(finish, 60000);
      btn.setPointerCapture?.(e.pointerId);
    });
    btn.addEventListener("pointermove", (e) => {
      if (!hint) return;
      cancel = startY - e.clientY > 60;
      hint.classList.toggle("cancel", cancel);
      hint.innerHTML = cancel ? "<b>↩</b>松开手指，取消发送" : "<b>🎙</b>手指上滑，取消发送";
    });
    ["pointerup", "pointercancel"].forEach((ev) => btn.addEventListener(ev, finish));
  }
  let playing;
  acts.playVoice = (el) => {
    if (playing) { playing.audio.pause(); playing.el.classList.remove("playing"); if (playing.el === el) { playing = null; return; } }
    const audio = new Audio(el.dataset.src);
    playing = { audio, el };
    el.classList.add("playing");
    audio.onended = audio.onerror = () => { el.classList.remove("playing"); if (playing?.el === el) playing = null; };
    audio.play().catch(() => { el.classList.remove("playing"); toast("这条语音无法播放"); });
  };

  // ======================= 视频 / 语音通话 =======================
  let RTC = null;
  acts.call = (el) => startCall(+el.dataset.uid, el.dataset.media || "video");
  async function startCall(uid, mediaKind) {
    if (RTC) return toast("你正在通话中");
    let r;
    try { r = await api("/api/calls", { method: "POST", body: { to: uid, media: mediaKind } }); }
    catch (e) {
      if (e.code === "offline") return dialog({ title: "对方不在线", text: "可以先给对方留言，上线后会看到", ok: "去留言", onOk: () => go(`/chat/${uid}`) });
      return fail(e);
    }
    RTC = newRtc(r.call, r.peer, true);
    RTC.mediaReady = getLocalMedia(r.call.media);
    ringtone.start("out");
    go("/call");
  }
  function newRtc(call, peer, outgoing) {
    return { call, peer, outgoing, state: outgoing ? "ringing" : "incoming", pc: null, queue: [], pending: [], local: null, secs: 0, timer: null, mic: true, cam: true, facing: "user" };
  }
  function onInvite(m) {
    if (RTC) return;   // 服务器已保证不会同时来两通
    RTC = newRtc(m.call, m.from, false);
    ringtone.start("in");
    if (navigator.vibrate) navigator.vibrate([400, 200, 400]);
    go("/call");
  }
  async function getLocalMedia(kind) {
    try {
      return await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: kind === "video" ? { facingMode: "user", width: { ideal: 720 }, height: { ideal: 1280 } } : false,
      });
    } catch {
      try { return await navigator.mediaDevices.getUserMedia({ audio: true }); } catch { toast("没有获得摄像头/麦克风权限，对方将看不到/听不到你"); return null; }
    }
  }

  route(/^\/call$/, () => {
    if (!RTC) { setTimeout(() => back("/messages"), 0); return ""; }
    const { peer, call, outgoing } = RTC;
    const video = call.media === "video";
    const iPay = call.payerId === S.me.id;
    const beauty = S.me.settings.beautyOn;
    const centerText = outgoing ? "正在等待对方接听…" : `邀请你${video ? "视频" : "语音"}通话`;
    return `<div class="call ${video ? "ringing" : ""}" id="call">
      <div class="remote" style="background-image:${bgOf(peer)}"></div>
      <video class="remote-v hidden" id="rv" autoplay playsinline></video><audio id="ra" autoplay></audio>
      <div class="self ${beauty ? "beauty" : ""} ${video ? "" : "hidden"}" id="selfBox"><video id="sv" autoplay muted playsinline></video><div class="off hidden" id="selfOff">摄像头已关闭</div></div>
      <div class="hdr hidden" id="callHdr">${av(peer, 40)}<div class="who"><b>${esc(peer.name)}</b><span id="callTime">00:00</span></div>
        <button class="fl" data-act="follow" data-uid="${peer.id}">+关注</button></div>
      <div class="bill hidden" id="bill">${call.payerId ? (iPay ? `${call.price} 金币/分钟<br>余额 <b class="coinsNow">${S.me.coins}</b> 金币` : `对方付费 ${call.price} 金币/分钟<br>你获得分成收益`) : "免费通话"}</div>
      <div class="net hidden" id="netTip">网络不稳定，正在重连…</div>
      <div class="center" id="ringBox">${av(peer, 100)}<b>${esc(peer.name)}</b><span id="ringText">${centerText}</span>
        ${!outgoing && iPay ? `<div class="price-tip">接听后由你支付 ${call.price} 金币/分钟</div>` : ""}</div>
      ${outgoing ? "" : `<div class="incoming-ctrl" id="inCtrl"><button class="no" data-act="hangup"><span class="cb">${ic("phone")}</span>拒绝</button><button class="yes" data-act="acceptCall"><span class="cb">${ic(video ? "video" : "phone")}</span>接听</button></div>`}
      <div class="ctrl ${outgoing ? "" : "hidden"}" id="ctrl">
        <button data-act="callMic"><span class="cb" id="cMic">${ic("mic")}</span><span>静音</span></button>
        ${video ? `<button data-act="callCam"><span class="cb" id="cCam">${ic("video")}</span><span>摄像头</span></button>` : ""}
        <button class="hang" data-act="hangup"><span class="cb">${ic("phone")}</span><span>挂断</span></button>
        ${video ? `<button data-act="callFlip"><span class="cb">${ic("flip")}</span><span>翻转</span></button>` : `<button data-act="callSpeaker"><span class="cb active" id="cSpk">${ic("voice")}</span><span>免提</span></button>`}
        <button class="gift" data-act="callGift"><span class="cb">${ic("gift")}</span><span>礼物</span></button>
      </div></div>`;
  }, {
    mount: async () => {
      if (!RTC) return;
      const rtc = RTC;
      onLeave(() => { if (RTC === rtc && !rtc.ended) hangup(); });
      if (rtc.state === "active") return showActive(rtc);
      if (rtc.outgoing) attachLocal(rtc, await rtc.mediaReady);
    },
  });
  function attachLocal(rtc, stream) {
    rtc.local = stream;
    const sv = $("#sv");
    if (sv && stream) { sv.srcObject = stream; sv.style.filter = beautyFilter(); }
    if (!stream?.getVideoTracks().length) $("#selfOff")?.classList.remove("hidden");
  }
  function beautyFilter() {
    if (!S.me.settings.beautyOn) return "";
    const b = S.me.settings.beauty;
    return `blur(${b.smooth / 160}px) brightness(${1 + b.white / 400}) saturate(${1 + b.ruddy / 250}) contrast(${1 - b.smooth / 1000})`;
  }
  acts.acceptCall = async () => {
    const rtc = RTC;
    if (!rtc || rtc.state !== "incoming") return;
    rtc.state = "accepting";
    ringtone.stop();
    $("#ringText").textContent = "正在接通…";
    rtc.mediaReady = getLocalMedia(rtc.call.media);
    attachLocal(rtc, await rtc.mediaReady);
    try { await api(`/api/calls/${rtc.call.id}/accept`, { method: "POST" }); }
    catch (e) { fail(e); rtc.state = "incoming"; return hangup(); }
    await ensurePc(rtc);
  };
  async function rtcAccepted(call) {
    const rtc = RTC;
    if (!rtc || rtc.call.id !== call.id) return;
    ringtone.stop();
    rtc.call = call;
    rtc.state = "active";
    showActive(rtc);
    await ensurePc(rtc);
    if (rtc.outgoing) {
      await rtc.pc.setLocalDescription(await rtc.pc.createOffer());
      wsSend({ type: "signal", callId: call.id, data: { sdp: rtc.pc.localDescription } });
    }
  }
  async function ensurePc(rtc) {
    if (rtc.pcReady) return rtc.pcReady;
    rtc.pcReady = (async () => {
      const stream = (rtc.local ||= await rtc.mediaReady);
      const pc = new RTCPeerConnection({ iceServers: S.config.iceServers });
      stream?.getTracks().forEach((t) => pc.addTrack(t, stream));
      // 没有摄像头/麦克风也要能收到对方的画面和声音
      if (rtc.call.media === "video" && !stream?.getVideoTracks().length) pc.addTransceiver("video", { direction: "recvonly" });
      if (!stream?.getAudioTracks().length) pc.addTransceiver("audio", { direction: "recvonly" });
      pc.onicecandidate = (e) => e.candidate && wsSend({ type: "signal", callId: rtc.call.id, data: { candidate: e.candidate } });
      pc.ontrack = (e) => {
        const s = e.streams[0] || new MediaStream([e.track]);
        if (e.track.kind === "video") { const rv = $("#rv"); if (rv) { rv.srcObject = s; rv.classList.remove("hidden"); } }
        else { const ra = $("#ra"); if (ra && ra.srcObject !== s) ra.srcObject = s; }
      };
      pc.onconnectionstatechange = () => {
        const bad = ["disconnected", "failed"].includes(pc.connectionState);
        $("#netTip")?.classList.toggle("hidden", !bad);
      };
      rtc.pc = pc;
      for (const d of rtc.queue.splice(0)) await handleSignal(rtc, d);
      return pc;
    })();
    return rtc.pcReady;
  }
  async function rtcSignal(m) {
    const rtc = RTC;
    if (!rtc || rtc.call.id !== m.callId) return;
    if (!rtc.pc) return rtc.queue.push(m.data);
    await handleSignal(rtc, m.data);
  }
  async function handleSignal(rtc, d) {
    const pc = rtc.pc;
    try {
      if (d.sdp) {
        await pc.setRemoteDescription(d.sdp);
        if (d.sdp.type === "offer") {
          await pc.setLocalDescription(await pc.createAnswer());
          wsSend({ type: "signal", callId: rtc.call.id, data: { sdp: pc.localDescription } });
        }
        for (const c of rtc.pending.splice(0)) await pc.addIceCandidate(c).catch(() => {});
      } else if (d.candidate) {
        if (pc.remoteDescription) await pc.addIceCandidate(d.candidate).catch(() => {});
        else rtc.pending.push(d.candidate);
      }
    } catch (e) { console.error("signal", e); }
  }
  function showActive(rtc) {
    if (!$("#call")) return;
    $("#call").classList.remove("ringing");
    $("#inCtrl")?.remove();
    $("#ctrl").classList.remove("hidden");
    $("#callHdr").classList.remove("hidden");
    $("#bill").classList.remove("hidden");
    if (rtc.call.media === "video") $("#ringBox").classList.add("hidden");
    else $("#ringText").textContent = "通话中";
    clearInterval(rtc.timer);
    rtc.timer = setInterval(() => {
      rtc.secs++;
      const t = $("#callTime"); if (t) t.textContent = mmss(rtc.secs);
      if (rtc.call.media !== "video") { const r = $("#ringText"); if (r) r.textContent = mmss(rtc.secs); }
    }, 1000);
  }
  function cleanupRtc(rtc) {
    ringtone.stop();
    clearInterval(rtc.timer);
    rtc.local?.getTracks().forEach((t) => t.stop());
    try { rtc.pc?.close(); } catch {}
  }
  async function hangup() {
    const rtc = RTC;
    if (!rtc || rtc.ended) return;
    const action = rtc.state === "active" ? "end" : rtc.outgoing ? "cancel" : "reject";
    try { await api(`/api/calls/${rtc.call.id}/${action}`, { method: "POST" }); } catch {}
    // 正常会收到 call_ended 推送；网络断了也要本地结束
    setTimeout(() => { if (RTC === rtc && !rtc.ended) rtcEnded({ call: { ...rtc.call, status: rtc.state === "active" ? "ended" : "canceled", seconds: rtc.secs }, reason: action === "end" ? "hangup" : action, by: S.me.id }); }, 3000);
  }
  acts.hangup = hangup;
  function rtcEnded(m) {
    const rtc = RTC;
    if (!rtc || rtc.call.id !== m.call.id || rtc.ended) return;
    rtc.ended = true;
    cleanupRtc(rtc);
    RTC = null;
    const byMe = m.by === S.me.id;
    const tip = { no_coins: "余额不足，通话已结束", missed: rtc.outgoing ? "对方无人接听" : "未接来电", reject: byMe ? "已拒绝" : "对方已拒绝",
      cancel: byMe ? "已取消" : "对方已取消", hangup: byMe ? "通话结束" : "对方已挂断", disconnect: "网络断开，通话已结束" }[m.reason] || "通话结束";
    toast(tip);
    const onCallPage = location.hash === "#/call";
    if (m.call.status === "ended") {
      if (onCallPage) view.innerHTML = "";
      return rateSheet(m.call, rtc.peer);
    }
    if (m.reason === "no_coins" && m.call.payerId === S.me.id) return dialog({ title: "金币不足", text: "充值后可以继续通话", ok: "去充值", onOk: () => go("/recharge"), onCancel: () => go(`/chat/${rtc.peer.id}`) });
    if (onCallPage) setTimeout(() => go(`/chat/${rtc.peer.id}`), 800);
  }
  function rateSheet(call, peer) {
    let stars = 5;
    const tags = ["颜值高", "声音好听", "聊得来", "很热情", "有礼貌", "网络卡顿"];
    const picked = new Set();
    const sh = openSheet(`<h3>通话结束 · ${mmss(call.seconds || 0)}${call.payerId === S.me.id ? ` · 消费 ${call.cost || 0} 金币` : ""}</h3>
      <div style="text-align:center;margin-bottom:6px">${av(peer, 64).replace('class="av"', 'class="av" style="margin:0 auto 8px"')}<b>${esc(peer.name)}</b></div>
      <div style="text-align:center;font-size:34px;color:#ffb020;letter-spacing:6px;cursor:pointer" id="stars">${"★".repeat(5)}</div>
      <div class="opts" style="justify-content:center;margin:14px 0 18px">${tags.map((t) => `<button data-t="${t}">${t}</button>`).join("")}</div>
      <div class="btns"><button class="btn ghost" id="rSkip">跳过</button><button class="btn primary" id="rOk">提交评价</button></div>`);
    const starsEl = sh.querySelector("#stars");
    starsEl.onclick = (e) => {
      const r = starsEl.getBoundingClientRect();
      stars = Math.max(1, Math.min(5, Math.ceil(((e.clientX - r.left) / r.width) * 5)));
      starsEl.textContent = "★".repeat(stars) + "☆".repeat(5 - stars);
    };
    sh.querySelectorAll("[data-t]").forEach((b) => (b.onclick = () => { b.classList.toggle("on"); picked.has(b.dataset.t) ? picked.delete(b.dataset.t) : picked.add(b.dataset.t); }));
    const done = () => { closeLayer(); go(`/chat/${peer.id}`); };
    sh.querySelector("#rSkip").onclick = done;
    sh.querySelector("#rOk").onclick = async () => {
      try { await api(`/api/calls/${call.id}/rating`, { method: "POST", body: { stars, tags: [...picked] } }); toast("感谢评价"); } catch (e) { fail(e); }
      done();
    };
  }
  acts.callMic = () => {
    if (!RTC) return;
    RTC.mic = !RTC.mic;
    RTC.local?.getAudioTracks().forEach((t) => (t.enabled = RTC.mic));
    $("#cMic").classList.toggle("active", !RTC.mic);
    $("#cMic").innerHTML = ic(RTC.mic ? "mic" : "micOff");
    toast(RTC.mic ? "麦克风已打开" : "已静音");
  };
  acts.callCam = () => {
    if (!RTC) return;
    RTC.cam = !RTC.cam;
    RTC.local?.getVideoTracks().forEach((t) => (t.enabled = RTC.cam));
    $("#cCam").classList.toggle("active", !RTC.cam);
    $("#cCam").innerHTML = ic(RTC.cam ? "video" : "camOff");
    $("#selfOff").classList.toggle("hidden", RTC.cam);
  };
  acts.callFlip = async () => {
    const rtc = RTC;
    if (!rtc?.local?.getVideoTracks().length) return toast("没有可用的摄像头");
    rtc.facing = rtc.facing === "user" ? "environment" : "user";
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: rtc.facing } });
      const [nt] = s.getVideoTracks();
      const [old] = rtc.local.getVideoTracks();
      await rtc.pc?.getSenders().find((x) => x.track?.kind === "video")?.replaceTrack(nt);
      rtc.local.removeTrack(old); old.stop(); rtc.local.addTrack(nt);
      $("#sv").srcObject = rtc.local;
      $("#sv").style.transform = rtc.facing === "user" ? "scaleX(-1)" : "none";
    } catch { toast("切换摄像头失败"); }
  };
  acts.callSpeaker = () => {
    const ra = $("#ra");
    // 网页里无法切换听筒/扬声器；App 版通过原生插件实现。这里先用音量近似
    ra.volume = ra.volume === 1 ? 0.4 : 1;
    $("#cSpk").classList.toggle("active", ra.volume === 1);
    toast(ra.volume === 1 ? "免提已打开" : "免提已关闭");
  };
  acts.callGift = () => RTC && giftSheet(RTC.peer, {
    callId: RTC.call.id,
    onSent: (g) => {
      const el = document.createElement("div");
      el.className = "float-gift";
      el.innerHTML = `<div class="g">${g.icon}</div><p>送出 ${g.name}</p>`;
      $("#call")?.appendChild(el);
      setTimeout(() => el.remove(), 2000);
    },
  });

  // 铃声（WebAudio 合成，无需音频文件）
  const ringtone = (() => {
    let ctx, t;
    const beep = (f, d) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = f; g.gain.value = 0.08;
      o.connect(g); g.connect(ctx.destination);
      o.start(); o.stop(ctx.currentTime + d);
    };
    return {
      start(kind) {
        try { ctx ||= new (window.AudioContext || window.webkitAudioContext)(); ctx.resume?.(); } catch { return; }
        clearInterval(t);
        const play = () => (kind === "in" ? (beep(880, 0.25), setTimeout(() => beep(660, 0.25), 300)) : beep(440, 0.9));
        play(); t = setInterval(play, kind === "in" ? 1500 : 3000);
      },
      stop() { clearInterval(t); },
    };
  })();

  // ======================= 我的 =======================
  route(/^\/me$/, async () => {
    [S.me, S.counts] = await Promise.all([api("/api/me"), api("/api/me/counts")]);
    const me = S.me;
    const cell = (go, icon, text) => `<a data-go="${go}">${ic(icon)}<span>${text}</span></a>`;
    return `<div class="me-hd"><a data-go="/edit">${av(me, 84)}</a><div><div class="nm">${esc(me.name)}${me.vip ? '<span class="tag vip">VIP</span>' : ""}<span class="tag lv">◆ Lv${me.level}</span></div><div class="id">ID：${me.id}</div></div></div>
      <div class="me-stats"><a data-go="/follows/following"><b>${S.counts.following}</b>关注</a><i class="sep"></i><a data-go="/follows/fans"><b>${S.counts.fans}</b>粉丝</a><a class="edit" data-go="/edit">编辑个人资料 ›</a></div>
      <div class="quick">
        <a data-go="/recharge"><span class="qi" style="background:linear-gradient(135deg,#7cc8ff,#4f9bff)">${ic("coinCard")}</span><b>充值</b><small>余额 <span class="coinsNow">${me.coins}</span></small></a>
        <a data-go="/earn"><span class="qi" style="background:linear-gradient(135deg,#ff8a7a,#ff4d6a)">${ic("gift")}</span><b>免费赚金币</b><small>分享获收益</small></a>
        <a data-go="/vip"><span class="qi" style="background:linear-gradient(135deg,#c58bff,#8d5cff)">${ic("crown")}</span><b>VIP</b><small>${me.vip ? `至 ${me.vipUntil}` : "更多特权"}</small></a>
        <a data-go="/invite"><span class="qi" style="background:linear-gradient(135deg,#ff9ac0,#ff5c9a)">${ic("plus")}</span><b>邀请好友得现金</b><small>得现金</small></a>
      </div>
      <div class="sec-title">设置中心</div>
      <div class="icon-grid">
        <a data-act="dnd"><i class="switch ${me.settings.dnd ? "on" : ""}"></i><span>免打扰</span></a>
        ${cell("/verify", "camera", me.verified ? (me.isHost ? "接听设置" : "开通接听") : me.verifyStatus === "pending" ? "认证审核中" : "视频认证")}${cell("/beauty", "beautyStar", "美颜设置")}${cell("/privacy", "lock", "隐私设置")}
      </div>
      <div class="sec-title">与我相关</div>
      <div class="icon-grid">${cell("/wallet", "wallet", "我的钱包")}${cell("/myposts", "community", "我的动态")}${cell("/guard", "guard", "我的守护")}${cell("/mygifts", "gift", "我的礼物")}
        ${cell("/rates", "tag", "通话评价")}${cell("/visitors", "clock", "访问足迹")}</div>
      <div class="sec-title">其他</div>
      <div class="icon-grid">${cell("/games", "game", "游戏技能")}${cell("/gameorders", "doc", "游戏订单")}${cell("/language", "lang", "语言设置")}${cell("/settings", "gear", "其他设置")}</div>
      <div style="height:30px"></div>`;
  }, { tab: "me" });
  async function saveSettings(patch) {
    S.me.settings = await api("/api/me/settings", { method: "PUT", body: patch });
    return S.me.settings;
  }
  acts.dnd = async () => {
    try { const s = await saveSettings({ dnd: !S.me.settings.dnd }); toast(s.dnd ? "免打扰已开启，将不会收到来电" : "免打扰已关闭"); render(); } catch (e) { fail(e); }
  };

  route(/^\/edit$/, () => {
    const me = S.me;
    return `${pageHd("编辑资料", `<button data-act="saveProfile" style="color:var(--c2);font-weight:600">保存</button>`)}<div class="bg-gray">
      <label class="form-row" for="avFile"><span style="flex:1">头像</span>${av(me, 56).replace('class="av"', 'class="av" id="avPrev"')}${ic("chev", "i chev")}</label><input type="file" id="avFile" accept="image/*" hidden>
      <div class="form-row" style="flex-wrap:wrap"><span style="flex:1">相册（最多 6 张，展示在主页）</span><div class="pub-imgs" id="photoBox" style="width:100%;margin-top:10px"></div><input type="file" id="phFile" accept="image/*" multiple hidden></div>
      <div class="form-row"><label>昵称</label><input id="eName" maxlength="12" value="${esc(me.name)}"></div>
      <div class="form-row"><label>性别</label><select id="eSex"><option value="m" ${me.sex === "m" ? "selected" : ""}>男</option><option value="f" ${me.sex === "f" ? "selected" : ""}>女</option></select></div>
      <div class="form-row"><label>年龄</label><input id="eAge" type="number" min="18" max="80" value="${me.age}"></div>
      <div class="form-row"><label>城市</label><input id="eCity" maxlength="10" value="${esc(me.city)}" placeholder="例如：长沙市"></div>
      <div class="form-row"><label>标签</label><input id="eLabels" maxlength="40" value="${esc((me.labels || []).join(" "))}" placeholder="用空格分开，如：旅行 唱歌"></div>
      <div class="form-row" style="align-items:flex-start"><label>个性签名</label><textarea id="eSign" maxlength="60">${esc(me.sign)}</textarea></div></div>`;
  }, {
    mount: () => {
      const photos = [...(S.me.photos || [])];
      let avatar = S.me.avatar;
      const drawPhotos = () => {
        $("#photoBox").innerHTML = photos.map((p, i) => `<div style="background-image:url('${esc(media(p))}')"><span class="del" data-i="${i}">×</span></div>`).join("") + (photos.length < 6 ? `<label class="add" for="phFile">${ic("plus")}</label>` : "");
        $("#photoBox").querySelectorAll(".del").forEach((d) => (d.onclick = (e) => { e.preventDefault(); photos.splice(+d.dataset.i, 1); drawPhotos(); }));
      };
      drawPhotos();
      $("#avFile").onchange = async (e) => {
        const f = e.target.files[0]; if (!f) return;
        try { avatar = await uploadImage(f, 400); $("#avPrev").style.backgroundImage = `url('${media(avatar)}')`; } catch (err) { fail(err); }
      };
      $("#phFile").onchange = async (e) => {
        try { for (const f of [...e.target.files].slice(0, 6 - photos.length)) photos.push(await uploadImage(f)); drawPhotos(); } catch (err) { fail(err); }
        e.target.value = "";
      };
      acts.saveProfile = async () => {
        try {
          S.me = await api("/api/me", { method: "PUT", body: {
            name: $("#eName").value.trim(), sex: $("#eSex").value, age: +$("#eAge").value, city: $("#eCity").value.trim(),
            sign: $("#eSign").value.trim(), avatar: avatar || null, photos, labels: $("#eLabels").value.split(/\s+/).filter(Boolean).slice(0, 8),
          } });
          toast(S.me.profileRewarded ? "资料已保存" : "资料已保存（填写头像、城市、签名可领 5 金币）"); back("/me");
        } catch (e) { fail(e); }
      };
    },
  });

  route(/^\/follows\/(\w+)$/, async (kind) => {
    const list = await api(`/api/me/follows?kind=${kind}`);
    return `${pageHd(kind === "following" ? "我的关注" : "我的粉丝")}${list.length ? `<div class="list">${list.map((u) =>
      `<div class="cell"><a data-go="/user/${u.id}">${av(u, 46)}</a><div class="grow">${esc(u.name)}<small>${esc(u.sign || statusText(u))}</small></div><button class="btn ${u.followed ? "ghost" : "primary"}" style="height:30px;font-size:13px;padding:0 14px" data-act="follow" data-uid="${u.id}">${u.followed ? "已关注" : kind === "fans" ? "回关" : "关注"}</button></div>`).join("")}</div>`
      : empty("", kind === "following" ? "还没有关注任何人" : "还没有粉丝", kind === "following" ? "去发现页看看吧" : "多发动态更容易被关注哦")}`;
  });

  // ======================= 钱包 / 充值 / VIP =======================
  let walletCache;
  route(/^\/recharge$/, async () => {
    walletCache = await api("/api/wallet");
    const devMock = S.config.devMode ? `<button class="pay-way" data-act="pickPay" data-v="mock" style="width:100%"><span class="pi" style="background:#999">测</span>模拟支付（开发模式）<i class="radio"></i></button>` : "";
    return `${pageHd("充值", `<a data-go="/wallet">明细</a>`)}
      <div class="balance-card"><small>金币余额</small><b class="coinsNow">${walletCache.coins}</b><small>1 元 = 10 金币 · 视频通话、送礼物使用</small></div>
      <div class="packs">${walletCache.packs.map((p, i) => `<button class="pack ${i === 2 ? "on" : ""}" data-act="pickPack" data-i="${i}">${p.hot ? '<span class="hot">热门</span>' : ""}<b>${p.coins}<small>金币</small></b><span>¥${p.yuan}</span>${p.bonus ? `<div style="font-size:11px;color:var(--red);margin-top:2px">加送 ${p.bonus}</div>` : ""}</button>`).join("")}</div>
      <div class="sec-title" style="padding-top:22px">支付方式</div>
      <div class="pay-ways"><button class="pay-way on" data-act="pickPay" data-v="wechat" style="width:100%"><span class="pi" style="background:#09bb07">微</span>微信支付<i class="radio"></i></button>
        <button class="pay-way" data-act="pickPay" data-v="alipay" style="width:100%"><span class="pi" style="background:#1677ff">支</span>支付宝<i class="radio"></i></button>${devMock}</div>
      <div class="agree">充值即代表同意 <a>《充值服务协议》</a>，未成年人禁止充值。如遇问题请联系客服。</div>
      <div style="height:90px"></div>
      <div class="bottom-bar"><button class="btn primary" style="flex:1" data-act="pay" id="payBtn">立即支付 ¥${walletCache.packs[2].yuan}</button></div>`;
  }, { mount: () => { ui.pack = 2; ui.pay = "wechat"; } });
  acts.pickPack = (el) => {
    ui.pack = +el.dataset.i;
    document.querySelectorAll(".pack").forEach((p) => p.classList.toggle("on", p === el));
    $("#payBtn").textContent = `立即支付 ¥${walletCache.packs[ui.pack].yuan}`;
  };
  acts.pickPay = (el) => { ui.pay = el.dataset.v; document.querySelectorAll(".pay-way").forEach((p) => p.classList.toggle("on", p === el)); };
  async function payOrder(body, done) {
    try {
      const o = await api("/api/orders", { method: "POST", body: { ...body, channel: ui.pay } });
      if (o.pay?.mock) { S.me = await api(`/api/orders/${o.orderId}/mock-pay`, { method: "POST" }); done(); }
      // 正式支付：o.pay 里是微信/支付宝的支付参数，App 内用原生 SDK 拉起
    } catch (e) {
      if (e.code === "pay_not_configured") {
        return dialog({ title: "支付通道未开通", text: S.config.devMode ? "微信/支付宝需要商户号才能收款。开发阶段可以用模拟支付测试完整流程。" : e.message,
          ok: S.config.devMode ? "用模拟支付" : "知道了", cancel: S.config.devMode ? "取消" : "", onOk: () => { if (S.config.devMode) { ui.pay = "mock"; payOrder(body, done); } } });
      }
      fail(e);
    }
  }
  acts.pay = () => payOrder({ kind: "coins", pack: ui.pack }, () => { toast("充值成功"); render(); });

  route(/^\/wallet$/, async () => {
    const w = await api("/api/wallet");
    return `${pageHd("我的钱包")}
      <div class="balance-card"><small>金币余额</small><b class="coinsNow">${w.coins}</b>${S.me.isHost ? `<small>主播收益：${w.earnings} 金币（可提现 ¥${(w.earnings / 10).toFixed(2)}）</small>` : ""}
        <div style="display:flex;gap:10px;margin-top:12px"><button class="btn" style="background:#fff;color:#ff7a59;height:36px;font-size:14px" data-go="/recharge">充值</button><button class="btn" style="background:rgba(255,255,255,.25);color:#fff;height:36px;font-size:14px" data-act="withdraw" data-e="${w.earnings}">提现</button></div></div>
      <div class="sec-title" style="padding-top:6px">收支明细</div>
      <div class="list">${w.ledger.length ? w.ledger.map((l) => `<div class="cell"><div class="grow">${esc(l.title)}${l.account === "earnings" ? ' <span class="status-pill ok">收益</span>' : ""}<small>${l.time}</small></div><b style="color:${l.amount > 0 ? "var(--green)" : "var(--text)"}">${l.amount > 0 ? "+" : ""}${l.amount}</b></div>`).join("") : '<p style="text-align:center;color:var(--muted);padding:20px">暂无记录</p>'}</div>`;
  });
  acts.withdraw = (el) => {
    if (!S.me.isHost) return dialog({ title: "提现", text: "充值的金币不能提现；完成视频认证并开通接听后，收到的礼物和通话收益可以提现。", cancel: "", ok: "知道了" });
    const sh = openSheet(`<h3>提现（可提 ${el.dataset.e} 收益）</h3>
      <div class="s-row"><label>提现金额（金币，最少 100，10 金币 = 1 元）</label><input id="wCoins" type="number" min="100" step="10" value="${el.dataset.e}" style="width:100%;height:44px;border:1px solid var(--line);border-radius:10px;padding:0 12px"></div>
      <div class="s-row"><label>收款账户</label><input id="wAcc" maxlength="60" placeholder="支付宝账号 + 真实姓名" style="width:100%;height:44px;border:1px solid var(--line);border-radius:10px;padding:0 12px"></div>
      <button class="btn primary block" id="wOk">提交</button>`);
    sh.querySelector("#wOk").onclick = async () => {
      try { await api("/api/withdraw", { method: "POST", body: { coins: +sh.querySelector("#wCoins").value, account: sh.querySelector("#wAcc").value.trim() } }); closeLayer(); toast("提现申请已提交"); render(); }
      catch (e) { fail(e); }
    };
  };

  route(/^\/earn$/, async () => {
    S.me = await api("/api/me");
    const me = S.me;
    const task = (icon, title, reward, btn, act, done) => `<div class="task"><span class="ti2">${icon}</span><div class="grow"><b>${title}</b><small>${reward}</small></div><button class="btn ${done ? "ghost" : "primary"}" ${done ? "disabled" : ""} data-act="${act}">${btn}</button></div>`;
    return `${pageHd("免费赚金币")}<div class="balance-card" style="background:linear-gradient(135deg,#ff8a7a,#ff4d6a)"><small>当前金币</small><b class="coinsNow">${me.coins}</b><small>完成任务领取金币，可用于视频通话</small></div>
      ${task("📅", "每日签到", me.vip ? "+1 金币，VIP 再送 10" : "+1 金币", me.signedToday ? "已签到" : "签到", "signIn", me.signedToday)}
      ${task("👤", "完善个人资料（头像、城市、签名）", "+5 金币", me.profileRewarded ? "已领取" : "去完善", "goEdit", me.profileRewarded)}
      ${task("📷", "完成视频认证", "+10 金币", me.verified ? "已完成" : me.verifyStatus === "pending" ? "审核中" : "去认证", "goVerify", me.verified || me.verifyStatus === "pending")}
      ${task("🔗", "分享给好友", "+2 金币 / 每日", me.sharedToday ? "已分享" : "去分享", "taskShare", me.sharedToday)}
      ${task("🎁", "邀请好友注册并首充", "+50 金币 / 人 + 10% 返现", "去邀请", "goInvite", false)}`;
  });
  acts.signIn = async () => { try { S.me = await api("/api/tasks/sign", { method: "POST" }); toast("签到成功"); render(); } catch (e) { fail(e); } };
  acts.goEdit = () => go("/edit");
  acts.goVerify = () => go("/verify");
  acts.goInvite = () => go("/invite");
  acts.taskShare = async () => { await acts.share(); render(); };

  route(/^\/vip$/, async () => {
    walletCache = await api("/api/wallet");
    const me = S.me;
    return `${pageHd("VIP 会员")}<div class="vip-hero"><h2>👑 SeeU VIP</h2><p>${me.vip ? `会员有效期至 ${me.vipUntil}` : "开通会员，畅享专属特权"}</p></div>
      <div class="vip-plans">${walletCache.vipPlans.map((p, i) => `<button class="vip-plan ${i === 1 ? "on" : ""}" data-act="pickVip" data-id="${p.id}" data-p="${p.yuan}">${p.title}<b>¥${p.yuan}</b><s>¥${p.origin}</s></button>`).join("")}</div>
      <div class="perks">${[["👑", "尊贵标识"], ["💰", "通话 9 折"], ["🎁", "签到多送 10 币"], ["👀", "查看全部访客"], ["🕶️", "隐身访问"], ["💬", "专属客服"]].map(([i, t]) => `<div><i>${i}</i>${t}</div>`).join("")}</div>
      <div class="page-pad"><div class="pay-ways" style="padding:0 0 12px"><button class="pay-way on" data-act="pickPay" data-v="wechat" style="width:100%"><span class="pi" style="background:#09bb07">微</span>微信支付<i class="radio"></i></button><button class="pay-way" data-act="pickPay" data-v="alipay" style="width:100%"><span class="pi" style="background:#1677ff">支</span>支付宝<i class="radio"></i></button></div>
        <button class="btn block" style="background:linear-gradient(135deg,#f4d8a0,#d9a85b);color:#4a3418" data-act="buyVip" id="vipBtn">${me.vip ? "续费" : "立即开通"} ¥${walletCache.vipPlans[1].yuan}</button></div>`;
  }, { mount: () => { ui.vip = walletCache.vipPlans[1].id; ui.pay = "wechat"; } });
  acts.pickVip = (el) => { ui.vip = el.dataset.id; document.querySelectorAll(".vip-plan").forEach((p) => p.classList.toggle("on", p === el)); $("#vipBtn").textContent = `${S.me.vip ? "续费" : "立即开通"} ¥${el.dataset.p}`; };
  acts.buyVip = () => payOrder({ kind: "vip", plan: ui.vip }, () => { toast("VIP 开通成功"); render(); });

  route(/^\/invite$/, async () => {
    const inv = await api("/api/invite");
    return `${pageHd("邀请好友")}<div class="invite-hero"><h2>邀请好友 得现金</h2><p>好友用你的邀请码注册并完成首充，你得 50 金币 + 充值额 10% 返现</p><div class="code">${inv.code}</div></div>
      <div class="steps"><div><i>1</i>分享邀请链接</div><div><i>2</i>好友注册登录</div><div><i>3</i>好友首充</div><div><i>4</i>奖励到账</div></div>
      <div class="page-pad" style="display:flex;gap:12px"><button class="btn ghost" style="flex:1" data-act="copyCode" data-c="${inv.code}">复制邀请码</button><button class="btn pink" style="flex:1" data-act="share">分享给好友</button></div>
      <div class="sec-title" style="padding-top:8px">我邀请的人（${inv.list.length}）</div>
      ${inv.list.length ? `<div class="list">${inv.list.map((u) => `<div class="cell">${av(u, 42)}<div class="grow">${esc(u.name)}<small>${u.time} 注册</small></div><span class="status-pill ${u.paid ? "ok" : ""}">${u.paid ? "已首充" : "未首充"}</span></div>`).join("")}</div>` : empty("", "还没有邀请到好友", "快去分享吧")}`;
  });
  acts.copyCode = (el) => navigator.clipboard?.writeText(el.dataset.c).then(() => toast("邀请码已复制"), () => toast("邀请码：" + el.dataset.c));

  // ======================= 视频认证 / 接听设置 / 美颜 =======================
  function cameraInto(sel, phSel, opts = { video: { facingMode: "user" } }) {
    let stream;
    const p = navigator.mediaDevices?.getUserMedia?.(opts)
      .then((s) => { stream = s; const v = $(sel); if (v) v.srcObject = s; else s.getTracks().forEach((t) => t.stop()); $(phSel)?.classList.add("hidden"); return s; })
      .catch(() => { const el = $(phSel); if (el) el.textContent = "没有摄像头权限，请在设置中允许"; return null; });
    onLeave(() => stream?.getTracks().forEach((t) => t.stop()));
    return p || Promise.resolve(null);
  }
  let verifyStream;
  route(/^\/verify$/, async () => {
    S.me = await api("/api/me");
    const me = S.me;
    if (me.verified) {
      return `${pageHd("接听设置")}<div class="page-pad" style="text-align:center"><span class="status-pill ok">✓ 已通过视频认证</span></div>
        <div class="host-box"><b>开通接听收费</b><p style="color:var(--muted);font-size:13px;margin:6px 0 10px">开通后会出现在发现页，别人和你视频/语音通话按分钟付费，你获得 50% 分成，可以提现。</p>
          <div class="slider-row"><label>视频价格</label><input type="range" id="hPrice" min="5" max="200" step="5" value="${me.price || 30}"><span id="hPriceV" style="width:70px">${me.price || 30} 币/分</span></div>
          <div class="slider-row"><label>语音价格</label><input type="range" id="hVoice" min="5" max="200" step="5" value="${me.voicePrice || 15}"><span id="hVoiceV" style="width:70px">${me.voicePrice || 15} 币/分</span></div>
          <div class="cell" style="padding:12px 0;border:0" data-act="hostToggle"><div class="grow">接听收费</div><i class="switch ${me.isHost ? "on" : ""}" id="hOn"></i></div>
          <button class="btn primary block" data-act="saveHost">保存</button></div>`;
    }
    if (me.verifyStatus === "pending") return `${pageHd("视频认证")}${empty("认证审核中", "通常 24 小时内完成，结果会通过系统消息通知你")}`;
    return `${pageHd("视频认证")}<div class="beauty-preview"><video id="vfVideo" autoplay muted playsinline></video><div class="ph" id="vfPh">正在打开摄像头…</div>
        <div style="position:absolute;inset:12% 22%;border:3px dashed rgba(255,255,255,.7);border-radius:50%"></div></div>
      <div class="page-pad" style="text-align:center"><b style="font-size:18px" id="vfStep">${me.verifyStatus === "rejected" ? "上次认证未通过，请重新录制" : "请正对屏幕，保持光线充足"}</b>
        <p style="color:var(--muted);font-size:13px">将录制约 6 秒视频，按提示做动作。认证视频仅用于人工审核，不会公开。</p>
        <button class="btn primary block" data-act="doVerify" id="vfBtn">开始录制</button></div>`;
  }, {
    mount: async () => {
      if ($("#vfVideo")) verifyStream = await cameraInto("#vfVideo", "#vfPh", { video: { facingMode: "user" }, audio: false });
      const bind = (id, out) => { const r = $(id); if (r) r.oninput = () => ($(out).textContent = `${r.value} 币/分`); };
      bind("#hPrice", "#hPriceV"); bind("#hVoice", "#hVoiceV");
    },
  });
  acts.hostToggle = () => $("#hOn").classList.toggle("on");
  acts.saveHost = async () => {
    try {
      S.me = await api("/api/me/host", { method: "PUT", body: { price: +$("#hPrice").value, voicePrice: +$("#hVoice").value, on: $("#hOn").classList.contains("on") } });
      toast(S.me.isHost ? "已开通接听收费" : "已关闭接听收费"); back("/me");
    } catch (e) { fail(e); }
  };
  acts.doVerify = async () => {
    if (!verifyStream || !window.MediaRecorder) return toast("无法录制，请检查摄像头权限");
    const btn = $("#vfBtn");
    btn.disabled = true;
    const type = ["video/mp4", "video/webm;codecs=vp8", "video/webm"].find((t) => MediaRecorder.isTypeSupported(t)) || "";
    const rec = new MediaRecorder(verifyStream, type ? { mimeType: type } : undefined);
    const chunks = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    const steps = ["请眨眨眼", "请缓慢向左转头", "请缓慢向右转头", "正在上传…"];
    rec.onstop = async () => {
      try {
        const url = await upload("video", new Blob(chunks, { type: rec.mimeType }), `verify.${(rec.mimeType || "").includes("mp4") ? "mp4" : "webm"}`);
        await api("/api/verify", { method: "POST", body: { video: url } });
        toast("已提交，等待审核"); render();
      } catch (e) { fail(e); btn.disabled = false; }
    };
    rec.start();
    steps.forEach((s, i) => setTimeout(() => { const el = $("#vfStep"); if (el) el.textContent = s; }, i * 2000));
    setTimeout(() => rec.state !== "inactive" && rec.stop(), 6000);
  };

  route(/^\/beauty$/, () => {
    const b = S.me.settings.beauty;
    const row = (k, t) => `<div class="slider-row"><label>${t}</label><input type="range" min="0" max="100" value="${b[k]}" data-k="${k}"><span id="bv_${k}">${b[k]}</span></div>`;
    return `${pageHd("美颜设置", `<button data-act="beautyReset">重置</button>`)}<div class="beauty-preview"><video id="bfVideo" autoplay muted playsinline></video><div class="ph" id="bfPh">正在打开摄像头…</div></div>
      <div style="padding:10px 0">${row("smooth", "磨皮")}${row("white", "美白")}${row("ruddy", "红润")}${row("slim", "瘦脸")}</div>
      <div class="cell" data-act="beautyOnToggle"><div class="grow">视频通话时开启美颜</div><i class="switch ${S.me.settings.beautyOn ? "on" : ""}" id="bOn"></i></div>
      <div class="page-pad"><button class="btn primary block" data-act="beautySave">保存</button></div>`;
  }, {
    mount: () => {
      cameraInto("#bfVideo", "#bfPh");
      const cur = { ...S.me.settings.beauty };
      const apply = () => { $("#bfVideo").style.filter = `blur(${cur.smooth / 160}px) brightness(${1 + cur.white / 400}) saturate(${1 + cur.ruddy / 250}) contrast(${1 - cur.smooth / 1000})`; };
      document.querySelectorAll("[data-k]").forEach((r) => (r.oninput = () => { cur[r.dataset.k] = +r.value; $(`#bv_${r.dataset.k}`).textContent = r.value; apply(); }));
      apply();
      acts.beautySave = async () => { try { await saveSettings({ beauty: cur, beautyOn: $("#bOn").classList.contains("on") }); toast("美颜设置已保存"); back("/me"); } catch (e) { fail(e); } };
      acts.beautyReset = () => { Object.assign(cur, { smooth: 50, white: 40, ruddy: 30, slim: 20 }); document.querySelectorAll("[data-k]").forEach((r) => { r.value = cur[r.dataset.k]; $(`#bv_${r.dataset.k}`).textContent = r.value; }); apply(); };
    },
  });
  acts.beautyOnToggle = () => $("#bOn").classList.toggle("on");

  // ======================= 设置类页面 =======================
  const switchCell = (k, title, desc) => `<div class="cell" data-act="toggleSet" data-k="${k}"><div class="grow">${title}${desc ? `<small>${desc}</small>` : ""}</div><i class="switch ${S.me.settings[k] ? "on" : ""}"></i></div>`;
  acts.toggleSet = async (el) => {
    const k = el.dataset.k;
    try { const s = await saveSettings({ [k]: !S.me.settings[k] }); el.querySelector(".switch").classList.toggle("on", s[k]); } catch (e) { fail(e); }
  };
  route(/^\/privacy$/, () => `${pageHd("隐私设置")}<div class="list">${switchCell("hideDistance", "隐藏我的同城信息", "别人在你的主页看不到「同城」")}${switchCell("hideNearby", "不在「附近」中展示我")}${switchCell("stealth", "隐身访问", "访问别人主页不留下足迹（VIP）")}</div>
    <div class="group-gap"></div><div class="list"><a class="cell" data-go="/blacklist"><div class="grow">黑名单</div>${ic("chev", "i chev")}</a></div>`);
  route(/^\/blacklist$/, async () => {
    const list = await api("/api/me/blocks");
    return `${pageHd("黑名单")}${list.length ? `<div class="list">${list.map((u) => `<div class="cell">${av(u, 46)}<div class="grow">${esc(u.name)}</div><button class="btn ghost" style="height:30px;font-size:13px;padding:0 14px" data-act="unblock" data-uid="${u.id}">移出</button></div>`).join("")}</div>` : empty("", "黑名单是空的")}`;
  });
  acts.unblock = (el) => api(`/api/users/${el.dataset.uid}/block`, { method: "POST" }).then(() => { toast("已移出黑名单"); render(); }, fail);

  route(/^\/settings$/, () => `${pageHd("其他设置")}<div class="list">${switchCell("notify", "新消息通知")}
      <a class="cell" data-act="clearCache"><div class="grow">清除缓存</div>${ic("chev", "i chev")}</a>
      <a class="cell" data-act="doc" data-t="用户协议"><div class="grow">用户协议</div>${ic("chev", "i chev")}</a>
      <a class="cell" data-act="doc" data-t="隐私政策"><div class="grow">隐私政策</div>${ic("chev", "i chev")}</a>
      <a class="cell" data-act="doc" data-t="关于我们"><div class="grow">关于我们</div><span class="val">v0.2</span>${ic("chev", "i chev")}</a>
      <a class="cell" data-act="openDownload"><div class="grow">下载 App</div>${ic("chev", "i chev")}</a></div>
    <div class="page-pad"><button class="btn block" style="background:#fff;color:var(--red);border:1px solid #ffd6dc" data-act="logout">退出登录</button></div>`);
  acts.openDownload = () => { location.href = `${API}/download/`; };
  acts.clearCache = async () => { try { const ks = await caches?.keys?.(); await Promise.all((ks || []).map((k) => caches.delete(k))); } catch {} S.gifts = null; bannersCache = null; toast("缓存已清除"); };
  acts.doc = (el) => dialog({ title: el.dataset.t, text: "正式文本需在上线前由律师撰写（涉及用户协议、隐私政策、未成年人保护、充值协议）。", cancel: "", ok: "好的" });
  acts.logout = () => logout(true);

  route(/^\/language$/, () => `${pageHd("语言设置")}<div class="list">${["简体中文", "繁體中文", "English"].map((l) => `<a class="cell" data-act="setLang" data-v="${l}"><div class="grow">${l}</div>${S.me.settings.lang === l ? `<span style="color:var(--c1)">${ic("check")}</span>` : ""}</a>`).join("")}</div>`);
  acts.setLang = async (el) => {
    try { await saveSettings({ lang: el.dataset.v }); render(); if (el.dataset.v !== "简体中文") toast("已保存。多语言翻译还在制作中，暂时仍显示简体中文"); } catch (e) { fail(e); }
  };

  route(/^\/myposts$/, async () => {
    const list = (postsCache = await api(`/api/posts?userId=${S.me.id}`));
    return `${pageHd("我的动态", `<a data-go="/publish/post">发布</a>`)}${list.length ? list.map(postHtml).join("") : empty("", "你还没有发布过动态", "点右上角发布第一条吧")}`;
  });
  route(/^\/guard$/, async () => {
    const list = await api("/api/me/guards");
    return `${pageHd("我的守护")}${list.length ? `<div class="list">${list.map((u) => `<a class="cell" data-go="/user/${u.id}">${av(u, 46)}<div class="grow">${esc(u.name)}<small>累计送出 ${u.total} 金币</small></div><span class="status-pill ok">守护中</span></a>`).join("")}</div>`
      : empty("", "还没有守护任何人", "给喜欢的人累计送出 1314 金币的礼物即可成为她的守护")}`;
  });
  route(/^\/mygifts$/, async () => {
    const list = await api("/api/me/gifts");
    return `${pageHd("我的礼物")}${list.length ? `<div class="list">${list.map((r) => `<div class="cell"><span style="font-size:30px">${r.gift?.icon || "🎁"}</span><div class="grow">${r.sent ? "送给" : "收到"} ${esc(r.peer)} 的${esc(r.gift?.name || "礼物")}<small>${r.time}</small></div><span class="val">${r.gift?.price || 0} 金币</span></div>`).join("")}</div>` : empty("", "还没有送出或收到礼物")}`;
  });
  route(/^\/rates$/, async () => {
    const list = await api("/api/me/ratings");
    return `${pageHd("通话评价")}${list.length ? `<div class="list">${list.map((r) => `<div class="cell">${av(r.user, 46)}<div class="grow">${esc(r.user.name)} <span style="color:#ffb020">${"★".repeat(r.stars)}</span><small>${r.tags.map(esc).join(" · ") || "无标签"} · ${r.time}</small></div></div>`).join("")}</div>` : empty("", "还没有通话评价", "视频通话结束后可以评价对方")}`;
  });
  route(/^\/visitors$/, async () => {
    const list = await api("/api/me/visitors");
    return `${pageHd("访问足迹")}${list.length ? `<div class="list">${list.map((v) => v.locked
      ? `<a class="cell" data-go="/vip"><div class="av" style="width:46px;height:46px;background:#ddd;filter:blur(3px)"></div><div class="grow">开通 VIP 查看<small>${v.time} 看过你</small></div>${ic("chev", "i chev")}</a>`
      : `<a class="cell" data-go="/user/${v.id}">${av(v, 46)}<div class="grow">${esc(v.name)}<small>${v.time} 看过你</small></div>${ic("chev", "i chev")}</a>`).join("")}</div>` : empty("", "还没有人看过你", "完善资料、多发动态能获得更多关注")}`;
  });
  route(/^\/games$/, () => `${pageHd("游戏技能")}${empty("", "游戏陪玩功能即将上线", "上线后可以添加游戏技能、接陪玩订单")}`);
  route(/^\/gameorders$/, () => `${pageHd("游戏订单")}${empty("", "游戏陪玩功能即将上线")}`);

  // ======================= 启动 =======================
  if (new URLSearchParams(location.search).get("invite") && !TOKEN) location.hash = "#/login";
  render();
})();
