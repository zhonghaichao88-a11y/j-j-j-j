"""全自动：补齐视频 → 用 social-auto-upload 上传到抖音并设好每天的定时发布。

每次运行：把未来 7 天里还空着的日子排满（每天 1 条，晚上 19:00 发布）。
建议每周自动运行一次（见 设置每周自动运行.bat）。
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
NOVEL = HERE / "小说推文"
VIDEOS = NOVEL / "成品视频"
STATE = HERE / "发布记录.json"
LOG = HERE / "发布日志.txt"

ACCOUNT = "main"                           # 自动发布工具里的账号代号，随便起
PUBLISH_HOURS = [12, 19]                   # 每天几点发（一天两条：中午和晚上）
ACTIVITY_TAGS = []                         # 想蹭的活动话题，例如 ["我的追秋实况"]，会加到每条作品
DAYS_AHEAD = 7                             # 抖音定时发布最远约一周
DECLARATION = "内容由AI生成"


def sau_cmd():
    if os.environ.get("SAU_CMD"):  # 测试用
        return os.environ["SAU_CMD"].split()
    for p in [HERE.parent / "social-auto-upload" / ".venv" / "Scripts" / "sau.exe",
              HERE.parent / "social-auto-upload" / ".venv" / "bin" / "sau"]:
        if p.exists():
            return [str(p)]
    raise SystemExit("没找到自动发布工具，请先双击「安装自动发布.bat」")


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M}] {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"published": {}}


def save_state(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def read_meta(video):
    """从同名 .txt 读出标题和话题。"""
    title, tags = video.stem, []
    for line in video.with_suffix(".txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("【发布标题】"):
            title = line[len("【发布标题】"):].strip()
        elif line.startswith("【话题】"):
            tags = [t.strip() for t in line[len("【话题】"):].split("#") if t.strip()]
    return title, tags


def pending_videos(state):
    """先把素材里新加的书切集生成，再按书名、集数顺序返回没发过的视频。"""
    sys.path.insert(0, str(NOVEL))
    import novel_video
    for v in novel_video.build_all():
        log(f"生成 {v.name}")
    return [v for v in sorted(VIDEOS.glob("*.mp4"))
            if v.name not in state["published"] and not v.name.startswith("示例")]


def free_slots(state, now):
    taken = set(state["published"].values())
    slots = []
    for d in range(0, DAYS_AHEAD + 1):
        for h in PUBLISH_HOURS:
            t = (now + timedelta(days=d)).replace(hour=h, minute=0, second=0, microsecond=0)
            if t > now + timedelta(hours=2) and t <= now + timedelta(days=DAYS_AHEAD) \
                    and t.strftime("%Y-%m-%d %H:%M") not in taken:
                slots.append(t)
    return slots


def upload(video, when):
    title, tags = read_meta(video)
    tags = list(dict.fromkeys(tags + ACTIVITY_TAGS))
    cmd = sau_cmd() + ["douyin", "upload-video", "--account", ACCOUNT, "--file", str(video),
                       "--title", title, "--tags", ",".join(tags),
                       "--schedule", when.strftime("%Y-%m-%d %H:%M"), "--declaration", DECLARATION]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log(f"上传失败 {video.name}：{(r.stderr or r.stdout).strip()[-500:]}")
    return r.returncode == 0


def main():
    state = load_state()
    videos = pending_videos(state)
    slots = free_slots(state, datetime.now())
    if not videos:
        log("没有待发布的视频：请把新的授权小说放进 小说推文/素材/")
        return
    if not slots:
        log("未来 7 天都已排满，无需操作")
        return
    ok = 0
    for video, when in zip(videos, slots):
        if not upload(video, when):
            log("出错后停止。常见原因：登录过期（双击「登录抖音.bat」重新扫码）或需要短信验证。")
            break
        state["published"][video.name] = when.strftime("%Y-%m-%d %H:%M")
        save_state(state)
        log(f"已排期 {video.name} → {when:%m-%d %H:%M} 发布")
        ok += 1
    log(f"本次完成 {ok} 条，剩余待发 {len(videos) - ok} 条")
    if len(videos) - ok < len(PUBLISH_HOURS) * DAYS_AHEAD:
        log("提醒：存货不到一周，记得往 小说推文/素材/ 加新书")


if __name__ == "__main__":
    main()
