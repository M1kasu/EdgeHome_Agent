from __future__ import annotations

from fastapi import FastAPI

from app.api import router

app = FastAPI(
    title="EdgeHome Agent",
    version="0.1.0",
    description="面向智能家居的离线优先自主服务智能体 MVP。",
)
app.include_router(router)


@app.get("/healthz", tags=["system"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}
