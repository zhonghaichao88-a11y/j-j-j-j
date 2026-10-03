"""抖音「口算挑战」竖屏视频一键生成。

用法:  python kousuan_video.py --grade 二年级上册 --issue 1 [--count 10] [--seconds 5] [--no-voice]
输出:  成品视频/二年级上册_第1期.mp4  和  同名 .txt（发布标题+话题）
需要:  ffmpeg、pip install pillow edge-tts（配音需联网）
"""
import argparse
import asyncio
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "products"))
import kousuan  # noqa: E402  复用口算题题库

W, H = 1080, 1920
FPS = 25
VOICE = "zh-CN-XiaoxiaoNeural"
FONTS = ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
         "/System/Library/Fonts/PingFang.ttc", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]
BG_TOP, BG_BOTTOM = (255, 244, 214), (255, 214, 165)
INK, ACCENT, GOOD = (60, 40, 20), (230, 90, 40), (40, 160, 80)


def font(size):
    for p in FONTS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise FileNotFoundError("找不到中文字体")


def canvas():
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):  # 竖向渐变背景
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)))
    return img, d


def center(d, y, text, size, fill=INK):
    f = font(size)
    for i, line in enumerate(text.split("\n")):
        w = d.textlength(line, font=f)
        d.text(((W - w) / 2, y + i * size * 1.35), line, font=f, fill=fill)


def header(d, grade, issue, idx=None, total=None):
    center(d, 150, f"{grade[:3]}口算挑战", 64, ACCENT)
    center(d, 240, f"第 {issue} 期", 44)
    if idx:
        center(d, 1650, f"第 {idx} / {total} 题", 48)
    f = font(26)
    d.text((W - 190, H - 70), "AI配音", font=f, fill=(150, 120, 90))


def frame_intro(grade, issue, n, sec):
    img, d = canvas(); header(d, grade, issue)
    center(d, 700, f"{n} 道题\n每题 {sec} 秒", 110)
    center(d, 1100, "你能全对吗？", 96, ACCENT)
    return img


def frame_question(grade, issue, idx, total, q, left, sec):
    img, d = canvas(); header(d, grade, issue, idx, total)
    center(d, 760, q.rstrip("=").strip() + " = ?", 150 if len(q) < 14 else 110)
    # 倒计时圆 + 进度条
    cx, cy, r = W // 2, 1250, 110
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ACCENT if left <= 2 else (255, 255, 255), outline=ACCENT, width=10)
    f = font(120); s = str(left); w = d.textlength(s, font=f)
    d.text((cx - w / 2, cy - 75), s, font=f, fill=(255, 255, 255) if left <= 2 else ACCENT)
    d.rounded_rectangle([140, 1450, W - 140, 1480], 15, fill=(255, 255, 255))
    d.rounded_rectangle([140, 1450, 140 + (W - 280) * left / sec, 1480], 15, fill=ACCENT)
    return img


def frame_answer(grade, issue, idx, total, q, a):
    img, d = canvas(); header(d, grade, issue, idx, total)
    center(d, 640, q.rstrip("=").strip() + " =", 130 if len(q) < 14 else 100)
    center(d, 900, a, 200, GOOD)
    center(d, 1250, "答对了吗？", 80)
    return img


def frame_outro():
    img, d = canvas()
    center(d, 600, "你答对了几道？", 100, ACCENT)
    center(d, 850, "评论区打出你的分数", 76)
    center(d, 1150, "关注我\n每天一期 越练越快", 80)
    return img


def speak_text(q, a=None):
    t = q.rstrip("= ").replace("×", "乘").replace("÷", "除以").replace("+", "加").replace("-", "减")
    return t + "等于多少？" if a is None else a.replace("……", "余")


async def tts(text, path):
    ca = os.environ.get("SSL_CERT_FILE")
    if ca and os.path.exists(ca):  # 某些代理环境需要自定义证书，须在导入 edge_tts 前设置
        import certifi
        certifi.where = lambda: ca
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate="+5%").save(str(path))


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def build(grade, issue, count=10, sec=5, voice=True, out_dir=HERE / "成品视频"):
    if grade not in kousuan.BOOKS:
        raise SystemExit(f"年级可选: {list(kousuan.BOOKS)}")
    random.seed(f"{grade}-{issue}")
    stages = kousuan.BOOKS[grade]
    # 前几期练前面的题型，后面逐期轮换
    topic, gen = stages[(issue - 1) % len(stages)]
    probs, seen = [], set()
    while len(probs) < count:
        q, a = gen()
        if q not in seen or len(seen) > 300:
            seen.add(q); probs.append((q, a))

    tmp = Path(tempfile.mkdtemp())
    # 每段 = ([(画面, 秒数), ...], 配音文本)；一段的配音横跨该段所有画面（如读题跨过整个倒计时）
    segs = [([(frame_intro(grade, issue, count, sec), 1.8)],
             f"{count}道题，你能全对吗？")]
    for i, (q, a) in enumerate(probs, 1):
        segs.append(([(frame_question(grade, issue, i, count, q, left, sec), 1.0) for left in range(sec, 0, -1)],
                     speak_text(q)))
        segs.append(([(frame_answer(grade, issue, i, count, q, a), 2.0)], speak_text(q, a)))
    segs.append(([(frame_outro(), 3.5)], "你答对了几道？评论区打出你的分数，关注我，每天一期。"))

    clips = []
    for k, (frames, text) in enumerate(segs):
        audio = None
        if voice and text:
            audio = tmp / f"{k:04d}.mp3"
            asyncio.run(tts(text, audio))
            if len(frames) == 1:  # 单画面段按配音长度延长，避免截断
                frames = [(frames[0][0], max(frames[0][1], duration(audio) + 0.4))]
        total = sum(d for _, d in frames)
        clip = tmp / f"{k:04d}.mp4"
        cmd = ["ffmpeg", "-y"]
        for j, (img, dur) in enumerate(frames):
            png = tmp / f"{k:04d}_{j}.png"; img.save(png)
            cmd += ["-loop", "1", "-framerate", str(FPS), "-t", str(dur), "-i", str(png)]
        n = len(frames)
        cmd += ["-i", str(audio)] if audio else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        vchain = "".join(f"[{j}:v]" for j in range(n)) + f"concat=n={n}:v=1:a=0,format=yuv420p[v]"
        cmd += ["-filter_complex",
                f"{vchain};[{n}:a]apad,atrim=0:{total},aresample=44100,aformat=channel_layouts=stereo[a]",
                "-map", "[v]", "-map", "[a]", "-t", str(total), "-c:v", "libx264",
                "-r", str(FPS), "-c:a", "aac", "-b:a", "128k", str(clip)]
        run(cmd)
        clips.append(clip)

    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{grade}_第{issue}期.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)])
    probs_txt = "\n".join(f"{i}. {q} {a}" for i, (q, a) in enumerate(probs, 1))
    out.with_suffix(".txt").write_text(
        f"【发布标题】{grade[:3]}口算挑战第{issue}期｜{topic}，{count}题你家孩子能全对吗？\n"
        f"【话题】#口算 #小学数学 #{grade[:3]}数学 #口算天天练 #亲子学习\n"
        f"【发布时】打开「添加声明」→ 选「内容由AI生成」（配音为AI合成）\n\n【本期答案】\n{probs_txt}\n",
        encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def main():
    ap = argparse.ArgumentParser(description="口算挑战视频生成")
    ap.add_argument("--grade", default="二年级上册", help=f"可选: {'、'.join(kousuan.BOOKS)}")
    ap.add_argument("--issue", type=int, default=1, help="第几期（不同期题目不同）")
    ap.add_argument("--to", type=int, help="批量生成到第几期，例如 --issue 1 --to 7 生成一周")
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--seconds", type=int, default=5)
    ap.add_argument("--no-voice", action="store_true", help="不要配音（离线时用）")
    a = ap.parse_args()
    for n in range(a.issue, (a.to or a.issue) + 1):
        print("已生成:", build(a.grade, n, a.count, a.seconds, not a.no_voice))


if __name__ == "__main__":
    main()
