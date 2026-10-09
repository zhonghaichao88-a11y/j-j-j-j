// 见面 SeeU 前端。当前是“设计预览版”：所有按钮都能点，后端还没接，
// 进房间后会放入演示成员，方便看一对一和多人布局。接后端时只需替换 Demo 部分。
(() => {
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const DEMO_PEERS = Math.max(0, Math.min(5, parseInt(params.get("demo") ?? "1", 10) || 0));

  const state = {
    name: "",
    room: "",
    stream: null,       // 摄像头+麦克风
    screen: null,       // 屏幕共享流
    mic: true,
    cam: true,
    facing: "user",
    peers: new Map(),   // id -> { name, mic, cam, tile }
    startedAt: 0,
    unread: 0,
  };

  // ---------- 工具 ----------
  let toastTimer;
  function toast(text) {
    const t = $("toast");
    t.textContent = text;
    t.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.add("hidden"), 2200);
  }
  const initial = (n) => (n || "?").trim().slice(0, 1).toUpperCase();
  const randomCode = () => {
    const a = "abcdefghijkmnpqrstuvwxyz";
    const part = (n) => Array.from({ length: n }, () => a[Math.floor(Math.random() * a.length)]).join("");
    return `${part(3)}-${part(4)}-${part(3)}`;
  };
  const validCode = (c) => /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/.test(c);
  const savedName = () => { try { return localStorage.getItem("seeu.name") || ""; } catch { return ""; } };
  const saveName = (n) => { try { localStorage.setItem("seeu.name", n); } catch {} };

  // ---------- 摄像头 ----------
  async function openMedia() {
    if (!navigator.mediaDevices?.getUserMedia) {
      $("previewHint").textContent = "此浏览器不支持摄像头（需 https）";
      state.cam = false;
      return renderPreview();
    }
    try {
      state.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
        video: { facingMode: state.facing, width: { ideal: 1280 }, height: { ideal: 720 } },
      });
      $("previewHint").textContent = "摄像头预览";
    } catch (e) {
      try { state.stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch {}
      state.cam = false;
      $("previewHint").textContent = "没有拿到摄像头权限，可在浏览器地址栏开启";
    }
    applyTracks();
    renderPreview();
  }

  function applyTracks() {
    state.stream?.getAudioTracks().forEach((t) => (t.enabled = state.mic));
    state.stream?.getVideoTracks().forEach((t) => (t.enabled = state.cam));
  }

  const hasVideo = () => !!state.stream?.getVideoTracks().length;

  function renderPreview() {
    $("previewVideo").srcObject = state.stream;
    const showVideo = state.cam && hasVideo();
    $("previewVideo").classList.toggle("hidden", !showVideo);
    $("previewAvatar").classList.toggle("hidden", showVideo);
    $("previewAvatar").querySelector("b").textContent = initial($("nameInput").value);
    setBtn($("pMic"), state.mic, "麦克风");
    setBtn($("pCam"), state.cam, "摄像头");
  }

  function setBtn(btn, on, label) {
    btn.classList.toggle("off", !on);
    btn.title = `${on ? "关闭" : "打开"}${label}`;
  }

  // ---------- 大厅 ----------
  $("nameInput").value = savedName();
  $("nameInput").addEventListener("input", renderPreview);
  $("pMic").onclick = () => { state.mic = !state.mic; applyTracks(); renderPreview(); };
  $("pCam").onclick = () => {
    if (!hasVideo()) return toast("没有可用的摄像头");
    state.cam = !state.cam; applyTracks(); renderPreview();
  };
  $("createBtn").onclick = () => enterRoom(randomCode());
  $("joinBtn").onclick = () => {
    const code = $("codeInput").value.trim().toLowerCase();
    if (!validCode(code)) return toast("房间号格式不对，应类似 abc-defg-hij");
    enterRoom(code);
  };
  $("codeInput").addEventListener("keydown", (e) => e.key === "Enter" && $("joinBtn").click());

  // ---------- 进房间 ----------
  function enterRoom(code) {
    const name = $("nameInput").value.trim();
    if (!name) { $("nameInput").focus(); return toast("先填一个昵称吧"); }
    saveName(name);
    state.name = name;
    state.room = code;
    history.replaceState(null, "", `?room=${code}${params.has("demo") ? `&demo=${DEMO_PEERS}` : ""}`);

    $("lobby").classList.add("hidden");
    $("room").classList.remove("hidden");
    $("roomCodeText").textContent = code;
    $("flipBtn").classList.toggle("hidden", !/Android|iPhone|iPad/i.test(navigator.userAgent));
    if (!navigator.mediaDevices?.getDisplayMedia) $("screenBtn").classList.add("hidden");

    addTile("self", { name: `${name}（我）`, self: true });
    syncDock();
    state.startedAt = Date.now();
    setInterval(tickTimer, 1000);
    sysMsg(`你已进入房间 ${code}`);

    startDemo();
  }

  function tickTimer() {
    const s = Math.floor((Date.now() - state.startedAt) / 1000);
    const mm = String(Math.floor(s / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    $("timer").textContent = s >= 3600 ? `${Math.floor(s / 3600)}:${mm.slice(-2)}:${ss}` : `${mm}:${ss}`;
  }

  // ---------- 视频格子 ----------
  function addTile(id, { name, self = false, stream = null, mic = true, cam = true }) {
    const tile = document.createElement("div");
    tile.className = "tile" + (self ? " self mirror" : "");
    tile.dataset.id = id;
    tile.innerHTML = `
      <video autoplay playsinline ${self ? "muted" : ""}></video>
      <div class="avatar"><b></b></div>
      <span class="name"><svg class="muted-icon"><use href="#i-mic"/><path d="M3 3l18 18"/></svg><span></span></span>`;
    tile.querySelector(".avatar b").textContent = initial(name);
    tile.querySelector(".name span").textContent = name;
    $("grid").appendChild(tile);
    if (!self) state.peers.set(id, { name, mic, cam, tile });
    updateTile(id, { stream: self ? state.stream : stream, mic, cam });
    layout();
    return tile;
  }

  function tileOf(id) { return $("grid").querySelector(`.tile[data-id="${CSS.escape(id)}"]`); }

  function updateTile(id, { stream, mic, cam }) {
    const tile = tileOf(id);
    if (!tile) return;
    const video = tile.querySelector("video");
    if (stream !== undefined && video.srcObject !== stream) video.srcObject = stream;
    const showVideo = cam && !!video.srcObject?.getVideoTracks().length;
    video.classList.toggle("hidden", !showVideo);
    tile.querySelector(".avatar").classList.toggle("hidden", showVideo);
    tile.querySelector(".muted-icon").classList.toggle("hidden", mic);
  }

  function removeTile(id) {
    tileOf(id)?.remove();
    state.peers.delete(id);
    layout();
  }

  function layout() {
    const grid = $("grid");
    const n = grid.querySelectorAll(".tile").length;
    grid.classList.toggle("duo", n === 2);
    $("waiting").classList.toggle("hidden", n > 1);
    const narrow = innerWidth < 860;
    const cols = n <= 1 ? 1 : n <= 4 ? 2 : narrow ? 2 : 3;
    grid.style.setProperty("--cols", cols);
    $("peerCount").textContent = n;
  }
  addEventListener("resize", layout);

  function syncDock() {
    setBtn($("micBtn"), state.mic, "麦克风");
    setBtn($("camBtn"), state.cam || !!state.screen, "摄像头");
    $("screenBtn").classList.toggle("on", !!state.screen);
    const self = tileOf("self");
    if (self) {
      self.classList.toggle("screen", !!state.screen);
      updateTile("self", { stream: state.screen || state.stream, mic: state.mic, cam: state.cam || !!state.screen });
    }
    renderPreview();
  }

  // ---------- 底部按钮 ----------
  $("micBtn").onclick = () => {
    state.mic = !state.mic; applyTracks(); syncDock();
    toast(state.mic ? "麦克风已打开" : "已静音");
  };
  $("camBtn").onclick = () => {
    if (!hasVideo()) return toast("没有可用的摄像头");
    state.cam = !state.cam; applyTracks(); syncDock();
  };
  $("flipBtn").onclick = async () => {
    if (!hasVideo()) return;
    state.facing = state.facing === "user" ? "environment" : "user";
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: state.facing } });
      const [newTrack] = s.getVideoTracks();
      const [old] = state.stream.getVideoTracks();
      state.stream.removeTrack(old); old.stop();
      state.stream.addTrack(newTrack);
      applyTracks();
      tileOf("self").classList.toggle("mirror", state.facing === "user");
      syncDock();
    } catch { toast("切换摄像头失败"); }
  };
  $("screenBtn").onclick = async () => {
    if (state.screen) return stopScreen();
    try {
      state.screen = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      state.screen.getVideoTracks()[0].onended = stopScreen;
      syncDock();
      toast("正在共享屏幕");
    } catch { /* 用户取消 */ }
  };
  function stopScreen() {
    state.screen?.getTracks().forEach((t) => t.stop());
    state.screen = null;
    syncDock();
  }
  $("chatBtn").onclick = () => toggleChat();
  $("closeChat").onclick = () => toggleChat(false);
  $("leaveBtn").onclick = () => {
    if (!confirm("确定要离开房间吗？")) return;
    stopScreen();
    state.stream?.getTracks().forEach((t) => t.stop());
    location.href = location.pathname;
  };
  $("copyBtn").onclick = async () => {
    const link = `${location.origin}${location.pathname}?room=${state.room}`;
    try { await navigator.clipboard.writeText(link); toast("邀请链接已复制，发给朋友即可"); }
    catch { prompt("复制这个链接发给朋友：", link); }
  };

  // ---------- 聊天 ----------
  function toggleChat(force) {
    const chat = $("chat");
    const open = force ?? chat.classList.contains("hidden");
    chat.classList.toggle("hidden", !open);
    $("chatBtn").classList.toggle("on", open);
    if (open) {
      state.unread = 0; renderUnread();
      $("chatInput").focus();
      $("messages").scrollTop = $("messages").scrollHeight;
    }
    layout();
  }
  function renderUnread() {
    $("unread").textContent = state.unread > 99 ? "99+" : state.unread;
    $("unread").classList.toggle("hidden", !state.unread);
  }
  function addMsg({ name, text, mine = false }) {
    const box = $("messages");
    const el = document.createElement("div");
    el.className = "msg" + (mine ? " mine" : "");
    const time = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    el.innerHTML = `<div class="meta"></div><div class="bubble"></div>`;
    el.querySelector(".meta").textContent = mine ? time : `${name} · ${time}`;
    el.querySelector(".bubble").textContent = text;
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;
    if (!mine && $("chat").classList.contains("hidden")) { state.unread++; renderUnread(); }
  }
  function sysMsg(text) {
    const el = document.createElement("div");
    el.className = "msg sys";
    el.textContent = text;
    $("messages").appendChild(el);
  }
  $("composer").onsubmit = (e) => {
    e.preventDefault();
    const text = $("chatInput").value.trim();
    if (!text) return;
    $("chatInput").value = "";
    addMsg({ name: state.name, text, mine: true });
    // 接后端后：通过 WebSocket 发给房间里的其他人
  };

  // ---------- 演示成员（接后端后删除） ----------
  function startDemo() {
    const people = [
      { name: "小红", mic: true, cam: false },
      { name: "阿杰", mic: false, cam: false },
      { name: "Lily", mic: true, cam: false },
      { name: "老王", mic: true, cam: false },
      { name: "小雨", mic: false, cam: false },
    ].slice(0, DEMO_PEERS);
    people.forEach((p, i) => setTimeout(() => {
      addTile(`demo${i}`, { name: `${p.name}（演示）`, mic: p.mic, cam: p.cam });
      sysMsg(`${p.name} 加入了房间`);
      if (i === 0) setTimeout(() => addMsg({ name: p.name, text: "哈喽，能听到吗？" }), 1200);
    }, 900 + i * 500));
    // 让“说话中”的绿色边框轮流亮，展示效果
    setInterval(() => {
      const tiles = [...$("grid").querySelectorAll(".tile")];
      tiles.forEach((t) => t.classList.remove("speaking"));
      const speaking = tiles[Math.floor(Math.random() * tiles.length)];
      if (speaking && tiles.length > 1) speaking.classList.add("speaking");
    }, 2500);
  }

  // ---------- 启动 ----------
  const fromLink = (params.get("room") || "").toLowerCase();
  if (validCode(fromLink)) $("codeInput").value = fromLink;
  openMedia();
})();
