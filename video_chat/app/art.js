// 没有上传头像/照片时用的占位插画（按用户 ID 生成，同一个人始终一样）。
window.ART = (() => {
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

  return art;
})();
