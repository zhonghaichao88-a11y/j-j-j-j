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
VIDEOS = HERE / "成品视频"
STATE = HERE / "发布记录.json"
LOG = HERE / "发布日志.txt"

ACCOUNT = "kousuan"                        # 自动发布工具里的账号代号，随便起
GRADES = ["一年级上册", "二年级上册"]        # 轮流发布的年级
PUBLISH_HOUR = 19                          # 每天几点发
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
    return {"published": {}, "next_issue": {}, "grade_turn": 0}


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


def next_video(state):
    """按年级轮流取下一条没发过的视频；没有就现场生成。"""
    grade = GRADES[state["grade_turn"] % len(GRADES)]
    state["grade_turn"] += 1
    issue = state["next_issue"].get(grade, 1)
    state["next_issue"][grade] = issue + 1
    video = VIDEOS / f"{grade}_第{issue}期.mp4"
    if str(video.name) in state["published"]:
        return next_video(state)
    if not video.exists():
        import kousuan_video
        log(f"生成 {video.name}")
        kousuan_video.build(grade, issue)
    return video


def free_slots(state, now):
    taken = set(state["published"].values())
    slots = []
    for d in range(1, DAYS_AHEAD + 1):
        t = (now + timedelta(days=d)).replace(hour=PUBLISH_HOUR, minute=0, second=0, microsecond=0)
        if t.strftime("%Y-%m-%d %H:%M") not in taken:
            slots.append(t)
    return slots


def upload(video, when):
    title, tags = read_meta(video)
    cmd = sau_cmd() + ["douyin", "upload-video", "--account", ACCOUNT, "--file", str(video),
                       "--title", title, "--tags", ",".join(tags),
                       "--schedule", when.strftime("%Y-%m-%d %H:%M"), "--declaration", DECLARATION]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log(f"上传失败 {video.name}：{(r.stderr or r.stdout).strip()[-500:]}")
    return r.returncode == 0


def main():
    sys.path.insert(0, str(HERE))
    state = load_state()
    slots = free_slots(state, datetime.now())
    if not slots:
        log("未来 7 天都已排满，无需操作")
        return
    ok = 0
    for when in slots:
        video = next_video(state)
        if upload(video, when):
            state["published"][video.name] = when.strftime("%Y-%m-%d %H:%M")
            save_state(state)
            log(f"已排期 {video.name} → {when:%m-%d %H:%M} 发布")
            ok += 1
        else:
            # 失败的这期退回去，下次重试
            grade = video.stem.split("_")[0]
            state["next_issue"][grade] -= 1
            state["grade_turn"] -= 1
            save_state(state)
            log("出错后停止。常见原因：登录过期（双击「登录抖音.bat」重新扫码）或需要短信验证。")
            break
    log(f"本次完成 {ok}/{len(slots)} 条")


if __name__ == "__main__":
    main()
