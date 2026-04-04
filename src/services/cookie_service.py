"""Cookie 服务。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from ..config import Settings

try:
    import browser_cookie3
except Exception:  # pragma: no cover
    browser_cookie3 = None


class CookieService:
    """Cookie 解析服务。作者：liusheng，时间：2026-04-03"""

    def __init__(self, settings: Settings) -> None:
        """初始化 Cookie 服务。作者：liusheng，时间：2026-04-03"""

        self.settings = settings

    def resolve_cookie_header(
        self,
        cookie_override: str | None = None,
        header_cookie: str | None = None,
        browser_name: str | None = None,
    ) -> str:
        """解析最终使用的 Cookie Header。作者：liusheng，时间：2026-04-03"""

        for candidate in (cookie_override, header_cookie, self.settings.default_cookie):
            if candidate and candidate.strip():
                return candidate.strip()
        if self.settings.cookie_file:
            cookie_path = Path(self.settings.cookie_file)
            if cookie_path.exists():
                content = cookie_path.read_text(encoding="utf-8").strip()
                if content:
                    return content
        auto_cookie = self.resolve_from_browser(browser_name or self.settings.default_browser)
        if auto_cookie:
            return auto_cookie
        raise ValueError(
            "未找到可用的 Accio Cookie，请通过请求体 cookie、请求头 x-accio-cookie、"
            "环境变量 ACCIO_COOKIE、ACCIO_COOKIE_FILE，或确保本机 Edge/Chrome 已登录 Accio。"
        )

    def resolve_from_browser(self, browser_name: str | None = None) -> str:
        """从浏览器自动提取 Cookie。作者：liusheng，时间：2026-04-03"""

        if browser_cookie3 is None:
            return ""
        host = urlparse(self.settings.accio_origin).hostname or "zh.accio.com"
        candidates = self._browser_candidates(browser_name)
        last_error = None
        for candidate in candidates:
            try:
                jar = self._load_cookie_jar(candidate)
                header = self._build_cookie_header_from_jar(jar, host)
                if header:
                    return header
            except Exception as exc:  # pragma: no cover
                last_error = exc
                continue
        if last_error:
            return ""
        return ""

    def _browser_candidates(self, browser_name: str | None) -> list[str]:
        """计算浏览器候选列表。作者：liusheng，时间：2026-04-03"""

        preferred = (browser_name or "").strip().lower()
        ordered = [preferred] if preferred else []
        for name in ("edge", "chrome", "chromium"):
            if name and name not in ordered:
                ordered.append(name)
        return ordered

    @staticmethod
    def _load_cookie_jar(browser_name: str):
        """加载浏览器 CookieJar。作者：liusheng，时间：2026-04-03"""

        loaders = {
            "edge": browser_cookie3.edge,
            "chrome": browser_cookie3.chrome,
            "chromium": browser_cookie3.chromium,
        }
        if browser_name not in loaders:
            raise ValueError(f"不支持的浏览器类型: {browser_name}")
        return loaders[browser_name](domain_name="accio.com")

    @staticmethod
    def _build_cookie_header_from_jar(cookie_jar, host: str) -> str:
        """把 CookieJar 转成请求头。作者：liusheng，时间：2026-04-03"""

        cookies: list[tuple[str, str, str]] = []
        for item in cookie_jar:
            domain = item.domain.lstrip(".")
            if CookieService._domain_matches(host, domain):
                cookies.append((domain, item.name, item.value))
        cookies.sort(key=lambda item: (item[0] != host, item[0], item[1]))
        dedup: dict[str, str] = {}
        for _, name, value in cookies:
            if name not in dedup:
                dedup[name] = value
        return "; ".join(f"{name}={value}" for name, value in dedup.items())

    @staticmethod
    def _domain_matches(host: str, domain: str) -> bool:
        """判断域名是否可用于目标主机。作者：liusheng，时间：2026-04-03"""

        return host == domain or host.endswith(f".{domain}")

    @staticmethod
    def parse_cookie_header(cookie_header: str) -> dict[str, str]:
        """把 Cookie Header 解析成字典。作者：liusheng，时间：2026-04-03"""

        result: dict[str, str] = {}
        for item in cookie_header.split(";"):
            chunk = item.strip()
            if not chunk or "=" not in chunk:
                continue
            key, value = chunk.split("=", 1)
            result[key.strip()] = value.strip()
        return result

    def build_debug_summary(self, cookie_header: str) -> dict[str, object]:
        """生成 Cookie 调试摘要。作者：liusheng，时间：2026-04-03"""

        cookie_map = self.parse_cookie_header(cookie_header)
        return {
            "cookie_names": sorted(cookie_map.keys()),
            "has_mtop_token": "_m_h5_tk" in cookie_map,
            "has_cna": "cna" in cookie_map,
            "has_cookie2": "cookie2" in cookie_map,
            "has_xman_us_f": "xman_us_f" in cookie_map,
            "device_id": cookie_map.get("cna", ""),
        }

    def merge_set_cookie_headers(
        self,
        cookie_header: str,
        set_cookie_headers: list[str],
    ) -> str:
        """把响应头 Set-Cookie 合并回 Cookie Header。作者：liusheng，时间：2026-04-03"""

        cookie_map = self.parse_cookie_header(cookie_header)
        for header in set_cookie_headers:
            first_part = header.split(";", 1)[0].strip()
            if not first_part or "=" not in first_part:
                continue
            key, value = first_part.split("=", 1)
            cookie_map[key.strip()] = value.strip()
        return "; ".join(f"{name}={value}" for name, value in cookie_map.items())
