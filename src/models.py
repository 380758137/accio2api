"""Accio2API 请求模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MtopRequest(BaseModel):
    """MTOP 通用请求。作者：liusheng，时间：2026-04-03"""

    api: str
    version: str = "1.0"
    data: dict[str, Any] = Field(default_factory=dict)
    method: Literal["GET", "POST"] = "POST"
    cookie: str | None = None
    language: str | None = None
    country: str | None = None
    currency: str | None = None
    token: str | None = None
    acl_id: str | None = None


class AskStreamRequest(BaseModel):
    """Accio 流式请求。作者：liusheng，时间：2026-04-03"""

    body: dict[str, Any] = Field(default_factory=dict)
    cookie: str | None = None
    request_id: str | None = None
    conversation_id: str | None = None
    round_id: str | None = None
    instruct: str | None = None
    language: str | None = None
    currency: str | None = None
    json_patch: bool = True
    channel_id: str | None = None


class ChatMessage(BaseModel):
    """兼容聊天消息。作者：liusheng，时间：2026-04-03"""

    role: Literal["system", "user", "assistant"]
    content: str


class ChatPrepareRequest(BaseModel):
    """最小聊天包装请求。作者：liusheng，时间：2026-04-03"""

    messages: list[ChatMessage]
    cookie: str | None = None
    conversation_id: str | None = None
    request_id: str | None = None
    round_id: str | None = None
    ask_from: str | None = None
    model_type: str | None = None
    question_type: str | None = None
    extra_body: dict[str, Any] = Field(default_factory=dict)
