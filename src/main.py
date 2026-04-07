"""Accio2API FastAPI 应用。"""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from .config import settings
from .models import AskStreamRequest, ChatPrepareRequest, MtopRequest
from .services.cookie_service import CookieService
from .services.mtop_service import MtopService
from .services.openai_service import OpenAIService
from .services.stream_service import StreamService

cookie_service = CookieService(settings)
mtop_service = MtopService(settings, cookie_service)
stream_service = StreamService(settings, cookie_service)
openai_service = OpenAIService(stream_service, cookie_service)

app = FastAPI(
    title="Accio2API",
    description="Accio 会话与流式 ask 的轻量 2API PoC",
    version="0.1.0",
)


def resolve_cookie_or_raise(
    body_cookie: str | None,
    header_cookie: str | None,
    browser_name: str | None,
) -> str:
    """解析 Cookie，不存在时抛出 400。作者：liusheng，时间：2026-04-03"""

    try:
        return cookie_service.resolve_cookie_header(body_cookie, header_cookie, browser_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/health")
async def health() -> dict[str, Any]:
    """健康检查。作者：liusheng，时间：2026-04-03"""

    return {
        "ok": True,
        "service": "accio2api",
        "mtop_base_url": settings.mtop_base_url,
        "ask_url": settings.ask_url,
        "stream_subscribe_url": settings.stream_subscribe_url,
    }


@app.post("/api/accio/cookies/debug")
async def debug_cookies(
    cookie: str | None = None,
    x_accio_cookie: Annotated[str | None, Header(alias="x-accio-cookie")] = None,
    x_accio_browser: Annotated[str | None, Header(alias="x-accio-browser")] = None,
) -> dict[str, Any]:
    """检查 Cookie 可用性。作者：liusheng，时间：2026-04-03"""

    cookie_header = resolve_cookie_or_raise(cookie, x_accio_cookie, x_accio_browser)
    return cookie_service.build_debug_summary(cookie_header)


@app.post("/api/accio/mtop")
async def call_mtop(
    request: MtopRequest,
    x_accio_cookie: Annotated[str | None, Header(alias="x-accio-cookie")] = None,
    x_accio_browser: Annotated[str | None, Header(alias="x-accio-browser")] = None,
) -> dict[str, Any]:
    """通用 MTOP 代理。作者：liusheng，时间：2026-04-03"""

    cookie_header = resolve_cookie_or_raise(request.cookie, x_accio_cookie, x_accio_browser)
    try:
        return await mtop_service.call(request, cookie_header)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"MTOP 请求失败: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/accio/conversations/{conversation_id}/status")
async def conversation_status(
    conversation_id: str,
    cookie: str | None = None,
    x_accio_cookie: Annotated[str | None, Header(alias="x-accio-cookie")] = None,
    x_accio_browser: Annotated[str | None, Header(alias="x-accio-browser")] = None,
) -> dict[str, Any]:
    """查询会话状态。作者：liusheng，时间：2026-04-03"""

    request = MtopRequest(
        api="mtop.alibaba.intl.buyeragent.conversation.status.read",
        method="POST",
        data={"conversationId": conversation_id},
        cookie=cookie,
    )
    cookie_header = resolve_cookie_or_raise(request.cookie, x_accio_cookie, x_accio_browser)
    try:
        return await mtop_service.call(request, cookie_header)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"会话状态查询失败: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/accio/ask/prepare")
async def prepare_ask(request: ChatPrepareRequest) -> dict[str, Any]:
    """把聊天消息转换成最小 ask 请求体。作者：liusheng，时间：2026-04-03"""

    try:
        body = stream_service.build_prepared_body(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "prepared_body": body,
        "tips": [
            "这个 body 已切到真实 /api/ask 模板，会在真正发请求时继续补齐运行时字段。",
            "如果上游仍校验失败，请优先在 extra_body 覆盖 conversationId、pageId、deviceId、cna 这类字段。",
        ],
    }


@app.post("/api/accio/ask/stream")
async def ask_stream(
    request: AskStreamRequest,
    x_accio_cookie: Annotated[str | None, Header(alias="x-accio-cookie")] = None,
    x_accio_browser: Annotated[str | None, Header(alias="x-accio-browser")] = None,
) -> StreamingResponse:
    """原样转发 Accio 流式结果。作者：liusheng，时间：2026-04-03"""

    cookie_header = resolve_cookie_or_raise(request.cookie, x_accio_cookie, x_accio_browser)
    return StreamingResponse(
        stream_service.proxy_stream(request, cookie_header),
        media_type="text/event-stream",
    )


@app.post("/api/accio/chat/stream")
async def chat_stream(
    request: ChatPrepareRequest,
    x_accio_cookie: Annotated[str | None, Header(alias="x-accio-cookie")] = None,
    x_accio_browser: Annotated[str | None, Header(alias="x-accio-browser")] = None,
) -> StreamingResponse:
    """把聊天消息转换后再请求上游流。作者：liusheng，时间：2026-04-03"""

    cookie_header = resolve_cookie_or_raise(request.cookie, x_accio_cookie, x_accio_browser)
    try:
        body = stream_service.build_prepared_body(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stream_request = AskStreamRequest(body=body)
    return StreamingResponse(
        stream_service.proxy_stream(stream_request, cookie_header),
        media_type="text/event-stream",
    )


# ---- Claude Code / OpenAI 兼容接口 ----


@app.head("/")
@app.head("/v1")
@app.get("/v1")
async def head_v1() -> dict[str, Any]:
    """Claude Code 连通性检查。作者：liusheng，时间：2026-04-07"""
    return {"ok": True}


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    """返回可用模型列表（OpenAI 兼容）。作者：liusheng，时间：2026-04-07"""

    return {
        "object": "list",
        "data": [
            {
                "id": "accio",
                "object": "model",
                "created": 1700000000,
                "owned_by": "accio2api",
            },
        ],
    }


@app.post("/v1/messages")
async def anthropic_messages(
    request: dict[str, Any],
    x_api_key: Annotated[str | None, Header(alias="x-api-key")] = None,
    x_accio_cookie: Annotated[str | None, Header(alias="x-accio-cookie")] = None,
    x_accio_browser: Annotated[str | None, Header(alias="x-accio-browser")] = None,
) -> Any:
    """Anthropic Messages API 兼容接口。作者：liusheng，时间：2026-04-07"""

    messages = request.get("messages", [])
    system = request.get("system")
    if system:
        if isinstance(system, str):
            messages = [{"role": "system", "content": system}] + messages
        elif isinstance(system, list):
            sys_text = "\n".join(b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text")
            if sys_text:
                messages = [{"role": "system", "content": sys_text}] + messages
    model = request.get("model", "accio")
    stream = request.get("stream", False)
    print(f"[ACCIO] messages count={len(messages)}, last_user={[m for m in messages if m.get('role')=='user'][-1:] if messages else []}", flush=True)

    if not messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")

    cookie_header = resolve_cookie_or_raise(None, x_accio_cookie, x_accio_browser)

    if stream:
        return StreamingResponse(
            openai_service.anthropic_stream(messages, model, cookie_header),
            media_type="text/event-stream",
        )
    return await openai_service.anthropic_non_stream(messages, model, cookie_header)


@app.post("/v1/chat/completions")
async def openai_chat_completions(
    request: dict[str, Any],
    authorization: Annotated[str | None, Header()] = None,
    x_accio_cookie: Annotated[str | None, Header(alias="x-accio-cookie")] = None,
    x_accio_browser: Annotated[str | None, Header(alias="x-accio-browser")] = None,
) -> Any:
    """OpenAI 兼容的 chat completions 接口。作者：liusheng，时间：2026-04-07"""

    messages = request.get("messages", [])
    model = request.get("model", "accio")
    stream = request.get("stream", False)

    if not messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")

    cookie_header = resolve_cookie_or_raise(None, x_accio_cookie, x_accio_browser)

    if stream:
        return StreamingResponse(
            openai_service.chat_completions_stream(messages, model, cookie_header),
            media_type="text/event-stream",
        )
    return await openai_service.chat_completions(messages, model, cookie_header)
