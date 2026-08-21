from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from agent.service import EdgeHomeAgent
from app.schemas import ChatRequest, ChatResponse, EnergyRequest


router = APIRouter(prefix="/api")
agent = EdgeHomeAgent()


@router.post("/agent/chat", response_model=ChatResponse, summary="处理智能家居自然语言指令")
@router.post("/chat", response_model=ChatResponse, include_in_schema=False)
def chat(request: ChatRequest) -> dict:
    return agent.chat(request.session_id, request.text, confirm=request.confirm)


@router.get("/home/state", summary="查询家庭或指定房间状态")
def home_state(room: str | None = Query(default=None)) -> dict:
    try:
        return agent.home_state(room)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/tasks/{task_id}", summary="查询延时任务状态")
def task_state(task_id: str) -> dict:
    task = agent.task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.post("/tasks/{task_id}/cancel", summary="取消尚未执行的延时任务")
def cancel_task(task_id: str) -> dict:
    task = agent.cancel_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.post("/energy/analyze", summary="本地确定性家庭能耗分析")
def energy_analyze(request: EnergyRequest) -> dict:
    return agent.energy.analyze(request.time_range, request.room, request.device)
