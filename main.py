"""Accio2API 启动入口。"""

from src.main import app
import uvicorn

from src.config import settings


if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=False,
    )
