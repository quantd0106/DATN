import uvicorn

from app.common.logging import server_log_config

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8001,
        workers=1,
        proxy_headers=False,
        access_log=False,
        log_config=server_log_config(),
    )
