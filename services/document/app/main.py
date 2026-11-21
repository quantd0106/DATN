from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.model_registry
from app.bootstrap import register_routes
from app.common.config import get_settings
from app.common.errors import register_error_handlers


def create_app() -> FastAPI:
    app = FastAPI(title="Document Service", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
        expose_headers=["X-Correlation-ID", "Content-Disposition"],
    )
    register_error_handlers(app)
    register_routes(app)
    return app


app = create_app()
