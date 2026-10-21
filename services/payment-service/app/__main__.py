"""`python -m app` - starts the service on HOST:PORT taken from the environment."""
import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
