"""Consulta, em modo somente leitura, as compatibilidades dos anuncios de cada SKU.

O resultado e um cache de pesquisa intermediario. Ele nao faz parte dos dossies
finais e nunca armazena credenciais do Mercado Livre.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


ML_API_BASE = "https://api.mercadolibre.com"
USER_AGENT = "JK-Sistema-SKU-Research/2.0"
_PRINT_LOCK = threading.Lock()


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _attribute_value(attributes: Any, needle: str) -> str:
    wanted = _fold(needle)
    for attribute in attributes or []:
        name = _fold((attribute or {}).get("nome"))
        if wanted in name:
            return str((attribute or {}).get("valor") or "").strip()
    return ""


def _targets(raw_dir: Path) -> list[dict[str, str]]:
    by_item: dict[str, dict[str, str]] = {}
    for path in sorted(raw_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        dossier = _read_json(path, {})
        sku = str(dossier.get("sku") or path.stem).strip()
        for ad in ((dossier.get("anuncios") or {}).get("ativos") or []):
            item_id = str((ad or {}).get("item_id") or "").strip().upper()
            if not re.fullmatch(r"MLB\d+", item_id):
                continue
            has_compatibilities = _fold(_attribute_value(ad.get("atributos"), "compatibil"))
            if has_compatibilities not in {"sim", "yes", "true"}:
                continue
            by_item.setdefault(item_id, {
                "item_id": item_id,
                "sku": sku,
                "loja": str(ad.get("loja") or "").strip(),
                "titulo": str(ad.get("titulo") or "").strip(),
            })
    return list(by_item.values())


def _tokens(source_dir: Path) -> dict[str, str]:
    stores = _read_json(source_dir / "lojas_config.json", [])
    output: dict[str, str] = {}
    for store in stores if isinstance(stores, list) else []:
        integration = ((store.get("integracoes") or {}).get("mercadolivre") or {})
        token = str(integration.get("access_token") or "").strip()
        if token:
            output[_fold(store.get("nome") or store.get("name"))] = token
    return output


def _compact_product(product: Any) -> dict[str, Any] | None:
    if not isinstance(product, dict):
        return None
    name = str(product.get("catalog_product_name") or product.get("name") or "").strip()
    if not name:
        return None
    return {
        "catalog_product_id": str(product.get("catalog_product_id") or "").strip(),
        "nome_catalogo": name,
        "origem": str(product.get("source") or "").strip(),
    }


def _fetch(target: dict[str, str], token: str, timeout: float) -> dict[str, Any]:
    try:
        response = requests.get(
            f"{ML_API_BASE}/items/{target['item_id']}/compatibilities",
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
            params={"extended": "true"},
            timeout=timeout,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = None
        products = []
        if response.status_code == 200 and isinstance(payload, dict):
            for raw_product in payload.get("products") or []:
                compact = _compact_product(raw_product)
                if compact and compact not in products:
                    products.append(compact)
        return {
            **target,
            "status_http": response.status_code,
            "status": "consultado" if response.status_code == 200 else "falha",
            "produtos": products,
        }
    except requests.RequestException as exc:
        return {**target, "status": "falha", "erro": f"{type(exc).__name__}: {str(exc)[:180]}", "produtos": []}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Consulta compatibilidades ML dos anuncios ligados aos SKUs.")
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=30)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    repo_root = Path(__file__).resolve().parents[1]
    appdata = os.environ.get("APPDATA", "")
    default_source = Path(appdata) / "JK Sistema Cliente" / "local_app" / "info" / "000002"
    source_dir = (args.source_dir or default_source).resolve()
    raw_dir = args.raw_dir.resolve()
    output_path = args.output.resolve()
    tokens = _tokens(source_dir)
    targets = _targets(raw_dir)
    print(f"[COMPAT] anuncios a consultar: {len(targets)}", flush=True)
    print(f"[COMPAT] lojas com credencial: {len(tokens)}", flush=True)

    missing_store = [target for target in targets if _fold(target["loja"]) not in tokens]
    if missing_store:
        print(f"[COMPAT] sem credencial da loja: {len(missing_store)}", flush=True)

    results: dict[str, dict[str, Any]] = {}
    runnable = [target for target in targets if _fold(target["loja"]) in tokens]
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 12))) as executor:
        futures = {
            executor.submit(_fetch, target, tokens[_fold(target["loja"])], args.timeout): target
            for target in runnable
        }
        done = 0
        for future in as_completed(futures):
            target = futures[future]
            try:
                row = future.result()
            except Exception as exc:  # pragma: no cover - rede imprevisivel
                row = {**target, "status": "falha", "erro": f"{type(exc).__name__}: {str(exc)[:180]}", "produtos": []}
            results[target["item_id"]] = row
            done += 1
            if done % 50 == 0 or done == len(runnable):
                with _PRINT_LOCK:
                    print(f"[COMPAT] consultados: {done}/{len(runnable)}", flush=True)

    for target in missing_store:
        results[target["item_id"]] = {**target, "status": "sem_credencial_da_loja", "produtos": []}

    payload = {
        "schema_version": 1,
        "consultado_em": datetime.now(timezone.utc).isoformat(),
        "total_anuncios": len(targets),
        "consultados": sum(row.get("status") == "consultado" for row in results.values()),
        "com_produtos": sum(bool(row.get("produtos")) for row in results.values()),
        "falhas": sum(row.get("status") not in {"consultado"} for row in results.values()),
        "anuncios": dict(sorted(results.items())),
    }
    _write_json_atomic(output_path, payload)
    print(
        f"[COMPAT] concluido: {payload['consultados']} consultados; "
        f"{payload['com_produtos']} com aplicacoes; {payload['falhas']} falhas",
        flush=True,
    )
    return 0 if payload["falhas"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
