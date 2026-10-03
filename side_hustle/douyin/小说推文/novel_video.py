"""小说推文视频一键生成：把授权小说的正文切成一集集竖屏视频（配音 + 大字幕 + 结尾搜书关键词）。

素材格式（放在 素材/ 文件夹，每本书一个 .txt，UTF-8）：
    关键词：xxxx              ← 推广后台给你的专属关键词（必填）
    钩子：一句吸引人的开头      ← 封面大字（可选，没有就用正文第一句）
    话题：小说推文,番茄小说      ← 额外话题（可选）
    ---
    正文……

用法：python novel_video.py            # 把 素材/ 里所有书切集生成，已生成的跳过
      python novel_video.py --no-voice # 离线测试
"""
import argparse
import asyncio
import os
import re
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
SRC = HERE / "素材"
OUT = HERE / "成品视频"
W, H, FPS = 1080, 1920, 25
VOICE = "zh-CN-YunxiNeural"        # 男声；女频书可改 zh-CN-XiaoxiaoNeural
CHARS_PER_EP = 380                 # 每集字数，约 70–90 秒
FONTS = ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
         "/System/Library/Fonts/PingFang.ttc", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]
THEMES = [((22, 24, 40), (70, 40, 90)), ((15, 30, 35), (30, 80, 90)), ((40, 18, 18), (110, 40, 40)),
          ((20, 20, 20), (70, 60, 50))]


def font(size):
    for p in FONTS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise FileNotFoundError("找不到中文字体")


# ---------- 素材解析与切集 ----------
def parse_book(path):
    raw = path.read_text(encoding="utf-8-sig")
    head, _, body = raw.partition("---")
    if not body.strip():
        raise ValueError(f"{path.name} 缺少 --- 分隔线")
    meta = {}
    for line in head.splitlines():
        m = re.match(r"\s*(关键词|钩子|话题)\s*[:：]\s*(.+)", line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
    if "关键词" not in meta:
        raise ValueError(f"{path.name} 缺少「关键词：」")
    return meta, body.strip()


def sentences(text):
    text = re.sub(r"[ \t\u3000]+", "", text)
    parts = re.findall(r"[^。！？!?…\n]+[。！？!?…”」）)』】]*", text)
    out = []
    for p in (s.strip() for s in parts):
        if not re.search(r"[\w\u4e00-\u9fff]", p):  # 只有标点的碎片并到上一句，避免配音为空
            if out:
                out[-1] += p
            continue
        while len(p) > 40:  # 太长的句子按逗号再切，保证字幕能放下
            cut = max(p.rfind(c, 0, 40) for c in "，,；;：:")
            cut = cut + 1 if cut > 8 else 40
            out.append(p[:cut]); p = p[cut:]
        if p:
            out.append(p)
    return out


def episodes(sents, size=CHARS_PER_EP):
    eps, cur, n = [], [], 0
    for s in sents:
        cur.append(s); n += len(s)
        if n >= size and s[-1] in "。！？!?…”」":  # 在句号处断集
            eps.append(cur); cur, n = [], 0
    if cur:
        if eps and n < size * 0.4:
            eps[-1].extend(cur)  # 尾巴太短就并进上一集
        else:
            eps.append(cur)
    return eps


# ---------- 画面 ----------
def background(theme):
    top, bottom = THEMES[theme % len(THEMES)]
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse([W * 0.1, H * 0.25, W * 0.9, H * 0.65], fill=tuple(min(255, c + 40) for c in bottom))
    return Image.blend(img, glow.filter(ImageFilter.GaussianBlur(160)), 0.35)


def wrap(d, text, f, max_w):
    lines, cur = [], ""
    for ch in text:
        if d.textlength(cur + ch, font=f) > max_w and ch not in "，。！？、：；”」）…,.!?:;":
            lines.append(cur); cur = ch  # 标点不放行首
        else:
            cur += ch
    return lines + ([cur] if cur else [])


def draw_center(d, text, size, y_mid, fill=(255, 255, 255), max_w=W - 160):
    f = font(size)
    lines = wrap(d, text, f, max_w)
    lh = size * 1.45
    y = y_mid - lh * len(lines) / 2
    for line in lines:
        w = d.textlength(line, font=f)
        d.text(((W - w) / 2, y), line, font=f, fill=fill, stroke_width=3, stroke_fill=(0, 0, 0))
        y += lh


def frame(bg, text, ep_label, keyword, size=68):
    img = bg.copy(); d = ImageDraw.Draw(img)
    draw_center(d, ep_label, 40, 220, (230, 200, 140))
    draw_center(d, text, size, H * 0.46)
    draw_center(d, f"番茄小说搜「{keyword}」看全文", 40, H - 300, (230, 200, 140))
    d.text((W - 190, H - 80), "AI配音", font=font(26), fill=(170, 170, 170))
    return img


def frame_cover(bg, hook, ep_label, keyword):
    img = bg.copy(); d = ImageDraw.Draw(img)
    draw_center(d, ep_label, 44, 260, (230, 200, 140))
    draw_center(d, hook, 92, H * 0.42, (255, 230, 160))
    draw_center(d, f"番茄小说搜「{keyword}」", 48, H - 330, (230, 200, 140))
    return img


def frame_end(bg, keyword):
    img = bg.copy(); d = ImageDraw.Draw(img)
    draw_center(d, "后续更精彩", 84, H * 0.33)
    draw_center(d, "打开番茄小说 搜索", 60, H * 0.47, (230, 200, 140))
    draw_center(d, f"「{keyword}」", 110, H * 0.57, (255, 230, 160))
    draw_center(d, "关注我 每天更新下一集", 52, H * 0.72)
    return img


# ---------- 配音与合成 ----------
async def tts(text, path):
    ca = os.environ.get("SSL_CERT_FILE")
    if ca and os.path.exists(ca):
        import certifi
        certifi.where = lambda: ca
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate="+15%").save(str(path))


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def render(segs, out, tmp):
    """segs: [(图片, 配音文本或None, 无配音时的秒数)]"""
    clips = []
    for k, (img, text, default) in enumerate(segs):
        png = tmp / f"{k:04d}.png"; img.save(png)
        audio, dur = None, default
        if text:
            audio = tmp / f"{k:04d}.mp3"
            asyncio.run(tts(text, audio))
            dur = duration(audio) + 0.15
        clip = tmp / f"{k:04d}.mp4"
        cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-t", str(dur), "-i", str(png)]
        cmd += ["-i", str(audio)] if audio else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        cmd += ["-filter_complex", f"[1:a]apad,atrim=0:{dur},aresample=44100,aformat=channel_layouts=stereo[a]",
                "-map", "0:v", "-map", "[a]", "-t", str(dur), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-r", str(FPS), "-c:a", "aac", "-b:a", "128k", str(clip)]
        run(cmd); clips.append(clip)
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)])


def silent_len(text):
    return max(1.5, len(text) * 0.22)


def build_book(path, voice=True):
    meta, body = parse_book(path)
    kw = meta["关键词"]
    eps = episodes(sentences(body))
    extra_tags = [t.strip() for t in re.split(r"[,，#\s]+", meta.get("话题", "")) if t.strip()]
    OUT.mkdir(parents=True, exist_ok=True)
    made = []
    for i, ep in enumerate(eps, 1):
        out = OUT / f"{path.stem}_第{i:02d}集.mp4"
        if out.exists():
            continue
        bg = background(i)
        label = f"第 {i} 集"
        hook = meta.get("钩子") if i == 1 and meta.get("钩子") else ep[0]
        segs = [(frame_cover(bg, hook, label, kw), hook if voice else None, 2.5)]
        for s in ep:
            segs.append((frame(bg, s, label, kw), s if voice else None, silent_len(s)))
        segs.append((frame_end(bg, kw), f"后续更精彩，打开番茄小说，搜索{kw}。" if voice else None, 3.0))
        tmp = Path(tempfile.mkdtemp())
        try:
            render(segs, out, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        tags = ["小说推文", "番茄小说", "小说", "每日推书"] + extra_tags
        title_hook = (meta.get("钩子") or ep[0])[:28]
        out.with_suffix(".txt").write_text(
            f"【发布标题】{title_hook}｜第{i}集，番茄小说搜「{kw}」\n"
            f"【话题】{' '.join('#' + t for t in dict.fromkeys(tags))}\n"
            f"【发布时】添加声明选「内容由AI生成」\n", encoding="utf-8")
        made.append(out)
        print("已生成:", out.name)
    return made


def build_all(voice=True):
    made = []
    for p in sorted(SRC.glob("*.txt")):
        if p.name.startswith("示例"):
            continue  # 示例素材不参与正式生成
        try:
            made += build_book(p, voice)
        except ValueError as e:
            print("跳过:", e)
    return made


def main():
    ap = argparse.ArgumentParser(description="小说推文视频生成")
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("--book", help="只生成某个素材文件（可用于示例）")
    a = ap.parse_args()
    if a.book:
        build_book(Path(a.book), not a.no_voice)
    else:
        made = build_all(not a.no_voice)
        print(f"本次新生成 {len(made)} 集")


if __name__ == "__main__":
    main()
