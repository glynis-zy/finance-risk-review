# -*- coding: utf-8 -*-
"""应用入口：启动即建表，挂载全部路由，统一 CORS。

启动：uvicorn app.main:app --reload --port 8000
Swagger：http://127.0.0.1:8000/docs
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.db.session import init_db
from app.routers import (
    admin,
    analysis,
    approvals,
    attachments,
    audit,
    auth,
    documents,
    reports,
    riskfindings,
    rules,
    sessions,
    suppliers,
    workflows,
)

app = FastAPI(
    title="财务单据智能风险审核系统",
    description="OCR 看懂 / LLM 理解 / 规则引擎判定 / LLM 润色",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ROUTERS = (
    auth.router,
    admin.router,
    documents.router,
    attachments.router,
    sessions.router,
    analysis.router,
    approvals.router,
    workflows.router,
    rules.router,
    suppliers.router,
    reports.router,
    audit.router,
    riskfindings.router,
)
for r in ROUTERS:
    app.include_router(r, prefix="/api/v1")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# 前端静态托管（原生 HTML/CSS/JS，无构建步骤）
_frontend = Path(settings.frontend_dir)
if _frontend.exists():
    app.mount("/static", StaticFiles(directory=str(_frontend)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(str(_frontend / "index.html"))
