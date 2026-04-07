"""调试 Accio SSE 数据结构。作者：liusheng，时间：2026-04-07"""

import asyncio
import json

from src.services.cookie_service import CookieService
from src.services.stream_service import StreamService
from src.config import settings
from src.models import AskStreamRequest, ChatPrepareRequest, ChatMessage

cs = CookieService(settings)
ss = StreamService(settings, cs)

async def test():
    """调试并打印 Accio SSE 返回结构。作者：liusheng，时间：2026-04-07"""
    cookie = cs.resolve_cookie_header()
    req = ChatPrepareRequest(messages=[ChatMessage(role="user", content="推荐几个做手机壳的工厂")])
    body = ss.build_prepared_body(req)
    stream_req = AskStreamRequest(body=body)
    full = b""
    count = 0
    async for raw in ss.proxy_stream(stream_req, cookie):
        full += raw
        count += 1
        if b'"streamEnd":true' in raw or b'"streamEnd": true' in raw:
            break
        if count > 50:
            break
    text = full.decode("utf-8", errors="replace")
    print(f"total chunks={count}, total_len={len(text)}", flush=True)
    print("RAW:", repr(text[:500]), flush=True)
    # 按空行分块解析
    blocks = text.split("\n\n")
    print(f"total blocks={len(blocks)}", flush=True)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        ev, data = "", ""
        for line in lines:
            if line.startswith("event:"): ev = line[6:]
            elif line.startswith("data:"): data = line[5:]
        if ev != "message" or not data:
            continue
        try:
            obj = json.loads(data)
            patches = json.loads(obj["msgBody"])
            for p in patches:
                val = p.get("value", "")
                val_str = json.dumps(val, ensure_ascii=False) if not isinstance(val, str) else val
                if len(val_str) > 200:
                    val_str = val_str[:200] + "..."
                print(f"  op={p['op']:8s} path={p['path']}", flush=True)
                print(f"           value={val_str}", flush=True)
            print("---", flush=True)
        except Exception as e:
            print(f"  parse error: {e}")

asyncio.run(test())
