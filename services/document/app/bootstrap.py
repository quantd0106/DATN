from fastapi import FastAPI

from app.modules.grants.router import internal_router
from app.modules.grants.router import router as grants_router
from app.modules.health.router import router as health_router
from app.modules.library.router import router as library_router
from app.modules.notes.router import router as notes_router
from app.modules.versions.router import router as versions_router


def register_routes(app: FastAPI) -> None:
    for router in (
        health_router,
        library_router,
        versions_router,
        grants_router,
        notes_router,
        internal_router,
    ):
        app.include_router(router)
