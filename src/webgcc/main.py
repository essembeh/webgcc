import uvicorn

from .settings import settings


def main() -> None:
    uvicorn.run("webgcc.webapp:app", host=settings.host, port=settings.port)
