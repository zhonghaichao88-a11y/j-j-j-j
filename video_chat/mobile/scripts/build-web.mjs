// 把 ../app 的网页复制到 www/，并写入线上后端地址。
// 用法：SEEU_API=https://api.你的域名.com npm run web
import { cpSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const api = process.env.SEEU_API || "";
if (!api) console.warn("⚠️  没有设置 SEEU_API，App 会连不上后端。例：SEEU_API=https://api.example.com npm run web");
const www = join(root, "www");
rmSync(www, { recursive: true, force: true });
mkdirSync(www);
cpSync(join(root, "..", "app"), www, { recursive: true });
writeFileSync(join(www, "config.js"), `window.SEEU_API = ${JSON.stringify(api)};\n`);
rmSync(join(www, "admin.html"), { force: true });   // 管理后台不打进 App
rmSync(join(www, "i18n"), { recursive: true, force: true });   // 翻译源文件，App 只需要生成好的 i18n.js
console.log(`已生成 www/，后端地址：${api || "(空)"}`);
