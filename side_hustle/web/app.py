"""办公文件批处理网站：把 office_toolkit 变成任何人打开网页就能用的在线工具。

启动（在 side_hustle 目录下）:
  uvicorn web.app:app --host 0.0.0.0 --port 8000
"""
import os
import re
import shutil
import tempfile
import zipfile
from urllib.parse import quote
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.datastructures import UploadFile

from office_toolkit import excel_tools, image_tools, pdf_tools, word_merge

from . import billing

MAX_TOTAL_MB = int(os.environ.get("OFFICE_WEB_MAX_MB", "50"))
MAX_FILES = int(os.environ.get("OFFICE_WEB_MAX_FILES", "300"))
TRUST_PROXY = os.environ.get("OFFICE_WEB_TRUST_PROXY") == "1"
STATIC = Path(__file__).with_name("static")

EXCEL = {".xlsx", ".xlsm", ".xls", ".csv"}
PDF = {".pdf"}
IMAGE = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
DOCX = {".docx"}


class UserError(Exception):
    """客户能看懂并自己改正的错误（文件类型不对、列名不存在等）。"""


def _safe_filename(name, used):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", Path(name or "file").name).strip(". ") or "file"
    base, ext = os.path.splitext(name)
    candidate, i = name, 2
    while candidate.lower() in used:
        candidate = f"{base}_{i}{ext}"
        i += 1
    used.add(candidate.lower())
    return candidate


async def _save(uploads, folder, allowed, budget, used):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for up in uploads:
        name = _safe_filename(up.filename, used)
        if Path(name).suffix.lower() not in allowed:
            raise UserError(f"不支持的文件类型：{up.filename}（只接受 {', '.join(sorted(allowed))}）")
        dest = folder / name
        with open(dest, "wb") as fh:
            while chunk := await up.read(1 << 20):
                budget[0] -= len(chunk)
                if budget[0] < 0:
                    raise UserError(f"上传文件总大小超过 {MAX_TOTAL_MB}MB 上限")
                fh.write(chunk)
        paths.append(dest)
    return paths


def _zip(folder, target):
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    if not files:
        raise UserError("没有生成任何文件")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(folder).as_posix())
    return target


def _flag(form, key):
    return str(form.get(key, "")).lower() in {"1", "true", "on", "yes"}


def _int(form, key, default=None, lo=None, hi=None):
    raw = str(form.get(key, "") or "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        raise UserError(f"参数 {key} 必须是整数")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise UserError(f"参数 {key} 应在 {lo}~{hi} 之间")
    return v


def _one(paths, what):
    if len(paths) != 1:
        raise UserError(f"请上传且只上传 1 个{what}")
    return paths[0]


# 每个工具: (允许的扩展名, 处理函数)。处理函数返回 (结果文件路径, 给用户看的摘要)。
def _excel_merge(form, inp, out):
    n_files, n_rows = excel_tools.merge(inp, out / "合并结果.xlsx", add_source=not _flag(form, "no_source"))
    return out / "合并结果.xlsx", f"已合并 {n_files} 个文件，共 {n_rows} 行"


def _excel_split(form, inp, out):
    src = _one(sorted(inp.iterdir()), "表格")
    column = str(form.get("column", "")).strip()
    if not column:
        raise UserError("请填写按哪一列拆分（例如：部门）")
    res = out / "res"
    try:
        n = excel_tools.split(src, column, res, one_file=_flag(form, "one_file"))
    except KeyError as e:
        raise UserError(str(e.args[0]))
    if _flag(form, "one_file"):
        return next(res.iterdir()), f"已拆分为 {n} 个工作表"
    return _zip(res, out / "拆分结果.zip"), f"已拆分为 {n} 个文件"


def _pdf_merge(form, inp, out, order):
    n = pdf_tools.merge(order, out / "合并结果.pdf")
    return out / "合并结果.pdf", f"已按上传顺序合并 {n} 个 PDF"


def _pdf_split(form, inp, out):
    n = pdf_tools.split(_one(sorted(inp.iterdir()), "PDF"), out / "res")
    return _zip(out / "res", out / "拆分结果.zip"), f"已拆成 {n} 页"


def _pdf_pages(form, inp, out):
    spec = str(form.get("spec", "")).strip()
    if not spec:
        raise UserError("请填写要提取的页码，例如 1-3,5,8-")
    try:
        n = pdf_tools.pages(_one(sorted(inp.iterdir()), "PDF"), out / "提取结果.pdf", spec)
    except ValueError as e:
        raise UserError(f"页码格式不对：{e}")
    return out / "提取结果.pdf", f"已提取 {n} 页"


def _pdf_text(form, inp, out):
    n = pdf_tools.text(_one(sorted(inp.iterdir()), "PDF"), out / "文字.txt")
    return out / "文字.txt", f"已提取 {n} 页文字（扫描件图片里的字提取不出来）"


def _image(form, inp, out):
    fmt = str(form.get("format", "") or "").lower() or None
    if fmt not in {None, "jpg", "png", "webp"}:
        raise UserError("输出格式只能是 jpg / png / webp")
    n, before, after = image_tools.batch(
        inp, out / "res",
        max_side=_int(form, "max_side", None, 64, 10000),
        quality=_int(form, "quality", 80, 10, 95),
        fmt=fmt,
        watermark=str(form.get("watermark", "") or "").strip() or None,
    )
    return _zip(out / "res", out / "图片结果.zip"), f"已处理 {n} 张图片，{before/1e6:.2f}MB → {after/1e6:.2f}MB"


TOOLS = {
    "excel_merge": (EXCEL, _excel_merge),
    "excel_split": (EXCEL, _excel_split),
    "pdf_merge": (PDF, _pdf_merge),
    "pdf_split": (PDF, _pdf_split),
    "pdf_pages": (PDF, _pdf_pages),
    "pdf_text": (PDF, _pdf_text),
    "image": (IMAGE, _image),
}

app = FastAPI(title="办公文件批处理", docs_url=None, redoc_url=None)


def _client_ip(request):
    if TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.get("/api/quota")
def quota(request: Request, code: str = ""):
    info = {"free_left": billing.free_left(_client_ip(request)), "free_per_day": billing.FREE_PER_DAY}
    if code:
        info["code_credits"] = billing.code_credits(code)
    return info


@app.post("/api/run/{tool}")
async def run(tool: str, request: Request):
    if tool not in TOOLS and tool != "word_merge":
        raise HTTPException(404, "没有这个工具")
    form = await request.form(max_files=MAX_FILES + 2)
    work = Path(tempfile.mkdtemp(prefix="officeweb_"))
    cleanup = BackgroundTask(shutil.rmtree, work, ignore_errors=True)
    inp, out = work / "in", work / "out"
    out.mkdir(parents=True)
    budget, used = [MAX_TOTAL_MB * 1024 * 1024], set()
    who = None
    try:
        uploads = [f for f in form.getlist("files") if isinstance(f, UploadFile) and f.filename]
        if tool == "word_merge":
            tpl = form.get("template")
            data = form.get("data")
            if not isinstance(tpl, UploadFile) or not isinstance(data, UploadFile):
                raise UserError("请同时上传 Word 模板（.docx）和名单表格（.xlsx）")
            tpl_path = (await _save([tpl], inp, DOCX, budget, used))[0]
            data_path = (await _save([data], inp, EXCEL - {".csv"}, budget, used))[0]
        else:
            if not uploads:
                raise UserError("请先选择要处理的文件")
            if len(uploads) > MAX_FILES:
                raise UserError(f"一次最多 {MAX_FILES} 个文件")
            paths = await _save(uploads, inp, TOOLS[tool][0], budget, used)

        ok, who = billing.charge(_client_ip(request), str(form.get("code", "") or "") or None)
        if not ok:
            shutil.rmtree(work, ignore_errors=True)
            return JSONResponse({"error": who, "need_code": True}, status_code=402)

        if tool == "word_merge":
            name_field = str(form.get("name_field", "") or "").strip() or None
            n, missing = word_merge.run(tpl_path, data_path, out / "res", name_field=name_field)
            result = _zip(out / "res", out / "批量生成结果.zip")
            summary = f"已生成 {n} 份文档"
            if missing:
                summary += f"；注意：模板里的 {', '.join(sorted(missing))} 在表格中找不到对应列"
        elif tool == "pdf_merge":
            result, summary = _pdf_merge(form, inp, out, paths)
        else:
            result, summary = TOOLS[tool][1](form, inp, out)
        billing.log_run(tool, who, True)
    except UserError as e:
        if who:
            billing.refund(who)
            billing.log_run(tool, who, False)
        shutil.rmtree(work, ignore_errors=True)
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:  # 文件损坏等：退额度，给可读提示
        if who:
            billing.refund(who)
            billing.log_run(tool, who, False)
        shutil.rmtree(work, ignore_errors=True)
        return JSONResponse({"error": f"处理失败（额度已退回）：{type(e).__name__}: {e}"}, status_code=422)

    headers = {"X-Summary": quote(summary)}
    return FileResponse(result, filename=result.name, headers=headers, background=cleanup)


@app.get("/healthz")
def healthz():
    return {"ok": True}


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
