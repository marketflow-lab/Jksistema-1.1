from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


@dataclass(frozen=True)
class FrontendRouterConfig:
    base_dir: str
    static_dir: str = "static"
    img_dir: str = "img"
    chrome_extensions_dir: str = "extensoes_chrome"

    @property
    def static_path(self) -> str:
        return _resolve_path(self.base_dir, self.static_dir)

    @property
    def img_path(self) -> str:
        return _resolve_path(self.base_dir, self.img_dir)

    @property
    def chrome_extensions_path(self) -> str:
        return _resolve_path(self.base_dir, self.chrome_extensions_dir)


def _resolve_path(base_dir: str, path_value: str) -> str:
    if os.path.isabs(path_value):
        return path_value
    return os.path.join(base_dir, path_value)


def _with_no_cache(response: FileResponse) -> FileResponse:
    for key, value in NO_CACHE_HEADERS.items():
        response.headers[key] = value
    return response


def create_frontend_router(config: FrontendRouterConfig) -> APIRouter:
    router = APIRouter()

    @router.get("/")
    async def root():
        return RedirectResponse(url="/frontend_index.html")

    @router.get("/{filename}.html")
    async def serve_html(filename: str):
        static_file = os.path.join(config.static_path, f"{filename}.html")
        legacy_file = os.path.join(config.base_dir, f"{filename}.html")

        if os.path.exists(static_file):
            return _with_no_cache(FileResponse(static_file, media_type="text/html; charset=utf-8"))
        if os.path.exists(legacy_file):
            return _with_no_cache(FileResponse(legacy_file, media_type="text/html; charset=utf-8"))

        raise HTTPException(status_code=404, detail="Arquivo nao encontrado")

    @router.get("/ia-sidebar.js")
    async def servir_ia_sidebar_js():
        caminho = os.path.join(config.static_path, "ia-sidebar.js")
        if not os.path.exists(caminho):
            raise HTTPException(status_code=404, detail="Arquivo do assistente IA nao encontrado")
        return _with_no_cache(FileResponse(caminho, media_type="application/javascript; charset=utf-8"))

    return router


def mount_static_assets(app: FastAPI, config: FrontendRouterConfig) -> None:
    os.makedirs(config.static_path, exist_ok=True)
    os.makedirs(config.img_path, exist_ok=True)
    app.mount("/img", StaticFiles(directory=config.img_path), name="img")
    app.mount("/", StaticFiles(directory=config.static_path, html=True), name="static")
