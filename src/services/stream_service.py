"""流式 ask 服务。"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from typing import Any, AsyncIterator

import httpx

from ..config import Settings
from ..models import AskStreamRequest, ChatPrepareRequest
from .cookie_service import CookieService

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)


class StreamService:
    """Accio 流式转发服务。作者：liusheng，时间：2026-04-03"""

    def __init__(self, settings: Settings, cookie_service: CookieService) -> None:
        """初始化流式服务。作者：liusheng，时间：2026-04-03"""

        self.settings = settings
        self.cookie_service = cookie_service

    def build_prepared_body(self, request: ChatPrepareRequest) -> dict[str, Any]:
        """从聊天消息推导真实 ask 模板。作者：liusheng，时间：2026-04-03"""

        user_messages = [item.content for item in request.messages if item.role == "user" and item.content.strip()]
        if not user_messages:
            raise ValueError("messages 中至少需要一条 user 消息。")
        system_messages = [item.content for item in request.messages if item.role == "system" and item.content.strip()]
        body = dict(request.extra_body)
        if request.conversation_id:
            body.setdefault("conversationId", request.conversation_id)
        if request.request_id:
            body.setdefault("requestId", request.request_id)
        if request.round_id:
            body.setdefault("roundId", request.round_id)
        body.setdefault("question", user_messages[-1])
        body.setdefault("components", "[]")
        body.setdefault("requestType", "ask")
        body.setdefault("askFrom", request.ask_from or self.settings.default_ask_from)
        body.setdefault("questionType", request.question_type or self.settings.default_question_type)
        body.setdefault("apiVersion", "7.0")
        body.setdefault("modelType", request.model_type or self.settings.default_model_type)
        body.setdefault("isNewAsk", "conversationId" not in body)
        body.setdefault("useCredits", True)
        if system_messages:
            body.setdefault("instruct", "\n".join(system_messages))
        return body

    async def build_stream_body(
        self,
        request: AskStreamRequest,
        cookie_header: str,
        client: httpx.AsyncClient,
    ) -> tuple[dict[str, Any], str]:
        """组装真实 ask 请求体。作者：liusheng，时间：2026-04-03"""

        body = dict(request.body)
        if "question" not in body or not str(body.get("question", "")).strip():
            raise ValueError("ask 请求缺少 question。")
        conversation_id = request.conversation_id or body.get("conversationId")
        enriched_cookie = await self.warmup_cookie(cookie_header, conversation_id, client)
        cookie_map = self.cookie_service.parse_cookie_header(enriched_cookie)
        request_id = request.request_id or body.get("requestId") or self.generate_request_id()
        round_id = request.round_id or body.get("roundId") or str(uuid.uuid4())
        channel_id = request.channel_id or body.get("channelId") or f"CHAT_AI#{request_id}"
        dmtrack_pageid = body.get("dmtrackPageId") or self.generate_dmtrack_pageid()
        cna = str(body.get("cna") or cookie_map.get("cna") or self.generate_device_id())
        device_id = str(body.get("deviceId") or cna)
        page_id = str(body.get("pageId") or f"{dmtrack_pageid}_{request_id}")
        language = str(body.get("language") or request.language or self.resolve_language(cookie_map))
        currency = str(body.get("currency") or request.currency or self.resolve_currency(cookie_map))
        country = str(body.get("country") or self.resolve_user_country(cookie_map))
        deliver_to_country = str(body.get("deliverToCountry") or self.resolve_deliver_to_country(cookie_map))
        merged_cookie = self.ensure_cookie_value(enriched_cookie, "cna", cna)

        body.setdefault("components", "[]")
        body.setdefault("deliverToCountry", deliver_to_country)
        body.setdefault("country", country)
        body.setdefault("language", language)
        body.setdefault("channelId", channel_id)
        body.setdefault("deviceType", "pc")
        body.setdefault("pageId", page_id)
        body.setdefault("scene", "BA")
        body.setdefault("deviceId", device_id)
        body.setdefault("cna", cna)
        body.setdefault("currency", currency)
        body.setdefault("requestId", request_id)
        if conversation_id:
            body["conversationId"] = conversation_id
        body.setdefault("isNewAsk", not bool(conversation_id))
        body.setdefault("requestType", "ask")
        body.setdefault("auth", "member")
        body.setdefault("isChildCid", False)
        body.setdefault("askFrom", self.settings.default_ask_from)
        body.setdefault("apiVersion", "7.0")
        body.setdefault("useCredits", True)
        body.setdefault("roundId", round_id)
        body.setdefault("modelType", self.settings.default_model_type)
        body.setdefault("questionType", self.settings.default_question_type)
        if request.instruct:
            body.setdefault("instruct", request.instruct)
        return body, merged_cookie

    async def warmup_cookie(
        self,
        cookie_header: str,
        conversation_id: str | None,
        client: httpx.AsyncClient,
    ) -> str:
        """访问一次页面，尽量补齐运行时 Cookie。作者：liusheng，时间：2026-04-03"""

        page_url = self.build_page_url(conversation_id)
        response = await client.get(
            page_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "identity",
                "User-Agent": USER_AGENT,
                "Referer": f"{self.settings.accio_origin}/",
                "Cookie": cookie_header,
            },
        )
        return self.cookie_service.merge_set_cookie_headers(
            cookie_header,
            response.headers.get_list("set-cookie"),
        )

    def build_page_url(self, conversation_id: str | None) -> str:
        """构造会话页面 URL。作者：liusheng，时间：2026-04-03"""

        if conversation_id:
            return f"{self.settings.accio_origin}/c/{conversation_id}"
        return f"{self.settings.accio_origin}/"

    def build_subscribe_query(
        self,
        request: AskStreamRequest,
        ask_body: dict[str, Any],
    ) -> dict[str, str]:
        """构造 SSE 订阅参数。作者：liusheng，时间：2026-04-03"""

        return {
            "_v": str(int(time.time() * 1000)),
            "appkey": self.settings.stream_app_key,
            "channelId": str(ask_body["channelId"]),
            "conversationId": str(ask_body.get("conversationId", "")),
            "currency": str(ask_body["currency"]),
            "deviceId": str(ask_body["deviceId"]),
            "instruct": str(ask_body.get("instruct", "")),
            "jsonPatch": str(request.json_patch).lower(),
            "language": str(ask_body["language"]),
            "requestId": str(ask_body["requestId"]),
            "roundId": str(ask_body["roundId"]),
            "sequence": "0",
        }

    async def send_ask(
        self,
        client: httpx.AsyncClient,
        ask_body: dict[str, Any],
        cookie_header: str,
    ) -> tuple[dict[str, Any], str, httpx.Response]:
        """发送真实 ask 请求。作者：liusheng，时间：2026-04-03"""

        conversation_id = ask_body.get("conversationId")
        response = await client.post(
            self.settings.ask_url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "identity",
                "Content-Type": "application/json",
                "Origin": self.settings.accio_origin,
                "Referer": self.build_page_url(str(conversation_id) if conversation_id else None),
                "User-Agent": USER_AGENT,
                "Cookie": cookie_header,
            },
            json=ask_body,
        )
        merged_cookie = self.cookie_service.merge_set_cookie_headers(
            cookie_header,
            response.headers.get_list("set-cookie"),
        )
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = response.json()
        else:
            payload = {"raw_text": response.text}
        return payload, merged_cookie, response

    @staticmethod
    def merge_ask_ack(
        ask_body: dict[str, Any],
        ask_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """把 ask 确认响应里的关键字段并回请求上下文。作者：liusheng，时间：2026-04-03"""

        merged = dict(ask_body)
        data = ask_payload.get("data")
        if isinstance(data, dict):
            for key in ("conversationId", "requestId", "roundId", "channelId"):
                value = data.get(key)
                if value:
                    merged[key] = value
        return merged

    @staticmethod
    def is_ask_accepted(response: httpx.Response, payload: dict[str, Any]) -> bool:
        """判断 ask 是否被服务端接受。作者：liusheng，时间：2026-04-03"""

        if response.status_code >= 400:
            return False
        if not payload:
            return True
        if payload.get("success") is False:
            return False
        code = payload.get("code")
        return code in (None, "", 0, "0", 200, "200", "SUCCESS", "success")

    async def proxy_stream(
        self,
        request: AskStreamRequest,
        cookie_header: str,
    ) -> AsyncIterator[bytes]:
        """把上游 Accio 流原样转发给客户端。作者：liusheng，时间：2026-04-03"""

        timeout = httpx.Timeout(
            timeout=None,
            connect=self.settings.stream_connect_timeout,
        )
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            trust_env=False,
        ) as client:
            body, warmed_cookie = await self.build_stream_body(request, cookie_header, client)
            ask_payload, ask_cookie, ask_response = await self.send_ask(client, body, warmed_cookie)
            if not self.is_ask_accepted(ask_response, ask_payload):
                payload = {
                    "stage": "ask",
                    "status_code": ask_response.status_code,
                    "body": ask_payload,
                    "request_body": body,
                }
                yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
                return
            stream_body = self.merge_ask_ack(body, ask_payload)
            subscribe_query = self.build_subscribe_query(request, stream_body)
            async with client.stream(
                "GET",
                self.settings.stream_subscribe_url,
                headers={
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "Referer": f"{self.settings.accio_origin}/",
                    "User-Agent": USER_AGENT,
                },
                params=subscribe_query,
            ) as response:
                if response.status_code >= 400:
                    error_text = await response.aread()
                    payload = {
                        "stage": "subscribe",
                        "status_code": response.status_code,
                        "body": error_text.decode("utf-8", errors="replace"),
                        "request_body": stream_body,
                        "ask_response": ask_payload,
                        "subscribe_query": subscribe_query,
                    }
                    yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
                    return
                async for chunk in response.aiter_bytes():
                    if chunk:
                        yield chunk

    @staticmethod
    def parse_kv_cookie_value(raw_value: str) -> dict[str, str]:
        """解析 cookie 中的 key=value&key2=value2 结构。作者：liusheng，时间：2026-04-03"""

        result: dict[str, str] = {}
        for item in raw_value.split("&"):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            if key:
                result[key] = value
        return result

    def resolve_language(self, cookie_map: dict[str, str]) -> str:
        """优先从 cookie 中推导语言。作者：liusheng，时间：2026-04-03"""

        cfg = self.parse_kv_cookie_value(cookie_map.get("sc_g_cfg_f", ""))
        return cfg.get("sc_b_locale", self.settings.default_language)

    def resolve_currency(self, cookie_map: dict[str, str]) -> str:
        """优先从 cookie 中推导币种。作者：liusheng，时间：2026-04-03"""

        cfg = self.parse_kv_cookie_value(cookie_map.get("sc_g_cfg_f", ""))
        return cfg.get("sc_b_currency", self.settings.default_currency)

    def resolve_deliver_to_country(self, cookie_map: dict[str, str]) -> str:
        """优先从 cookie 中推导发货国家。作者：liusheng，时间：2026-04-03"""

        cfg = self.parse_kv_cookie_value(cookie_map.get("sc_g_cfg_f", ""))
        return cfg.get("sc_b_site", self.settings.default_deliver_to_country)

    def resolve_user_country(self, cookie_map: dict[str, str]) -> str:
        """优先从 cookie 中推导账号国家。作者：liusheng，时间：2026-04-03"""

        user_cfg = self.parse_kv_cookie_value(cookie_map.get("xman_us_f", ""))
        x_user = user_cfg.get("x_user", "")
        return x_user.split("|", 1)[0] or self.settings.default_country

    @staticmethod
    def generate_request_id() -> str:
        """生成与前端格式接近的 requestId。作者：liusheng，时间：2026-04-03"""

        random_seed = uuid.uuid4().int % 10**13
        return f"AI_Web_{random_seed}_{int(time.time() * 1000)}"

    @staticmethod
    def generate_dmtrack_pageid() -> str:
        """生成与页面埋点格式接近的 pageId 前缀。作者：liusheng，时间：2026-04-03"""

        return uuid.uuid4().hex + uuid.uuid4().hex[:8]

    @staticmethod
    def generate_device_id() -> str:
        """生成与 cna 形态接近的运行时设备 ID。作者：liusheng，时间：2026-04-03"""

        return base64.b64encode(os.urandom(18)).decode("ascii").rstrip("=")

    def ensure_cookie_value(
        self,
        cookie_header: str,
        key: str,
        value: str,
    ) -> str:
        """确保 Cookie Header 中存在指定键值。作者：liusheng，时间：2026-04-03"""

        cookie_map = self.cookie_service.parse_cookie_header(cookie_header)
        cookie_map[key] = value
        return "; ".join(f"{name}={item_value}" for name, item_value in cookie_map.items())
