"""SeeU 后端入口。

开发启动（在 video_chat 目录下）:
  uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload
然后浏览器打开 http://localhost:8000
"""
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import asyncio
from contextlib import asynccontextmanager

from . import api_account, api_admin, api_chat, api_game, api_social, config, push
from .db import init_db

logging.basicConfig(level=logging.INFO)
init_db()
config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(api_game.settle_loop())   # 陪玩订单超时处理
    push.vapid_key()
    yield
    task.cancel()


app = FastAPI(title="SeeU API", docs_url="/api/docs" if config.DEV_MODE else None, redoc_url=None, lifespan=lifespan)
# App 内嵌网页（Capacitor）的来源，以及本地开发
app.add_middleware(CORSMiddleware, allow_origins=["capacitor://localhost", "http://localhost", "https://localhost"],
                   allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?", allow_methods=["*"], allow_headers=["*"])

for r in (api_account.router, api_admin.router, api_social.router, api_chat.router, api_game.router, push.router):
    app.include_router(r)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logging.exception("unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "服务器开小差了，请稍后再试"}, status_code=500)


@app.get("/api/health")
def health():
    return {"ok": True}


app.mount("/uploads", StaticFiles(directory=config.UPLOAD_DIR), name="uploads")
DOWNLOAD_DIR = config.BASE.parent / "download"
if DOWNLOAD_DIR.exists():
    app.mount("/download", StaticFiles(directory=DOWNLOAD_DIR, html=True), name="download")
app.mount("/", StaticFiles(directory=config.APP_DIR, html=True), name="app")
