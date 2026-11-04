"""Measure actual list/SKU routes with an isolated synthetic catalog.

Run both versions with the same Python and this script:
    python scripts/benchmark_importacoes_sku_api.py --source-root TREE --variant before
    python scripts/benchmark_importacoes_sku_api.py --source-root TREE --variant after
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import statistics
import sys
import tempfile
import time
from pathlib import Path

ITEM_COUNT = 60
CATALOG_COUNT = 1500
POLL_INTERVAL = .01
ROUTE = "/api/medias-compras/listas-pedidos"


def summarize(samples):
    results = {}
    for name in samples[0]:
        values = sorted(sample[name] for sample in samples)
        results[name] = {
            "p50": round(statistics.median(values), 2),
            "p95": round(values[math.ceil(len(values) * .95) - 1], 2),
        }
    return results


def read(client, path, ready):
    started = time.perf_counter()
    size = 0
    requests = 0
    while True:
        response = client.get(path)
        requests += 1
        size += len(response.content)
        payload = response.json()
        if response.status_code == 200 and ready(payload):
            return payload, {"tempo_ms": (time.perf_counter() - started) * 1000,
                             "bytes_json": size, "consultas": requests}
        if response.status_code not in (200, 202) or time.perf_counter() - started > 8:
            raise RuntimeError(f"Detalhe sintético não ficou pronto (HTTP {response.status_code}).")
        time.sleep(POLL_INTERVAL)


def generate_data(fixture, csv_writer):
    tenant = fixture["root"] / "cliente-a"
    skus = [str(i + 1).zfill(3) for i in range(CATALOG_COUNT)]
    catalog = [{
        "store_id": "store-a", "sku": sku, "sku_normalizado": sku,
        "nome": f"Produto sintético {sku}", "m3 individual": "0,125", "row_version": "1",
        "updated_at_utc": "2026-10-02T12:00:00Z", "deleted_at_utc": "",
    } for sku in skus]
    csv_writer(tenant / "cadastro_produtos_lojas.csv", catalog)
    fiscal = fixture["catalog"][0]
    fixture["catalog"] = [{**fiscal, "sku": sku} for sku in skus]
    base = copy.deepcopy(fixture["lists"]["cliente-a"][0])
    base["exchange_rate"] = 5
    base["itens"] = [{"SKU": sku, "Quantidade": 2, "Valor unidade": 10, "Valor total": 20}
                     for sku in skus[:ITEM_COUNT]]
    return base, tenant


def validate(payload, sku, compact):
    if compact:
        item = payload["item"]
        assert "itens" not in payload["lista"]
        assert payload["fiscal_pronto"] is True
        assert payload["rateio"]["total_usd"] == 20 * ITEM_COUNT
        assert payload["rateio"]["total_m3"] == .25 * ITEM_COUNT
    else:
        assert len(payload["lista"]["itens"]) == ITEM_COUNT
        assert payload["lista"]["total_usd"] == 20 * ITEM_COUNT
        item = next(item for item in payload["lista"]["itens"] if item["SKU"] == sku)
    assert item["SKU"] == sku
    assert item["Quantidade"] == 2
    assert item["M3"] == .25
    assert item["II"] == 12.5


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--variant", choices=("before", "after"), required=True)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--scratch-dir", type=Path, default=Path.cwd())
    args = parser.parse_args()
    assert args.iterations > 0 and args.warmup >= 0
    root = args.source_root.resolve()
    sys.path[:0] = [str(root), str(root / "tests")]

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pytest import MonkeyPatch
    from requests import Session

    from backend.services import impostos  # Registers fiscal helpers used by the API.
    from backend.services import impostos_ncm, impostos_regras, medias_compras_common, medias_compras_fiscal, medias_compras_listas
    from test_medias_compras_carregamento import _csv, ambiente

    compact = args.variant == "after"
    source_file = root / "backend" / "services" / (
        "importacoes_sku_detalhe.py" if compact else "medias_compras_listas.py"
    )
    source_sha256 = hashlib.sha256(source_file.read_bytes()).hexdigest()
    app = FastAPI()
    app.dependency_overrides[medias_compras_common.get_tenant_id] = lambda: "cliente-a"
    if compact:
        from backend.services.importacoes_sku_detalhe import _impostos_sku, api_importacoes_sku_detalhe
        app.add_api_route(ROUTE + "/{lista_id}/skus/{sku}", api_importacoes_sku_detalhe)
    else:
        app.add_api_route(ROUTE + "/{lista_id}", medias_compras_listas.api_medias_compras_lista_pedido_detalhe)
    samples = {"dados_salvos": [], "primeira_consulta": [], "segundo_sku": []}

    with tempfile.TemporaryDirectory(prefix="jk-sku-benchmark-", dir=args.scratch_dir.resolve()) as sandbox, MonkeyPatch.context() as patch:
        def deny_external_http(*_args, **_kwargs):
            raise RuntimeError("O benchmark usa apenas dados sintéticos locais.")

        patch.setattr(Session, "request", deny_external_http)
        fixture = ambiente.__wrapped__(patch, Path(sandbox))
        base, tenant = generate_data(fixture, _csv)
        def tenant_path(_client_id):
            return str(tenant)

        _csv(tenant / "cadastro_produtos.csv", fixture["catalog"])
        patch.setattr(impostos_ncm, "get_tenant_path", tenant_path)
        patch.setattr(impostos_regras, "get_tenant_path", tenant_path)
        patch.setattr(impostos_regras, "_carregar_convenio_mg_referencia", lambda _client_id: [])
        if compact:
            original_load_catalog = medias_compras_fiscal._carregar_cadastro_para_impostos

            def filtered_catalog(client_id, *, filtro_sku=None):
                frame, catalog_path = original_load_catalog(client_id)
                if filtro_sku is not None:
                    frame = frame.loc[frame["sku"].map(filtro_sku)]
                return frame, catalog_path

            patch.setattr(medias_compras_fiscal, "_carregar_cadastro_para_impostos", filtered_catalog)
            patch.setattr(medias_compras_fiscal, "_caminhos_cadastros_meta", lambda _client_id, **_opts: [])
        with impostos_ncm._conexao_db_ncm_impostos("cliente-a") as connection:
            connection.execute(
                "INSERT INTO ncm_referencia (ncm,ncm_original,descricao,cest,ii,ipi,pis,cofins) "
                "VALUES ('87089990','8708.99.90','Teste','01.001.00',12.5,5,2.1,9.65)"
            )
        connection.close()
        if compact:
            assert _impostos_sku("cliente-a", "001")["sku"] == "001"

        with TestClient(app) as client:
            for iteration in range(args.warmup + args.iterations):
                # Each new list key begins cold. The second SKU shares the same revision.
                target = {**copy.deepcopy(base), "id": f"benchmark-{iteration}"}
                fixture["lists"]["cliente-a"] = [target]
                (tenant / "listas_pedidos.json").write_text(json.dumps([target]), encoding="utf-8")
                endpoint = f"{ROUTE}/{target['id']}"
                endpoint_a = f"{endpoint}/skus/001" if compact else endpoint
                endpoint_b = f"{endpoint}/skus/002" if compact else endpoint
                mode = "" if compact else "?cache_mode=stale"
                ready = (lambda payload: "item" in payload) if compact else (
                    lambda payload: payload.get("_cache", {}).get("estado") == "ready")
                _, saved = read(client, endpoint_a + "?somente_salvos=true", lambda payload: "lista" in payload)
                first, cold = read(client, endpoint_a + mode, ready)
                second, warm = read(client, endpoint_b + mode, ready)
                validate(first, "001", compact)
                validate(second, "002", compact)
                if iteration >= args.warmup:
                    for key, result in zip(samples, (saved, cold, warm)):
                        samples[key].append(result)
    assert hashlib.sha256(source_file.read_bytes()).hexdigest() == source_sha256, "A rota mudou durante a coleta."
    print(json.dumps({
        "variante": args.variant,
        "rota_sha256": source_sha256,
        "python": sys.version.split()[0],
        "iterations": args.iterations,
        "warmup": args.warmup,
        "itens": ITEM_COUNT,
        "cadastro": CATALOG_COUNT,
        "intervalo_consulta_ms": POLL_INTERVAL * 1000,
        "cenarios": {key: {"metricas": summarize(results), "amostras": results} for key, results in samples.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
