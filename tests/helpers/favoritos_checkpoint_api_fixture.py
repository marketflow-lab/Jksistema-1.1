"""Isolated stdin fixture for the real JS -> FastAPI -> SQLite checkpoint roundtrip."""
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from backend.routers.favoritos import LEGACY_FAVORITOS_ROUTES
from backend.services import favoritos_endpoints as endpoints
from backend.services import favoritos_storage as history

def main():
    with tempfile.TemporaryDirectory(prefix="jk-checkpoint-roundtrip-") as directory:
        tenant = lambda client: os.path.join(directory, client)
        endpoints.get_tenant_path = tenant
        history.get_tenant_path = tenant
        history._favoritos_usuario_slug = lambda value: re.sub(r"[^a-z0-9_-]+", "_", str(value).lower())
        endpoints._lojas_favoritos_com_ml = lambda client: [{"nome": "Loja A"}, {"nome": "Loja B"}]
        endpoints._chave_loja_favoritos = lambda value: re.sub(r"[^a-z0-9]", "", str(value).lower())
        app = FastAPI()
        async def auth(request: Request):
            request.state.username = "fixture-owner"
            return "fixture-client"
        app.dependency_overrides[endpoints.get_tenant_id] = auth
        for spec in LEGACY_FAVORITOS_ROUTES:
            if spec.endpoint_name.startswith("favoritos_checkpoints_"):
                app.add_api_route("/api/favoritos" + spec.path, getattr(endpoints, spec.endpoint_name), methods=[spec.method])
        with TestClient(app) as client:
            for line in sys.stdin:
                message = json.loads(line)
                response = client.request(message["method"], message["url"], json=message.get("body"))
                print(json.dumps({"id": message["id"], "status": response.status_code, "data": response.json()}, ensure_ascii=True), flush=True)

if __name__ == "__main__":
    main()
