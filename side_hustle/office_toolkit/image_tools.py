"""图片批量压缩 / 缩放 / 转格式 / 加文字水印。

用法:
  python -m office_toolkit.image_tools 输入文件夹 输出文件夹 [--max-side 1600] [--quality 80]
        [--format jpg] [--watermark "版权所有"] [--font 字体.ttf]
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
DEFAULT_FONTS = [  # 中文水印需要中文字体
    "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]


def _font(path, size):
    for p in ([path] if path else []) + DEFAULT_FONTS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _watermark(img, text, font_path, opacity=110):
    base = img.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = _font(font_path, max(16, min(base.size) // 20))
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    margin = max(10, min(base.size) // 50)
    xy = (base.width - (r - l) - margin, base.height - (b - t) - margin)
    draw.text(xy, text, font=font, fill=(255, 255, 255, opacity),
              stroke_width=2, stroke_fill=(0, 0, 0, opacity // 2))
    return Image.alpha_composite(base, layer)


def process(src, dst_dir, max_side=None, quality=80, fmt=None, watermark=None, font=None):
    img = ImageOps.exif_transpose(Image.open(src))  # 修正手机照片方向
    if max_side and max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    if watermark:
        img = _watermark(img, watermark, font)
    ext = (fmt or Path(src).suffix.lstrip(".")).lower()
    ext = "jpg" if ext == "jpeg" else ext
    out = Path(dst_dir) / f"{Path(src).stem}.{ext}"
    if ext == "jpg":
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, "white")
            rgba = img.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        img.convert("RGB").save(out, "JPEG", quality=quality, optimize=True, progressive=True)
    elif ext == "png":
        img.save(out, "PNG", optimize=True)
    elif ext == "webp":
        img.save(out, "WEBP", quality=quality)
    else:
        img.save(out)
    return out


def batch(src_dir, dst_dir, **kw):
    dst = Path(dst_dir)
    dst.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in Path(src_dir).iterdir() if p.suffix.lower() in IMG_EXT)
    before = sum(p.stat().st_size for p in files)
    outs = [process(p, dst, **kw) for p in files]
    after = sum(p.stat().st_size for p in outs)
    return len(files), before, after


def main(argv=None):
    ap = argparse.ArgumentParser(description="图片批处理")
    ap.add_argument("src_dir"); ap.add_argument("dst_dir")
    ap.add_argument("--max-side", type=int, help="最长边像素，超过则等比缩小")
    ap.add_argument("--quality", type=int, default=80)
    ap.add_argument("--format", choices=["jpg", "png", "webp"])
    ap.add_argument("--watermark"); ap.add_argument("--font")
    a = ap.parse_args(argv)
    n, before, after = batch(a.src_dir, a.dst_dir, max_side=a.max_side, quality=a.quality,
                             fmt=a.format, watermark=a.watermark, font=a.font)
    print(f"已处理 {n} 张图片，{before/1e6:.1f}MB -> {after/1e6:.1f}MB")


if __name__ == "__main__":
    main()
