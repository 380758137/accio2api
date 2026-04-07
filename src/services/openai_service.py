"""OpenAI 兼容适配服务。作者：liusheng，时间：2026-04-07"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator

from ..models import AskStreamRequest, ChatMessage, ChatPrepareRequest
from .cookie_service import CookieService
from .stream_service import StreamService

STREAM_END_PATH = "/data/streamEnd"
MSG_STREAM_END_PATH = "/data/round/messages/0/streamEnd"


class OpenAIService:
    """将 Accio 流式响应转为 OpenAI chat completions 格式。作者：liusheng，时间：2026-04-07"""

    def __init__(self, stream_service: StreamService, cookie_service: CookieService) -> None:
        """初始化服务。作者：liusheng，时间：2026-04-07"""
        self.stream_service = stream_service
        self.cookie_service = cookie_service

    @staticmethod
    def parse_sse_events(raw: str) -> list[dict[str, str]]:
        """从原始 SSE 文本中解析出事件列表（按空行分块）。作者：liusheng，时间：2026-04-07"""
        events: list[dict[str, str]] = []
        for block in raw.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            current: dict[str, str] = {}
            for line in block.split("\n"):
                if line.startswith("id:"):
                    current["id"] = line[3:].strip()
                elif line.startswith("event:"):
                    current["event"] = line[6:].strip()
                elif line.startswith("data:"):
                    current["data"] = line[5:]
            if current:
                events.append(current)
        return events

    @staticmethod
    def extract_summary_delta(patches: list[dict[str, Any]], prev_summary: str) -> tuple[str, str, bool]:
        """从 JSON Patch 中提取 summary 增量文本和流结束标志。作者：liusheng，时间：2026-04-07

        支持两种 Accio 返回结构：
        - 直接问答：path 为 /data/round/messages/0/content/0/data/0/data/summary
        - 搜索类：summary 嵌套在 steps 或 content 内的 add 操作 value 中

        Returns:
            (delta_text, full_summary, stream_ended)
        """
        current_summary = prev_summary
        stream_ended = False
        for patch in patches:
            path = patch.get("path", "")
            op = patch.get("op", "")
            value = patch.get("value")
            # 直接 summary path（问答类）
            if "summary" in path and op in ("add", "replace") and isinstance(value, str):
                current_summary = value
            # steps/content 中的 add 操作（搜索类），value 是包含 summary 的字典
            elif op == "add" and isinstance(value, dict):
                summary = OpenAIService._dig_summary(value)
                if summary:
                    current_summary = current_summary + summary if current_summary else summary
            if path in (STREAM_END_PATH, MSG_STREAM_END_PATH) and value is True:
                stream_ended = True
        delta = current_summary[len(prev_summary):]
        return delta, current_summary, stream_ended

    @staticmethod
    def _dig_summary(obj: Any) -> str:
        """递归从嵌套结构中提取 summary 文本。作者：liusheng，时间：2026-04-07"""
        if not isinstance(obj, dict):
            return ""
        # 直接有 summary 字段
        data = obj.get("data")
        if isinstance(data, dict) and "summary" in data:
            return str(data["summary"])
        # contentType == summary
        if obj.get("contentType") == "summary" and isinstance(data, dict):
            return str(data.get("summary", ""))
        return ""

    @staticmethod
    def _extract_text(raw: Any) -> str:
        """从 content 中提取纯文本，过滤 system-reminder 标签。作者：liusheng，时间：2026-04-07"""
        if isinstance(raw, list):
            parts = []
            for b in raw:
                if isinstance(b, dict) and b.get("type") == "text":
                    text = b.get("text", "")
                    if not text.strip().startswith("<system-reminder>"):
                        parts.append(text)
            return "\n".join(parts)
        text = str(raw)
        if text.strip().startswith("<system-reminder>"):
            return ""
        return text

    def build_chat_request(self, messages: list[dict[str, str]], **kwargs: Any) -> ChatPrepareRequest:
        """将 OpenAI/Anthropic messages 转为 ChatPrepareRequest。作者：liusheng，时间：2026-04-07"""
        chat_messages = []
        for m in messages:
            role = m.get("role", "user")
            content = self._extract_text(m.get("content", ""))
            if content.strip():
                chat_messages.append(ChatMessage(role=role, content=content))
        if not chat_messages:
            chat_messages.append(ChatMessage(role="user", content="hello"))
        return ChatPrepareRequest(messages=chat_messages, **kwargs)

    async def chat_completions_stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        cookie_header: str,
    ) -> AsyncIterator[bytes]:
        """流式返回 OpenAI 格式的 SSE。作者：liusheng，时间：2026-04-07"""
        chat_req = self.build_chat_request(messages)
        body = self.stream_service.build_prepared_body(chat_req)
        stream_req = AskStreamRequest(body=body)

        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())

        # 先发 role chunk
        role_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(role_chunk, ensure_ascii=False)}\n\n".encode()

        prev_summary = ""
        buffer = ""
        async for raw_chunk in self.stream_service.proxy_stream(stream_req, cookie_header):
            buffer += raw_chunk.decode("utf-8", errors="replace")
            # 只处理到最后一个完整事件块（以 \n\n 结尾）
            last_sep = buffer.rfind("\n\n")
            if last_sep < 0:
                continue
            complete_part = buffer[:last_sep + 2]
            buffer = buffer[last_sep + 2:]
            events = self.parse_sse_events(complete_part)

            for event in events:
                if event.get("event") != "message":
                    continue
                try:
                    data = json.loads(event.get("data", "{}"))
                except json.JSONDecodeError:
                    continue
                msg_body_raw = data.get("msgBody")
                if not msg_body_raw:
                    continue
                try:
                    patches = json.loads(msg_body_raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(patches, list):
                    continue

                delta, prev_summary, stream_ended = self.extract_summary_delta(patches, prev_summary)

                if delta:
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()

                if stream_ended:
                    stop_chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    }
                    yield f"data: {json.dumps(stop_chunk, ensure_ascii=False)}\n\n".encode()
                    yield b"data: [DONE]\n\n"
                    return

        # 兜底：流结束但没收到 streamEnd
        if prev_summary:
            stop_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(stop_chunk, ensure_ascii=False)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    async def anthropic_stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        cookie_header: str,
    ) -> AsyncIterator[bytes]:
        """流式返回 Anthropic Messages API 格式的 SSE。作者：liusheng，时间：2026-04-07"""
        chat_req = self.build_chat_request(messages)
        body = self.stream_service.build_prepared_body(chat_req)
        stream_req = AskStreamRequest(body=body)

        msg_id = f"msg_{uuid.uuid4().hex[:24]}"

        # message_start
        start_event = {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        }
        yield f"event: message_start\ndata: {json.dumps(start_event, ensure_ascii=False)}\n\n".encode()

        # content_block_start
        block_start = {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
        yield f"event: content_block_start\ndata: {json.dumps(block_start, ensure_ascii=False)}\n\n".encode()

        yield b"event: ping\ndata: {\"type\": \"ping\"}\n\n"

        prev_summary = ""
        buffer = ""
        async for raw_chunk in self.stream_service.proxy_stream(stream_req, cookie_header):
            buffer += raw_chunk.decode("utf-8", errors="replace")
            last_sep = buffer.rfind("\n\n")
            if last_sep < 0:
                continue
            complete_part = buffer[:last_sep + 2]
            buffer = buffer[last_sep + 2:]
            events = self.parse_sse_events(complete_part)

            for event in events:
                if event.get("event") != "message":
                    continue
                try:
                    data = json.loads(event.get("data", "{}"))
                except json.JSONDecodeError:
                    continue
                msg_body_raw = data.get("msgBody")
                if not msg_body_raw:
                    continue
                try:
                    patches = json.loads(msg_body_raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(patches, list):
                    continue

                delta, prev_summary, stream_ended = self.extract_summary_delta(patches, prev_summary)

                if delta:
                    delta_event = {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": delta},
                    }
                    yield f"event: content_block_delta\ndata: {json.dumps(delta_event, ensure_ascii=False)}\n\n".encode()

                if stream_ended:
                    block_stop = {"type": "content_block_stop", "index": 0}
                    yield f"event: content_block_stop\ndata: {json.dumps(block_stop, ensure_ascii=False)}\n\n".encode()
                    msg_delta = {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": len(prev_summary)},
                    }
                    yield f"event: message_delta\ndata: {json.dumps(msg_delta, ensure_ascii=False)}\n\n".encode()
                    msg_stop = {"type": "message_stop"}
                    yield f"event: message_stop\ndata: {json.dumps(msg_stop, ensure_ascii=False)}\n\n".encode()
                    return

        # 兜底
        block_stop = {"type": "content_block_stop", "index": 0}
        yield f"event: content_block_stop\ndata: {json.dumps(block_stop, ensure_ascii=False)}\n\n".encode()
        msg_delta = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": len(prev_summary)},
        }
        yield f"event: message_delta\ndata: {json.dumps(msg_delta, ensure_ascii=False)}\n\n".encode()
        msg_stop = {"type": "message_stop"}
        yield f"event: message_stop\ndata: {json.dumps(msg_stop, ensure_ascii=False)}\n\n".encode()

    async def anthropic_non_stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        cookie_header: str,
    ) -> dict[str, Any]:
        """非流式返回 Anthropic Messages API 格式。作者：liusheng，时间：2026-04-07"""
        full_text = ""
        async for chunk_bytes in self.anthropic_stream(messages, model, cookie_header):
            text = chunk_bytes.decode("utf-8", errors="replace")
            if "content_block_delta" in text:
                for line in text.split("\n"):
                    if line.startswith("data: "):
                        try:
                            obj = json.loads(line[6:])
                            full_text += obj.get("delta", {}).get("text", "")
                        except json.JSONDecodeError:
                            pass

        return {
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": full_text}],
            "model": model,
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": len(full_text)},
        }

    async def chat_completions(
        self,
        messages: list[dict[str, str]],
        model: str,
        cookie_header: str,
    ) -> dict[str, Any]:
        """非流式返回 OpenAI 格式响应。作者：liusheng，时间：2026-04-07"""
        full_text = ""
        async for chunk_bytes in self.chat_completions_stream(messages, model, cookie_header):
            text = chunk_bytes.decode("utf-8", errors="replace")
            if text.startswith("data: [DONE]"):
                break
            if text.startswith("data: "):
                try:
                    obj = json.loads(text[len("data: "):].strip())
                    content = (obj.get("choices") or [{}])[0].get("delta", {}).get("content", "")
                    full_text += content
                except (json.JSONDecodeError, IndexError):
                    pass

        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": full_text},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }
