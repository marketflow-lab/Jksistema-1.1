"""Replay publishSeller PUT bodies against the real router and read both rankings.

Input on stdin: {"category": <optional>, "publications": [{"seller": {...},
"anuncios": [...], "generation": 0}, ...]}. Output contains public item IDs
and metrics only. All database access uses a new temporary directory.
"""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.routers import pesquisa_mercado as routes
from backend.services import favoritos_endpoints
from backend.services.pesquisa_mercado import PesquisaMercadoStore


JOBS = "/api/pesquisa-mercado/jobs"
METRIC_FIELDS = (
    "id", "rank", "rankable", "status", "catalogo", "vendas", "vendasFonte",
    "vendas_exibicao", "vendas_rotulo", "vendas_limite_inferior", "vendas_estimadas",
    "media_vendas_mensal", "media_mensal_fonte", "media_mensal_rotulo",
    "media_vendas_mensal_exibicao", "data_criacao", "dataCriacaoFonte",
    "avant_metrics_version", "avant_detail_observed",
)


def fixture_owner(request: Request) -> str:
    request.state.username = "fixture-user"
    return "fixture-tenant"


def checked(response):
    if response.status_code != 200:
        raise ValueError(f"Fixture API returned HTTP {response.status_code}.")
    return response.json()


def ranking(client, job_url, seller_key=None):
    params = {"limit": 100}
    if seller_key is not None:
        params["seller_key"] = seller_key
    items = []
    while True:
        page = checked(client.get(job_url + "/ranking", params={**params, "offset": len(items)}))
        items.extend({field: item.get(field) for field in METRIC_FIELDS} for item in page["items"])
        if len(items) == page["total"]:
            return {"total": page["total"], "items": items}
        if not page["items"] or len(items) > page["total"]:
            raise ValueError("Inconsistent pagination from the fixture API.")


def replay(batch):
    if not isinstance(batch, dict) or not isinstance(batch.get("publications"), list) or not batch["publications"]:
        raise ValueError("Expected a nonempty publications list.")
    app = FastAPI()
    app.dependency_overrides[favoritos_endpoints.get_tenant_id] = fixture_owner
    app.include_router(routes.create_pesquisa_mercado_router())
    category = {"category": batch["category"]} if "category" in batch else {}
    with TemporaryDirectory(prefix="jk-pm-ranking-") as directory, TestClient(app) as client:
        root = Path(directory) / "info"
        sellers = {}
        with patch.object(routes, "get_pesquisa_mercado_store", return_value=PesquisaMercadoStore(root)):
            job = checked(client.post(JOBS, json=category))["job"]
            job_url = f"{JOBS}/{job['id']}"
            for index, body in enumerate(batch["publications"]):
                seller_key = body["seller"]["key"]
                checked(client.put(job_url + "/vendedores/" + quote(seller_key, safe=""), json=body))
                sellers.setdefault(seller_key, index)

        with patch.object(routes, "get_pesquisa_mercado_store", return_value=PesquisaMercadoStore(root)):
            job = checked(client.get(job_url))["job"]
            return {
                "jobCounters": job["counters"],
                "ranking": ranking(client, job_url),
                "bySeller": [
                    {"publicationIndex": index, **ranking(client, job_url, seller_key)}
                    for seller_key, index in sellers.items()
                ],
            }


if __name__ == "__main__":
    try:
        result = replay(json.load(sys.stdin.buffer))
    except (ValueError, KeyError, TypeError) as error:
        # Request validation may contain the submitted payload. Keep CLI output narrow.
        detail = str(error) if type(error) is ValueError and str(error).startswith("Fixture API returned HTTP ") else "Invalid fixture input or response."
        print(detail, file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, allow_nan=False, separators=(",", ":")))
