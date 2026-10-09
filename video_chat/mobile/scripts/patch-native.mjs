// 给原生工程加上摄像头、麦克风权限说明（npx cap add 之后运行一次即可，重复运行无副作用）。
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));

const manifest = join(root, "android/app/src/main/AndroidManifest.xml");
if (existsSync(manifest)) {
  let xml = readFileSync(manifest, "utf8");
  const perms = ["CAMERA", "RECORD_AUDIO", "MODIFY_AUDIO_SETTINGS", "INTERNET", "WAKE_LOCK", "VIBRATE", "POST_NOTIFICATIONS"];
  for (const p of perms) {
    const line = `<uses-permission android:name="android.permission.${p}" />`;
    if (!xml.includes(`android.permission.${p}"`)) xml = xml.replace("</manifest>", `    ${line}\n</manifest>`);
  }
  if (!xml.includes("android.hardware.camera")) {
    xml = xml.replace("</manifest>", '    <uses-feature android:name="android.hardware.camera" android:required="false" />\n</manifest>');
  }
  writeFileSync(manifest, xml);
  console.log("Android 权限已写入");
}

const plist = join(root, "ios/App/App/Info.plist");
if (existsSync(plist)) {
  let s = readFileSync(plist, "utf8");
  const keys = {
    NSCameraUsageDescription: "视频通话和视频认证需要使用摄像头",
    NSMicrophoneUsageDescription: "视频/语音通话和发送语音消息需要使用麦克风",
    NSPhotoLibraryUsageDescription: "发布动态、更换头像需要访问相册",
  };
  for (const [k, v] of Object.entries(keys)) {
    if (!s.includes(`<key>${k}</key>`)) s = s.replace("<dict>", `<dict>\n\t<key>${k}</key>\n\t<string>${v}</string>`);
  }
  if (!s.includes("<key>UIBackgroundModes</key>")) {
    s = s.replace("<dict>", "<dict>\n\t<key>UIBackgroundModes</key>\n\t<array>\n\t\t<string>audio</string>\n\t\t<string>voip</string>\n\t</array>");
  }
  writeFileSync(plist, s);
  console.log("iOS 权限说明已写入");
}
