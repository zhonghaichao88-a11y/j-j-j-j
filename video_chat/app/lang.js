// 多语言：页面渲染出来后，把界面上的中文按词典换成繁体 / 英文。
// 用户自己写的内容（昵称、聊天、动态）不在词典里，英文模式下保持原样；繁体模式下做简繁字形转换。
window.I18N = (() => {
  const DATA = window.I18N_DATA || { en: {}, "zh-TW": {}, chars: {} };
  const CODES = { 简体中文: "zh-CN", 繁體中文: "zh-TW", English: "en" };
  let lang = "zh-CN";
  const res = {};
  function build(code) {
    if (res[code] || code === "zh-CN") return res[code];
    const dict = DATA[code] || {};
    // 两个字以上的词条可以在长句里按片段替换；单字只在整段完全相同时替换，避免误伤
    const keys = Object.keys(dict).filter((k) => k.length >= 2).sort((a, b) => b.length - a.length);
    const re = keys.length ? new RegExp(keys.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "g") : null;
    return (res[code] = { dict, re });
  }
  function tr(text) {
    if (lang === "zh-CN" || !text || !/[一-鿿]/.test(text)) return text;
    const m = text.match(/^(\s*)([\s\S]*?)(\s*)$/);
    const core = m[2];
    const { dict, re } = build(lang);
    let out = dict[core];
    if (out === undefined) {
      out = re ? core.replace(re, (k) => dict[k]) : core;
      if (lang === "zh-TW") out = out.replace(/[一-鿿]/g, (c) => DATA.chars[c] || c);
      if (lang === "en") out = out.replace(/(\d)(?=[A-Za-z])/g, "$1 ").replace(/ {2,}/g, " ");
    }
    return m[1] + out + m[3];
  }
  const ATTRS = ["placeholder", "title", "alt", "aria-label"];
  function applyNode(n) {
    if (n.nodeType === 3) {
      const p = n.parentNode;
      if (!p || /^(SCRIPT|STYLE|TEXTAREA)$/.test(p.nodeName)) return;
      if (n.__tr !== n.data) n.__zh = n.data;     // 程序改过文字，以新的中文为准
      const t = tr(n.__zh);
      if (t !== n.data) n.data = t;
      n.__tr = n.data;
    } else if (n.nodeType === 1) {
      for (const a of ATTRS) {
        if (!n.hasAttribute(a)) continue;
        const key = `__zh_${a}`;
        if (n[`__tr_${a}`] !== n.getAttribute(a)) n[key] = n.getAttribute(a);
        const t = tr(n[key]);
        if (t !== n.getAttribute(a)) n.setAttribute(a, t);
        n[`__tr_${a}`] = n.getAttribute(a);
      }
      if (/^(SCRIPT|STYLE|TEXTAREA)$/.test(n.nodeName)) return;
      for (let c = n.firstChild; c; c = c.nextSibling) applyNode(c);
    }
  }
  let observer;
  function watch() {
    if (observer) return;
    observer = new MutationObserver((list) => {
      if (lang === "zh-CN") return;
      for (const m of list) {
        if (m.type === "characterData") applyNode(m.target);
        else if (m.type === "attributes") applyNode(m.target);
        else m.addedNodes.forEach(applyNode);
      }
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }
  function set(name) {
    const code = CODES[name] || name || "zh-CN";
    if (code === lang) return;
    const prev = lang;
    lang = code;
    document.documentElement.lang = code;
    try { localStorage.setItem("seeu.lang", code); } catch {}
    watch();
    applyNode(document.body);
    if (code === "zh-CN" && prev !== "zh-CN") restore(document.body);
  }
  function restore(n) {   // 切回简体：把文字还原成原来的中文
    if (n.nodeType === 3 && n.__zh !== undefined) { n.data = n.__zh; n.__tr = n.data; }
    if (n.nodeType === 1) {
      for (const a of ATTRS) if (n[`__zh_${a}`] !== undefined) n.setAttribute(a, n[`__zh_${a}`]);
      for (let c = n.firstChild; c; c = c.nextSibling) restore(c);
    }
  }
  return { set, tr, get lang() { return lang; }, init() { try { set(localStorage.getItem("seeu.lang") || "zh-CN"); } catch {} } };
})();
