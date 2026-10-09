// 演示数据。接后端后由接口返回，前端结构保持不变。
// 照片全部用占位插画（真实照片由用户上传后从服务器返回）。
window.MOCK = (() => {
  const palettes = [
    ["#ffd1dc", "#ff9ec4"], ["#c9e8ff", "#8fc6ff"], ["#ffe3c2", "#ffb38a"], ["#e3d7ff", "#b49bff"],
    ["#c8f3e3", "#7fd8c0"], ["#ffe9a8", "#ffc56b"], ["#f9d2ff", "#e09bff"], ["#d4f0ff", "#9ad7f5"],
    ["#ffd9cf", "#ff9d8a"], ["#dfe7ff", "#a7b8ff"],
  ];
  const hair = ["#3b2a26", "#2b2222", "#5a3a2c", "#1f1a1a", "#6b4632"];
  const skin = ["#ffe0cf", "#f8d4bf", "#ffe6d8", "#f3cdb6"];

  // 生成一张头像/照片占位插画（SVG data URI）
  function art(seed, variant = 0) {
    const n = Math.abs(seed * 9301 + variant * 49297) % 233280;
    const [a, b] = palettes[n % palettes.length];
    const h = hair[n % hair.length];
    const s = skin[(n >> 3) % skin.length];
    const shirt = palettes[(n >> 2) % palettes.length][1];
    const tilt = ((n % 7) - 3) * 2;
    const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 300 400'>
      <defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='${a}'/><stop offset='1' stop-color='${b}'/></linearGradient></defs>
      <rect width='300' height='400' fill='url(#g)'/>
      <circle cx='${40 + (n % 60)}' cy='${60 + (n % 40)}' r='38' fill='#fff' opacity='.25'/>
      <circle cx='${250 - (n % 50)}' cy='${110 + (n % 30)}' r='22' fill='#fff' opacity='.2'/>
      <g transform='rotate(${tilt} 150 220)'>
        <path d='M86 200 q-14 120 10 210 h108 q24-90 10-210 z' fill='${h}'/>
        <path d='M40 400 q10-110 110-120 q100 10 110 120 z' fill='${shirt}'/>
        <rect x='134' y='230' width='32' height='40' rx='12' fill='${s}'/>
        <ellipse cx='150' cy='190' rx='54' ry='64' fill='${s}'/>
        <path d='M94 190 q-4-88 56-90 q62 2 56 90 q-14-46-56-52 q-42 6-56 52 z' fill='${h}'/>
        <ellipse cx='130' cy='196' rx='5' ry='6' fill='#3a2a2a'/><ellipse cx='170' cy='196' rx='5' ry='6' fill='#3a2a2a'/>
        <ellipse cx='122' cy='214' rx='9' ry='5' fill='#ff8fa3' opacity='.45'/><ellipse cx='178' cy='214' rx='9' ry='5' fill='#ff8fa3' opacity='.45'/>
        <path d='M140 226 q10 8 20 0' stroke='#d9667c' stroke-width='4' fill='none' stroke-linecap='round'/>
      </g></svg>`;
    return `url('data:image/svg+xml;utf8,${encodeURIComponent(svg).replace(/'/g, "%27")}')`;
  }

  const names = ["豆豆飞", "九月", "蝶恋花", "Only", "苏苏", "美莲", "不忘初心", "暖心小太阳", "梧桐", "姜姜",
                 "如初", "Maybe", "允儿", "小鹿", "晚风", "柚子", "糖糖", "安然", "七七", "Lily"];
  const cities = ["沈阳市", "长沙市", "南通市", "长春市", "杭州市", "成都市", "广州市", "武汉市", "西安市", "重庆市"];
  const signs = ["喜欢旅行和猫，晚上一般都在线～", "真诚交友，不聊无聊的话题", "唱歌给你听呀", "工作累了想找人聊聊天",
                 "认真生活，慢慢变好", "每天 8 点以后在线", "爱笑的女孩运气不会太差", "声音好听，欢迎来撩"];
  const labels = [["温柔", "爱笑", "猫奴"], ["会唱歌", "夜猫子"], ["旅行", "摄影", "美食"], ["健身", "瑜伽"], ["游戏", "王者荣耀"], ["读书", "电影"]];

  const users = names.map((name, i) => ({
    id: 1000 + i,
    name,
    age: 20 + ((i * 7) % 12),
    city: cities[i % cities.length],
    rating: [4.5, 5.0, 5.0, 5.0, 5.0, 5.0, 4.9, 4.8, 5.0, 4.7][i % 10],
    price: [30, 25, 75, 40, 39, 50, 15, 20, 35, 28][i % 10],
    badge: i % 9 === 2 ? "goddess" : i % 7 === 5 ? "newbie" : i % 4 === 3 && i > 10 ? "" : "verified",
    status: i % 6 === 3 ? "busy" : i % 5 === 4 ? "offline" : "online",
    isNew: i % 7 === 5 || i > 15,
    distance: ((i * 13) % 40) / 10 + 0.3,
    sign: signs[i % signs.length],
    labels: labels[i % labels.length],
    fans: 300 + i * 137,
    calls: 80 + i * 23,
    answerRate: 88 + (i % 12),
    seed: i + 1,
    vip: i % 3 === 0,
  }));

  const posts = [
    { uid: 1007, text: "在的呢", pics: 1, time: "21:04", likes: 0, comments: [] },
    { uid: 1008, text: "所以，", pics: 1, time: "20:51", likes: 3, comments: [{ name: "晚风", text: "好看" }] },
    { uid: 1001, text: "今天的晚霞好美，有人一起看吗", pics: 3, time: "19:30", likes: 26, comments: [] },
    { uid: 1004, text: "新买的裙子～", pics: 1, time: "18:12", likes: 41, comments: [{ name: "Maybe", text: "链接求一个" }] },
    { uid: 1013, text: "周末去爬山了，累但开心", pics: 3, time: "昨天", likes: 12, comments: [] },
    { uid: 1016, text: "晚上 9 点后在线，来聊天呀", pics: 0, time: "昨天", likes: 8, comments: [] },
  ];

  const reels = [
    { uid: 1010, text: "下班回家的路上", likes: 1203, comments: 88 },
    { uid: 1002, text: "海边的风好舒服", likes: 860, comments: 52 },
    { uid: 1005, text: "今天的妆容你喜欢吗", likes: 2310, comments: 140 },
    { uid: 1012, text: "试试新学的舞", likes: 532, comments: 31 },
  ];

  const convs = [
    { uid: 1009, last: "小哥哥失眠的时候一般会做什么呀？", time: "21:04", unread: 1 },
    { uid: 1010, last: "你好，今天过得怎么样？", time: "21:03", unread: 1 },
    { uid: 1014, last: "我今天好累，想找人聊聊天", time: "10-05", unread: 1 },
    { uid: 1011, last: "你好呀，很高兴认识你", time: "10-05", unread: 1 },
    { uid: 1012, last: "晚上一起打游戏吗", time: "10-05", unread: 0 },
    { uid: 1015, last: "[礼物] 玫瑰", time: "10-03", unread: 0 },
  ];

  const gifts = [
    { id: 1, name: "玫瑰", icon: "🌹", price: 1 }, { id: 2, name: "爱心", icon: "💖", price: 10 },
    { id: 3, name: "棒棒糖", icon: "🍭", price: 20 }, { id: 4, name: "小熊", icon: "🧸", price: 52 },
    { id: 5, name: "花束", icon: "💐", price: 99 }, { id: 6, name: "钻戒", icon: "💍", price: 199 },
    { id: 7, name: "跑车", icon: "🏎️", price: 520 }, { id: 8, name: "城堡", icon: "🏰", price: 1314 },
  ];

  const packs = [
    { coins: 60, yuan: 6 }, { coins: 300, yuan: 30, bonus: 10 }, { coins: 680, yuan: 68, bonus: 40, hot: true },
    { coins: 1280, yuan: 128, bonus: 100 }, { coins: 3280, yuan: 328, bonus: 300 }, { coins: 6480, yuan: 648, bonus: 700 },
  ];

  const me = {
    id: 2265161, name: "浪漫天降", sex: "m", age: 28, city: "长沙市", sign: "认真交朋友", vip: true, level: 2,
    coins: 181, follow: 11, fans: 214, seed: 99,
  };

  return { art, users, posts, reels, convs, gifts, packs, me };
})();
