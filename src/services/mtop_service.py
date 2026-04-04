"""MTOP 服务。"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import httpx

from ..config import Settings
from ..models import MtopRequest
from .cookie_service import CookieService

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)


class MtopService:
    """Accio MTOP 代理服务。作者：liusheng，时间：2026-04-03"""

    def __init__(self, settings: Settings, cookie_service: CookieService) -> None:
        """初始化 MTOP 服务。作者：liusheng，时间：2026-04-03"""

        self.settings = settings
        self.cookie_service = cookie_service

    @staticmethod
    def extract_h5_token(cookie_map: dict[str, str]) -> str:
        """提取 H5 token。作者：liusheng，时间：2026-04-03"""

        raw_token = cookie_map.get("_m_h5_tk", "")
        token = raw_token.split("_", 1)[0].strip()
        if not token:
            raise ValueError("Cookie 中缺少 _m_h5_tk，无法生成 MTOP sign。")
        return token

    def build_common_data(
        self,
        request: MtopRequest,
        cookie_map: dict[str, str],
    ) -> dict[str, Any]:
        """补齐 Accio 公共请求参数。作者：liusheng，时间：2026-04-03"""

        payload = dict(request.data)
        payload.setdefault("deviceId", cookie_map.get("cna", ""))
        payload.setdefault("language", request.language or self.settings.default_language)
        payload.setdefault("country", request.country or self.settings.default_country)
        payload.setdefault("currency", request.currency or self.settings.default_currency)
        if request.acl_id and "aclId" not in payload:
            payload["aclId"] = request.acl_id
        return payload

    @staticmethod
    def is_token_expired(payload: dict[str, Any]) -> bool:
        """判断 MTOP 返回是否为 token 过期。作者：liusheng，时间：2026-04-03"""

        ret_list = payload.get("ret") or []
        return any("TOKEN_EXOIRED" in item or "TOKEN_EMPTY" in item for item in ret_list if isinstance(item, str))

    async def _send_once(
        self,
        request: MtopRequest,
        cookie_header: str,
    ) -> tuple[dict[str, Any], str, httpx.Response]:
        """执行一次 MTOP 请求。作者：liusheng，时间：2026-04-03"""

        cookie_map = self.cookie_service.parse_cookie_header(cookie_header)
        token = request.token or self.extract_h5_token(cookie_map)
        data = self.build_common_data(request, cookie_map)
        data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        timestamp = str(int(time.time() * 1000))
        sign_text = f"{token}&{timestamp}&{self.settings.app_key}&{data_json}"
        sign = hashlib.md5(sign_text.encode("utf-8")).hexdigest()
        url = f"{self.settings.mtop_base_url}/h5/{request.api}/{request.version}/"
        params = {
            "jsv": self.settings.mtop_jsv,
            "appKey": self.settings.app_key,
            "t": timestamp,
            "sign": sign,
            "api": request.api,
            "v": request.version,
            "type": "originaljson",
            "dataType": "json",
            "timeout": "20000",
            "H5Request": "true",
        }
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": self.settings.accio_origin,
            "Referer": f"{self.settings.accio_origin}/",
            "User-Agent": USER_AGENT,
            "Cookie": cookie_header,
        }
        timeout = httpx.Timeout(self.settings.request_timeout)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            trust_env=False,
        ) as client:
            if request.method == "GET":
                response = await client.get(url, params={**params, "data": data_json}, headers=headers)
            else:
                response = await client.post(url, params=params, headers=headers, data={"data": data_json})
        response.raise_for_status()
        payload = response.json()
        merged_cookie = self.cookie_service.merge_set_cookie_headers(
            cookie_header,
            response.headers.get_list("set-cookie"),
        )
        return payload, merged_cookie, response

    async def compensate_token(self, cookie_header: str) -> str:
        """请求 token 补偿接口。作者：liusheng，时间：2026-04-03"""

        compensate_request = MtopRequest(
            api="mtop.alibaba.intl.accio.token.compensate",
            version="2.0",
            method="POST",
            data={"clientType": "PC"},
        )
        _, merged_cookie, _ = await self._send_once(compensate_request, cookie_header)
        return merged_cookie

    async def call(self, request: MtopRequest, cookie_header: str) -> dict[str, Any]:
        """执行 MTOP 请求。作者：liusheng，时间：2026-04-03"""

        payload, merged_cookie, response = await self._send_once(request, cookie_header)
        retried = False
        if self.is_token_expired(payload):
            retry_cookie = merged_cookie
            if retry_cookie == cookie_header and request.api != "mtop.alibaba.intl.accio.token.compensate":
                retry_cookie = await self.compensate_token(cookie_header)
            if retry_cookie != cookie_header:
                payload, merged_cookie, response = await self._send_once(request, retry_cookie)
                retried = True
                cookie_header = retry_cookie
        cookie_map = self.cookie_service.parse_cookie_header(cookie_header)
        data = self.build_common_data(request, cookie_map)
        return {
            "request": {
                "url": str(response.request.url),
                "api": request.api,
                "version": request.version,
                "method": request.method,
                "data": data,
            },
            "response": payload,
            "normalized": self.normalize_payload(payload),
            "meta": {
                "retried_after_token_refresh": retried,
                "cookie_names": sorted(self.cookie_service.parse_cookie_header(merged_cookie).keys()),
            },
        }

    @staticmethod
    def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """归一化 MTOP 返回。作者：liusheng，时间：2026-04-03"""

        ret_list = payload.get("ret") or []
        data = payload.get("data")
        first_ret = ret_list[0] if ret_list else ""
        return {
            "ok": isinstance(first_ret, str) and first_ret.startswith("SUCCESS"),
            "ret": ret_list,
            "trace_id": payload.get("traceId"),
            "response_code": data.get("responseCode") if isinstance(data, dict) else None,
            "data_keys": sorted(data.keys()) if isinstance(data, dict) else [],
        }
