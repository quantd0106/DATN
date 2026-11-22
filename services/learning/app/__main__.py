import uvicorn

from app.common.logging import server_log_config


def main() -> None:
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host="0.0.0.0",
        port=8003,
        workers=1,
        proxy_headers=False,
        access_log=False,
        log_config=server_log_config(),
    )


if __name__ == "__main__":
    main()
