"""Accio2API 配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class Settings:
    """运行配置。作者：liusheng，时间：2026-04-03"""

    server_host: str
    server_port: int
    accio_origin: str
    mtop_base_url: str
    ask_url: str
    stream_subscribe_url: str
    app_key: str
    stream_app_key: str
    mtop_jsv: str
    request_timeout: float
    stream_connect_timeout: float
    default_cookie: str
    cookie_file: str
    default_browser: str
    default_language: str
    default_currency: str
    default_country: str
    default_deliver_to_country: str
    default_ask_from: str
    default_model_type: str
    default_question_type: str


def load_settings() -> Settings:
    """加载环境变量配置。作者：liusheng，时间：2026-04-03"""

    accio_host = os.getenv("ACCIO_HOST", "zh.accio.com").strip()
    accio_main_domain = ".".join(accio_host.split(".")[-2:])
    mtop_host = os.getenv("ACCIO_MTOP_HOST", f"acs.h.{accio_main_domain}").strip()
    return Settings(
        server_host=os.getenv("ACCIO2API_HOST", "0.0.0.0").strip(),
        server_port=int(os.getenv("ACCIO2API_PORT", "8091")),
        accio_origin=f"https://{accio_host}",
        mtop_base_url=f"https://{mtop_host}",
        ask_url=f"https://{accio_host}/api/ask",
        stream_subscribe_url=os.getenv(
            "ACCIO_STREAM_SUBSCRIBE_URL",
            "https://stream.accio.com/message/subscribe",
        ).strip(),
        app_key=os.getenv("ACCIO_APP_KEY", "24889839").strip(),
        stream_app_key=os.getenv("ACCIO_STREAM_APP_KEY", "50005").strip(),
        mtop_jsv=os.getenv("ACCIO_MTOP_JSV", "2.7.2").strip(),
        request_timeout=float(os.getenv("ACCIO_REQUEST_TIMEOUT", "30")),
        stream_connect_timeout=float(os.getenv("ACCIO_STREAM_CONNECT_TIMEOUT", "15")),
        default_cookie=os.getenv("ACCIO_COOKIE", "").strip(),
        cookie_file=os.getenv("ACCIO_COOKIE_FILE", "").strip(),
        default_browser=os.getenv("ACCIO_BROWSER", "edge").strip().lower(),
        default_language=os.getenv("ACCIO_LANGUAGE", "zh_CN").strip(),
        default_currency=os.getenv("ACCIO_CURRENCY", "USD").strip(),
        default_country=os.getenv("ACCIO_COUNTRY", "US").strip(),
        default_deliver_to_country=os.getenv("ACCIO_DELIVER_TO_COUNTRY", "US").strip(),
        default_ask_from=os.getenv("ACCIO_ASK_FROM", "conversation").strip(),
        default_model_type=os.getenv("ACCIO_MODEL_TYPE", "fast").strip(),
        default_question_type=os.getenv("ACCIO_QUESTION_TYPE", "v3_search").strip(),
    )


settings = load_settings()
