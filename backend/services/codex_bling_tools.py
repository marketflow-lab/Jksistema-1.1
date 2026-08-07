"""Read-only Bling Data Tools for the internal Codex assistant."""

from __future__ import annotations

import json
import logging
import math
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import HTTPException
import requests

from backend.services.bling import _BlingAdaptiveLimiter
from backend.services.bling_vendas import (
    _bling_executar_com_refresh,
    _bling_listar_naturezas,
    _bling_map_canais_venda_basico,
    _bling_map_depositos,
    _bling_map_lojas_virtuais,
    _bling_saldos,
)
from backend.services.estoque_lancamentos import (
    _bling_listar_lancamentos_lote,
    _bling_listar_lotes_produto,
)


logger = logging.getLogger(__name__)

BLING_API_BASE = "https://api.bling.com.br/Api/v3"
DEFAULT_LIMIT = 50
REPORT_LIMIT = 200
DETAIL_LIMIT = 100
QUERY_ONLY_LIMIT = 100
QUERY_TIMEOUT_SECONDS = 60
POSITIVE_STOCK_PRODUCT_LIMIT = 20_000
POSITIVE_STOCK_DEPOSIT_LIMIT = 1_000
POSITIVE_STOCK_BATCH_SIZE = 50


_RESOURCE_ROWS = [
    ("/anuncios", "anuncios", "List ads", "situacao,idProduto"),
    ("/anuncios/{idAnuncio}", "anuncios", "Get ad", "idAnuncio"),
    ("/anuncios/categorias", "anuncios", "List ad categories", "idCategoria,tipoProduto"),
    ("/caixas", "financeiro", "List cash/bank entries", "dataInicial,dataFinal"),
    ("/caixas/{idCaixa}", "financeiro", "Get cash/bank entry", "idCaixa"),
    ("/canais-venda", "canais", "List sales channels", "tipos[],situacao"),
    ("/canais-venda/{idCanalVenda}", "canais", "Get sales channel", "idCanalVenda"),
    ("/canais-venda/tipos", "canais", "List sales channel types", ""),
    ("/categorias/lojas", "categorias", "List virtual store categories", "idLoja,idCategoriaProduto"),
    ("/categorias/produtos", "categorias", "List product categories", ""),
    ("/categorias/receitas-despesas", "financeiro", "List revenue/expense categories", "tipo,situacao"),
    ("/contas/pagar", "financeiro", "List payable accounts", "dataVencimentoInicial,dataVencimentoFinal,situacao"),
    ("/contas/pagar/{idContaPagar}", "financeiro", "Get payable account", "idContaPagar"),
    ("/contas/receber", "financeiro", "List receivable accounts", "tipoFiltroData,dataInicial,dataFinal,situacoes[]"),
    ("/contas/receber/{idContaReceber}", "financeiro", "Get receivable account", "idContaReceber"),
    ("/contas/receber/boletos", "financeiro", "List receivable boletos", ""),
    ("/contas-contabeis", "financeiro", "List financial accounts", "situacoes,ordenacao"),
    ("/contatos", "contatos", "List contacts", "pesquisa,criterio,numeroDocumento"),
    ("/contatos/{idContato}", "contatos", "Get contact", "idContato"),
    ("/contratos", "contratos", "List contracts", "dataCriacaoInicio,dataCriacaoFinal,situacao,idContato"),
    ("/depositos", "estoque", "List deposits", "descricao,situacao"),
    ("/depositos/{idDeposito}", "estoque", "Get deposit", "idDeposito"),
    ("/empresas/me/dados-basicos", "empresa", "Get company basic data", ""),
    ("/estoques/saldos", "estoque", "Get product stock balances", "idsProdutos[],codigos[]"),
    ("/estoques/saldos/{idDeposito}", "estoque", "Get stock balances by deposit", "idDeposito,idsProdutos[],codigos[]"),
    ("/formas-pagamentos", "financeiro", "List payment methods", "descricao,situacao"),
    ("/grupos-produtos", "produtos", "List product groups", "nome,nomePai"),
    ("/logisticas", "logistica", "List logistics", "tipoIntegracao,situacao"),
    ("/logisticas/{idLogistica}", "logistica", "Get logistics", "idLogistica"),
    ("/logisticas/etiquetas", "logistica", "Get sale labels", "formato,idsVendas[]"),
    ("/naturezas-operacoes", "fiscal", "List operation natures", "situacao,descricao"),
    ("/nfce", "fiscal", "List NFC-e", "chaveAcesso,numero,serie,situacao,dataEmissaoInicial,dataEmissaoFinal"),
    ("/nfce/{idNotaFiscalConsumidor}", "fiscal", "Get NFC-e", "idNotaFiscalConsumidor"),
    ("/nfe", "fiscal", "List NF-e", "chaveAcesso,numero,serie,situacao,tipo,dataEmissaoInicial,dataEmissaoFinal"),
    ("/nfe/{idNotaFiscal}", "fiscal", "Get NF-e", "idNotaFiscal"),
    ("/nfe/documento/{chaveAcesso}", "fiscal", "Get NF-e document", "chaveAcesso,formato"),
    ("/nfse", "fiscal", "List NFS-e", "situacao,dataEmissaoInicial,dataEmissaoFinal"),
    ("/notificacoes", "notificacoes", "List notifications", "periodo"),
    ("/ordens-producao", "producao", "List production orders", "idsSituacoes[]"),
    ("/pedidos/compras", "compras", "List purchase orders", "dataInicial,dataFinal,idFornecedor"),
    ("/pedidos/compras/{idPedidoCompra}", "compras", "Get purchase order", "idPedidoCompra"),
    ("/pedidos/vendas", "vendas", "List sales orders", "dataInicial,dataFinal,numero,idLoja,idsSituacoes[]"),
    ("/pedidos/vendas/{idPedidoVenda}", "vendas", "Get sales order", "idPedidoVenda"),
    ("/produtos", "produtos", "List products", "criterio,tipo,nome,idsProdutos[],codigos[],gtins[]"),
    ("/produtos/{idProduto}", "produtos", "Get product", "idProduto"),
    ("/produtos/fornecedores", "produtos", "List supplier products", "idProduto,idFornecedor"),
    ("/produtos/lojas", "produtos", "List product-store links", "idProduto,idLoja,dataAlteracaoInicial,dataAlteracaoFinal"),
    ("/produtos/lotes", "lotes", "List product lots", "idsProdutos[]"),
    ("/produtos/lotes/{idLote}", "lotes", "Get product lot", "idLote"),
    ("/produtos/lotes/{idLote}/lancamentos", "lotes", "List lot movements", "idLote"),
    ("/produtos/lotes/controla-lote", "lotes", "Check lot control", "idsProdutos[]"),
    ("/produtos/lotes/lancamentos/{idLancamento}", "lotes", "Get lot movement", "idLancamento"),
    ("/produtos/variacoes/{idProdutoPai}", "produtos", "Get product variations", "idProdutoPai"),
    ("/propostas-comerciais", "vendas", "List commercial proposals", "situacao,idContato,dataInicial,dataFinal"),
    ("/situacoes/{idSituacao}", "situacoes", "Get status", "idSituacao"),
    ("/situacoes/modulos", "situacoes", "List status modules", ""),
    ("/situacoes/modulos/{idModuloSistema}", "situacoes", "List module statuses", "idModuloSistema"),
    ("/vendedores", "vendedores", "List sellers", "nomeContato,situacaoContato,idContato,idLoja"),
    ("/vendedores/{idVendedor}", "vendedores", "Get seller", "idVendedor"),
]


BLING_READ_ONLY_RESOURCES = [
    {
        "method": "GET",
        "path": path,
        "module": module,
        "description": description,
        "params": [item for item in params.split(",") if item],
        "read_only": True,
    }
    for path, module, description, params in _RESOURCE_ROWS
]

BLING_READ_ONLY_EXCEPTIONS = [
    {
        "method": "POST",
        "path": "/naturezas-operacoes/{idNaturezaOperacao}/obter-tributacao",
        "module": "fiscal",
        "description": "Read-only fiscal tax rule calculation for an operation nature",
        "params": ["idNaturezaOperacao"],
        "read_only": True,
        "mutating": False,
    }
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def _num(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(str(value).replace(",", "."))
    except Exception:
        return 0.0


def _limit(value: Any, default: int = DEFAULT_LIMIT, maximum: int = REPORT_LIMIT) -> int:
    try:
        parsed = int(value or default)
    except Exception:
        parsed = default
    return max(1, min(parsed, maximum))


def _extract_first_id(text: str) -> str:
    cleaned = str(text or "")
    for pattern in (
        r"\bid\s*(?:bling|pedido|nota|nfe|lote|conta|caixa)?\s*[:#-]?\s*(\d{2,})\b",
        r"\b(?:pedido|nota|nfe|lote|conta|caixa)\s+(\d{2,})\b",
    ):
        match = re.search(pattern, cleaned, flags=re.I)
        if match:
            return match.group(1)
    return ""


def _extract_invoice_key(text: str) -> str:
    match = re.search(r"\b(\d{44})\b", str(text or ""))
    return match.group(1) if match else ""


def _extract_ref(text: str) -> str:
    raw = str(text or "").strip()
    for pattern in (
        r"\bsku\s*[:#-]?\s*([A-Za-z0-9._/-]{2,50})\b",
        r"\bcodigo\s*[:#-]?\s*([A-Za-z0-9._/-]{2,50})\b",
        r"\bgtin\s*[:#-]?\s*([A-Za-z0-9._/-]{8,50})\b",
        r"\bean\s*[:#-]?\s*([A-Za-z0-9._/-]{8,50})\b",
    ):
        match = re.search(pattern, raw, flags=re.I)
        if match:
            return match.group(1).strip(".,;:)")
    for token in re.findall(r"\b[A-Z0-9][A-Z0-9._/-]{2,50}\b", raw.upper()):
        if re.search(r"\d", token) and token.lower() not in {"bling", "nfe", "sku"}:
            return token.strip(".,;:)")
    return ""


def _unique_texts(values: Any, limit: int = 20) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    for value in values:
        text = str(value or "").strip().strip(".,;:)")
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= max(1, int(limit or 1)):
            break
    return out


def _extract_bling_product_ids(text: str) -> list[str]:
    raw = str(text or "")
    ids: list[str] = []
    patterns = (
        r"\b(?:id_bling|id\s*bling|bling\s*id|id_produto_bling|id\s*produto\s*bling|idsProdutos\[\]|ids_produtos)\b[^0-9]{0,30}((?:\d{3,}[\s,;/|]*){1,12})",
        r"\b(?:ids?\s*bling|ids?\s*produtos?\s*bling)\b[^0-9]{0,30}((?:\d{3,}[\s,;/|]*){1,12})",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, raw, flags=re.I):
            ids.extend(re.findall(r"\d{3,}", match.group(1) or ""))
    return _unique_texts(ids, 20)


def _extract_name_term(text: str) -> str:
    raw = str(text or "").strip()
    quoted = re.search(r"[\"']([^\"']{3,80})[\"']", raw)
    if quoted:
        return quoted.group(1).strip()
    match = re.search(r"\b(?:produto|nome|item)\s+(.{3,80})", raw, flags=re.I)
    if match:
        tail = re.sub(r"\b(?:na|no|do|da)?\s*bling\b.*$", "", match.group(1), flags=re.I).strip()
        return tail[:80]
    return ""


def _params_add(base: list[tuple[str, Any]], key: str, values: Any) -> None:
    if values is None:
        return
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    for value in values:
        text = str(value or "").strip()
        if text:
            base.append((key, text))


def _json_error_payload(status: int, data: Any) -> str:
    if isinstance(data, dict):
        error = data.get("error") if isinstance(data.get("error"), dict) else {}
        message = error.get("message") or error.get("description") or data.get("message")
        if message:
            return str(message)[:300]
    return f"HTTP {status}"


def _response_error_text(data: Any, status: int) -> str:
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()[:300]
        if isinstance(error, dict):
            message = error.get("message") or error.get("description")
            if message:
                return str(message)[:300]
        message = data.get("message")
        if message:
            return str(message)[:300]
    if int(status or 0) == 401:
        return "Token Bling ausente, expirado ou sem permissao. Refaca a conexao em Integracoes."
    return f"HTTP {status}"


def _resource_meta(path: str) -> dict[str, Any]:
    for item in BLING_READ_ONLY_RESOURCES + BLING_READ_ONLY_EXCEPTIONS:
        if item.get("path") == path:
            return dict(item)
    return {"path": path, "method": "GET", "module": "bling", "description": path, "params": [], "read_only": True}


def _resource_url(path: str) -> str:
    return BLING_API_BASE + path


def _ia_func(name: str) -> Callable[..., Any]:
    from backend.services import ia as ia_service
    from backend.services.marketplace_tools.registry import TOOL_EXECUTORS, resolve_executor

    func = resolve_executor(name) if name in TOOL_EXECUTORS else getattr(ia_service, name, None)
    if not callable(func):
        raise RuntimeError(f"Funcao IA indisponivel: {name}")
    return func


def _remaining_timeout(deadline: Optional[float], requested: int = 20) -> int:
    if deadline is None:
        return max(1, int(requested or 1))
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        raise requests.exceptions.Timeout("A consulta completa do Bling excedeu 60 segundos.")
    return max(1, min(int(requested or 1), int(math.ceil(remaining))))


def _get_json(
    access_token: str,
    path: str,
    params: Any = None,
    timeout: int = 20,
    deadline: Optional[float] = None,
    limiter: Optional[_BlingAdaptiveLimiter] = None,
) -> tuple[Any, int]:
    headers = {"Authorization": f"Bearer {access_token}"}
    limiter = limiter or _BlingAdaptiveLimiter(start_interval=0.09)
    resp = None
    # The Codex/WhatsApp query path is intentionally conservative: never retry
    # a 429 in the same request. A single retry is reserved for network/5xx
    # failures, while OAuth 401 handling remains in _bling_executar_com_refresh.
    for attempt in range(2):
        limiter.wait_turn()
        try:
            resp = requests.get(
                _resource_url(path),
                headers=headers,
                params=params,
                timeout=_remaining_timeout(deadline, timeout),
            )
        except requests.RequestException:
            if deadline is not None and time.monotonic() >= float(deadline):
                return {"error": "A consulta completa do Bling excedeu 60 segundos."}, 504
            if attempt == 0:
                time.sleep(limiter.on_transient_error())
                continue
            resp = None
            break
        status = int(resp.status_code or 0)
        if status == 429:
            break
        if status in {500, 502, 503, 504} and attempt == 0:
            time.sleep(limiter.on_transient_error())
            continue
        if status == 200:
            limiter.on_success()
        break
    if resp is None:
        return {"error": "Falha de conexao com a API Bling."}, 503
    status = int(resp.status_code or 0)
    try:
        payload = resp.json()
    except Exception:
        payload = {"error": {"message": str(getattr(resp, "text", ""))[:300]}}
    if status != 200:
        return payload, status
    if isinstance(payload, dict) and "data" in payload:
        return payload.get("data"), 200
    return payload, 200


def _list_paginated(
    access_token: str,
    path: str,
    params: Any = None,
    limit: int = DEFAULT_LIMIT,
    page_limit: int = 10,
    deadline: Optional[float] = None,
) -> tuple[list[dict[str, Any]], int]:
    remaining = _limit(limit)
    rows: list[dict[str, Any]] = []
    for pagina in range(1, max(1, int(page_limit or 1)) + 1):
        page_params = list(params or []) if isinstance(params, list) else list((params or {}).items())
        page_params.append(("pagina", pagina))
        page_params.append(("limite", min(100, max(1, remaining))))
        data, status = _get_json(access_token, path, page_params, deadline=deadline)
        if status != 200:
            return [], status
        if isinstance(data, dict):
            data = data.get("data") if isinstance(data.get("data"), list) else []
        if not isinstance(data, list) or not data:
            break
        for item in data:
            if isinstance(item, dict):
                rows.append(item)
                remaining -= 1
                if remaining <= 0:
                    return rows, 200
        if len(data) < 100:
            break
    return rows, 200


def _list_complete_paginated(
    access_token: str,
    path: str,
    params: Any = None,
    *,
    max_records: int,
    deadline: Optional[float],
    limiter: Optional[_BlingAdaptiveLimiter] = None,
) -> tuple[list[dict[str, Any]], int, bool, dict[str, Any]]:
    """List a complete API collection without the 200-row conversational clamp."""

    cap = max(1, int(max_records or 1))
    rows: list[dict[str, Any]] = []
    pages = 0
    page_size = 100
    while len(rows) < cap:
        pagina = pages + 1
        requested_limit = min(page_size, cap - len(rows))
        page_params = list(params or []) if isinstance(params, list) else list((params or {}).items())
        page_params.extend((("pagina", pagina), ("limite", requested_limit)))
        data, status = _get_json(
            access_token,
            path,
            page_params,
            deadline=deadline,
            limiter=limiter,
        )
        pages += 1
        if status != 200:
            return rows, status, False, {
                "pages": pages,
                "max_records": cap,
                "reason": f"HTTP {status} na pagina {pagina}",
            }
        if isinstance(data, dict):
            data = data.get("data") if isinstance(data.get("data"), list) else []
        if not isinstance(data, list):
            return rows, 502, False, {
                "pages": pages,
                "max_records": cap,
                "reason": f"resposta invalida na pagina {pagina}",
            }
        page_rows = [item for item in data if isinstance(item, dict)]
        rows.extend(page_rows[: max(0, cap - len(rows))])
        if len(page_rows) != len(data):
            return rows, 502, False, {
                "pages": pages,
                "max_records": cap,
                "reason": f"pagina {pagina} contem registro(s) invalido(s)",
            }
        if len(data) < requested_limit:
            return rows, 200, True, {"pages": pages, "max_records": cap, "reason": ""}
        if not page_rows:
            return rows, 502, False, {
                "pages": pages,
                "max_records": cap,
                "reason": f"pagina {pagina} sem registros validos",
            }
    return rows, 200, False, {
        "pages": pages,
        "max_records": cap,
        "reason": f"catalogo atingiu o limite seguro de {cap} registros",
    }


def _exception_chain_text(exc: BaseException) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen and len(parts) < 5:
        seen.add(id(cur))
        detail = getattr(cur, "detail", None)
        text = str(detail or cur or "").strip()
        if text:
            parts.append(text)
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    return " | ".join(parts)


def _classify_bling_exception(exc: BaseException) -> tuple[int, str]:
    if isinstance(exc, HTTPException):
        return int(exc.status_code or 500), str(exc.detail or exc)[:300]
    chain = _exception_chain_text(exc)
    chain_norm = _norm(chain)
    if "token bling expirado" in chain_norm or ("refaca a conexao" in chain_norm and "integracoes" in chain_norm):
        return 401, "Token Bling expirado para esta loja. Refaca a conexao em Integracoes."
    if "unauthorized" in chain_norm or "401" in chain_norm:
        return 401, "Bling retornou 401. Token ausente, expirado ou sem permissao."
    return 500, (chain or str(exc))[:300]


def _call_store(
    client_id: str,
    loja: str,
    cfg: dict[str, Any],
    callback: Callable[[str], tuple[Any, int]],
) -> tuple[Any, int, dict[str, Any]]:
    try:
        return _bling_executar_com_refresh(client_id, loja, cfg, callback)
    except HTTPException as exc:
        return {"error": str(exc.detail or exc)}, int(exc.status_code or 500), cfg
    except Exception as exc:
        status, message = _classify_bling_exception(exc)
        if status in {401, 403}:
            logger.warning("[Codex Bling] Consulta read-only sem autorizacao da loja %s: %s", loja, message)
        else:
            logger.exception("[Codex Bling] Falha na consulta read-only da loja %s", loja)
        return {"error": message}, status, cfg


def _connected_stores(
    client_id: str,
    loja: Optional[str],
    *,
    require_exact: bool = False,
    allow_default_fallback: bool = True,
) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    warnings: list[str] = []
    stores: list[tuple[str, dict[str, Any]]] = []
    lojas_cfg: list[dict[str, Any]] = []
    try:
        from backend.services.integracoes import carregar_lojas

        lojas_cfg = [item for item in (carregar_lojas(client_id) or []) if isinstance(item, dict)]
    except Exception as exc:
        root = Path(__file__).resolve().parents[2]
        path = root / "info" / str(client_id or "default") / "lojas_config.json"
        if not path.exists() and str(client_id or "") != "default" and allow_default_fallback:
            path = root / "info" / "default" / "lojas_config.json"
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
                lojas_cfg = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
                warnings.append("Usei leitura direta de lojas_config.json porque o servico de integracoes nao estava configurado.")
            except Exception as read_exc:
                return [], [f"Nao foi possivel carregar lojas Bling: {str(exc)[:120]}; fallback falhou: {str(read_exc)[:120]}"]
        else:
            return [], [f"Nao foi possivel carregar lojas Bling: {str(exc)[:180]}"]

    conectadas: list[str] = []
    by_norm: dict[str, dict[str, Any]] = {}
    for loja_cfg in lojas_cfg:
        nome = str(loja_cfg.get("nome") or "").strip()
        integracoes = loja_cfg.get("integracoes") if isinstance(loja_cfg.get("integracoes"), dict) else {}
        cfg = integracoes.get("bling") if isinstance(integracoes, dict) else {}
        if nome:
            by_norm[_norm(nome)] = loja_cfg
        if nome and isinstance(cfg, dict) and str(cfg.get("access_token") or "").strip():
            conectadas.append(nome)
    loja_txt = str(loja or "").strip()
    if require_exact and (not loja_txt or loja_txt in {"__todas", "Todas as lojas"}):
        choices = ", ".join(conectadas[:10]) or "nenhuma"
        return [], [f"Informe uma loja Bling especifica antes da consulta. Lojas disponiveis: {choices}."]
    if require_exact:
        alvo_norm = _norm(loja_txt)
        nomes = [nome for nome in conectadas if _norm(nome) == alvo_norm]
        if not nomes:
            choices = ", ".join(conectadas[:10]) or "nenhuma"
            return [], [f"Loja Bling inexistente ou ambigua: {loja_txt}. Lojas disponiveis: {choices}."]
    elif loja_txt and loja_txt not in {"__todas", "Todas as lojas"}:
        alvo_norm = _norm(loja_txt)
        nomes = [
            nome for nome in conectadas
            if _norm(nome) == alvo_norm or (alvo_norm and (alvo_norm in _norm(nome) or _norm(nome) in alvo_norm))
        ][:5]
    else:
        nomes = conectadas[:5]
    if not nomes:
        return [], ["Nenhuma loja com Bling conectado foi encontrada para o escopo solicitado."]
    for nome in nomes:
        try:
            loja_cfg = by_norm.get(_norm(nome))
            if not loja_cfg:
                raise HTTPException(status_code=404, detail="Loja nao encontrada")
            integracoes = loja_cfg.get("integracoes") if isinstance(loja_cfg.get("integracoes"), dict) else {}
            cfg = dict(integracoes.get("bling") or {})
            if not cfg:
                raise HTTPException(status_code=400, detail="Integracao Bling nao configurada para esta loja")
            cfg["id"] = cfg.get("id") or cfg.get("client_id")
            cfg["secret"] = cfg.get("secret") or cfg.get("client_secret")
            if not cfg.get("access_token"):
                raise HTTPException(status_code=401, detail="Token Bling ausente. Refaca a autenticacao OAuth.")
        except HTTPException as exc:
            warnings.append(f"{nome}: {exc.detail}")
            continue
        except Exception as exc:
            warnings.append(f"{nome}: {str(exc)[:180]}")
            continue
        stores.append((nome, cfg))
    if not stores and not warnings:
        warnings.append("As lojas encontradas nao possuem token Bling valido.")
    return stores, warnings


def _result(
    function: str,
    arguments: dict[str, Any],
    rows_key: str,
    rows: list[Any],
    *,
    lojas: list[str],
    sources: list[dict[str, Any]],
    warnings: Optional[list[str]] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    result = dict(extra or {})
    result.update(
        {
            rows_key: rows,
            "rows": rows,
            "records": len(rows),
            "record_count": len(rows),
            "lojas": lojas,
            "sources": sources,
            "warnings": list(warnings or []),
            "queried_at": _now(),
            "read_only": True,
            "cache_hit": False,
        }
    )
    return {"function": function, "arguments": arguments, "result": result}


def _source(loja: str, path: str, records: int, params: Any = None, status: int = 200) -> dict[str, Any]:
    meta = _resource_meta(path)
    return {
        "loja": loja,
        "method": meta.get("method") or "GET",
        "path": path,
        "module": meta.get("module") or "bling",
        "records": int(records or 0),
        "status": status,
        "params": params or {},
        "external": True,
        "read_only": True,
    }


def _compact_product(produto: dict[str, Any], loja: str = "") -> dict[str, Any]:
    tributacao = produto.get("tributacao") if isinstance(produto.get("tributacao"), dict) else {}
    estoque = produto.get("estoque") if isinstance(produto.get("estoque"), dict) else {}
    return {
        "loja": loja,
        "id": produto.get("id"),
        "sku": produto.get("codigo") or produto.get("sku"),
        "nome": produto.get("nome") or produto.get("descricao"),
        "tipo": produto.get("tipo"),
        "situacao": produto.get("situacao"),
        "formato": produto.get("formato"),
        "preco": produto.get("preco"),
        "preco_custo": produto.get("precoCusto") or produto.get("preco_custo"),
        "ncm": produto.get("ncm") or tributacao.get("ncm"),
        "cest": produto.get("cest") or tributacao.get("cest"),
        "origem": tributacao.get("origem"),
        "unidade": produto.get("unidade"),
        "estoque_minimo": estoque.get("minimo") or produto.get("estoqueMinimo"),
        "estoque_maximo": estoque.get("maximo") or produto.get("estoqueMaximo"),
    }


def _search_products_for_store(access_token: str, message: str, limit: int) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    ref = _extract_ref(message)
    explicit_ids = _extract_bling_product_ids(message)
    term = _extract_name_term(message)
    attempts: list[tuple[str, list[tuple[str, Any]]]] = []
    if explicit_ids:
        attempts.append(("idsProdutos[]", [("idsProdutos[]", pid) for pid in explicit_ids[:_limit(limit)]]))
    if ref:
        attempts.append(("codigos[]", [("codigos[]", ref)]))
        if len(ref) >= 8:
            attempts.append(("gtins[]", [("gtins[]", ref)]))
        attempts.append(("nome", [("nome", ref)]))
    if term and term != ref:
        attempts.append(("nome", [("nome", term)]))
    if not attempts:
        attempts.append(("recentes", []))

    seen: set[str] = set()
    products: list[dict[str, Any]] = []
    last_status = 200
    used: dict[str, Any] = {"ref": ref, "ids_bling": explicit_ids, "term": term, "attempts": []}
    for name, params in attempts:
        rows, status = _list_paginated(access_token, "/produtos", params=params, limit=limit, page_limit=3)
        last_status = status
        used["attempts"].append({"type": name, "status": status, "records": len(rows)})
        if status != 200:
            continue
        for item in rows:
            pid = str(item.get("id") or "")
            key = pid or str(item.get("codigo") or item)
            if key in seen:
                continue
            seen.add(key)
            products.append(item)
            if len(products) >= limit:
                return products, 200, used
        if products:
            break
    return products, last_status, used


def _search_products_payload(access_token: str, message: str, limit: int) -> tuple[dict[str, Any], int]:
    products, status, used = _search_products_for_store(access_token, message, limit)
    return {"products": products, "used": used}, status


def _detail_products(access_token: str, products: list[dict[str, Any]], detail_limit: int = 10) -> list[dict[str, Any]]:
    detailed: list[dict[str, Any]] = []
    for produto in products[: max(0, int(detail_limit or 0))]:
        pid = str(produto.get("id") or "").strip()
        if not pid:
            detailed.append(produto)
            continue
        data, status = _get_json(access_token, f"/produtos/{pid}")
        if status == 200 and isinstance(data, dict):
            detailed.append(data)
        else:
            detailed.append(produto)
    detailed.extend(products[len(detailed):])
    return detailed


def tool_bling_status(client_id: str, message: str, loja: Optional[str], limit: int = DEFAULT_LIMIT, **_: Any) -> dict[str, Any]:
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    try:
        raw_status = _ia_func("_ia_tool_get_integrations_status")(client_id, loja)
    except Exception:
        raw_status = None
    stores, store_warnings = _connected_stores(client_id, loja)
    warnings.extend(store_warnings)
    for nome, cfg in stores:
        info = {
            "loja": nome,
            "bling_conectado": True,
            "access_token": "presente" if cfg.get("access_token") else "ausente",
            "refresh_token": "presente" if cfg.get("refresh_token") else "ausente",
        }
        canais, status_canais, cfg = _call_store(client_id, nome, cfg, _bling_map_canais_venda_basico)
        depositos, status_deps, _ = _call_store(client_id, nome, cfg, _bling_map_depositos)
        if status_canais == 200 and isinstance(canais, dict):
            info["canais_venda"] = len(canais)
        elif status_canais != 200:
            warnings.append(f"{nome}: canais de venda retornaram HTTP {status_canais}.")
        if status_deps == 200 and isinstance(depositos, dict):
            info["depositos"] = len(depositos)
        elif status_deps != 200:
            warnings.append(f"{nome}: depositos retornaram HTTP {status_deps}.")
        rows.append(info)
        sources.append(_source(nome, "/canais-venda", int(info.get("canais_venda") or 0), status=status_canais))
        sources.append(_source(nome, "/depositos", int(info.get("depositos") or 0), status=status_deps))
    if not rows and isinstance(raw_status, dict):
        result = raw_status.get("result") if isinstance(raw_status.get("result"), dict) else {}
        lojas = result.get("lojas") if isinstance(result.get("lojas"), list) else []
        rows.extend([item for item in lojas if isinstance(item, dict)])
    return _result(
        "bling_status",
        {"loja": loja or ""},
        "lojas",
        rows[:_limit(limit)],
        lojas=[nome for nome, _ in stores],
        sources=sources,
        warnings=warnings,
        extra={"status_source": "integrations_status_and_bling_light_checks"},
    )


def tool_bling_products(client_id: str, message: str, loja: Optional[str], limit: int = DEFAULT_LIMIT, **_: Any) -> dict[str, Any]:
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    stores, store_warnings = _connected_stores(client_id, loja)
    warnings.extend(store_warnings)
    ref = _extract_ref(message)
    if ref:
        try:
            raw = _ia_func("_ia_tool_get_bling_product")(client_id, message, loja, None, limite=min(_limit(limit), 20))
        except Exception:
            raw = None
        result = raw.get("result") if isinstance(raw, dict) and isinstance(raw.get("result"), dict) else {}
        matches = result.get("matches") if isinstance(result.get("matches"), list) else []
        if matches:
            for item in matches:
                if isinstance(item, dict):
                    rows.append(item)
            product_warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
            return _result(
                "bling_products",
                {"mensagem": message, "loja": loja or "", "ref": ref},
                "produtos",
                rows[:_limit(limit)],
                lojas=sorted({str(item.get("loja") or "") for item in rows if isinstance(item, dict)}),
                sources=[_source(str(item.get("loja") or ""), "/produtos", 1, {"ref": ref}) for item in rows[:10] if isinstance(item, dict)],
                warnings=warnings + list(product_warnings or []),
                extra={"source_tool": "_ia_tool_get_bling_product"},
            )
    for nome, cfg in stores:
        product_payload, status, cfg = _call_store(
            client_id,
            nome,
            cfg,
            lambda token: _search_products_payload(token, message, _limit(limit)),
        )
        used = product_payload.get("used") if isinstance(product_payload, dict) and isinstance(product_payload.get("used"), dict) else {}
        if status != 200:
            warnings.append(f"{nome}: produtos Bling falharam: {_response_error_text(product_payload, status)}.")
            sources.append(_source(nome, "/produtos", 0, used if isinstance(used, dict) else {}, status=status))
            continue
        products = product_payload.get("products") if isinstance(product_payload, dict) and isinstance(product_payload.get("products"), list) else []
        detailed, status_detail, _ = _call_store(
            client_id,
            nome,
            cfg,
            lambda token, _items=products: (_detail_products(token, _items, min(10, _limit(limit))), 200),
        )
        if status_detail == 200 and isinstance(detailed, list):
            products = detailed
        compact = [_compact_product(item, nome) for item in products if isinstance(item, dict)]
        rows.extend(compact)
        sources.append(_source(nome, "/produtos", len(compact), used if isinstance(used, dict) else {}, status=status))
    return _result(
        "bling_products",
        {"mensagem": message, "loja": loja or "", "limite": _limit(limit)},
        "produtos",
        rows[:_limit(limit)],
        lojas=[nome for nome, _ in stores],
        sources=sources,
        warnings=warnings,
    )


def tool_bling_fiscal_product(client_id: str, message: str, loja: Optional[str], limit: int = DEFAULT_LIMIT, **kwargs: Any) -> dict[str, Any]:
    raw = tool_bling_products(client_id, message, loja, limit=min(_limit(limit), 20), **kwargs)
    result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
    products = result.get("produtos") if isinstance(result.get("produtos"), list) else []
    fiscal_rows = []
    for item in products:
        if not isinstance(item, dict):
            continue
        fiscal_rows.append(
            {
                "loja": item.get("loja"),
                "id": item.get("id") or item.get("id_bling"),
                "sku": item.get("sku"),
                "nome": item.get("nome"),
                "ncm": item.get("ncm"),
                "cest": item.get("cest"),
                "origem": item.get("origem"),
                "unidade": item.get("unidade"),
                "preco": item.get("preco"),
                "preco_custo": item.get("preco_custo") or item.get("preco_custo_bling"),
            }
        )
    return _result(
        "bling_fiscal_product",
        {"mensagem": message, "loja": loja or ""},
        "produtos_fiscais",
        fiscal_rows,
        lojas=result.get("lojas") if isinstance(result.get("lojas"), list) else [],
        sources=result.get("sources") if isinstance(result.get("sources"), list) else [],
        warnings=result.get("warnings") if isinstance(result.get("warnings"), list) else [],
        extra={"records_source": "bling_products"},
    )


def _bling_deposito_eh_full(deposito: Any) -> bool:
    if not isinstance(deposito, dict):
        return False
    descricao = str(deposito.get("descricao") or deposito.get("nome") or "")
    return bool(
        re.search(
            r"\b(full|fulfillment|mercado livre(?: full)?|mercado envios?|ml full)\b",
            descricao,
            re.I,
        )
    )


def _bling_deposito_id(deposito: Any) -> str:
    if not isinstance(deposito, dict):
        return ""
    nested = deposito.get("deposito") if isinstance(deposito.get("deposito"), dict) else {}
    value = deposito.get("id") or deposito.get("idDeposito") or nested.get("id")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value or "").strip()


def _bling_optional_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = _norm(value)
    if normalized in {"1", "true", "sim", "yes", "ativo", "ativa", "active", "a"}:
        return True
    if normalized in {"0", "false", "nao", "no", "inativo", "inativa", "inactive", "i"}:
        return False
    return None


def _bling_deposito_ativo(deposito: Any) -> Optional[bool]:
    if not isinstance(deposito, dict) or "situacao" not in deposito:
        return None
    value = deposito.get("situacao")
    if isinstance(value, dict):
        value = value.get("id") if value.get("id") is not None else value.get("valor") or value.get("nome")
    return _bling_optional_bool(value)


def _bling_deposito_desconsidera_saldo(deposito: Any) -> Optional[bool]:
    if not isinstance(deposito, dict) or "desconsiderarSaldo" not in deposito:
        return None
    return _bling_optional_bool(deposito.get("desconsiderarSaldo"))


def _bling_deposito_resultado(
    saldo: dict[str, Any],
    catalogo: Optional[dict[str, Any]],
    *,
    incluido: bool,
    motivo: str = "",
) -> dict[str, Any]:
    deposito_id = _bling_deposito_id(saldo)
    catalogo = catalogo if isinstance(catalogo, dict) else {}
    descricao = str(catalogo.get("descricao") or catalogo.get("nome") or "").strip()
    if not descricao:
        descricao = f"Deposito ID {deposito_id or 'nao informado'} - nao classificado"
    row = {
        "id": deposito_id or None,
        "descricao": descricao,
        "saldo_fisico": _num(saldo.get("saldoFisico")),
        "saldo_virtual": _num(saldo.get("saldoVirtual")),
        "situacao": catalogo.get("situacao"),
        "padrao": bool(catalogo.get("padrao")),
        "desconsiderar_saldo": _bling_deposito_desconsidera_saldo(catalogo),
        "tipo_detectado": "FULL" if _bling_deposito_eh_full(catalogo) else "LOJA",
        "incluido_no_saldo_loja": bool(incluido),
    }
    if motivo:
        row["motivo"] = motivo
        row["motivo_exclusao"] = motivo
    return row


def _bling_stock_chart_data(rows: list[dict[str, Any]], lojas: list[str]) -> dict[str, Any]:
    """Return the complete aggregate stock contract, without customer data."""

    safe_rows = [row for row in rows if isinstance(row, dict)]
    ranking: list[dict[str, Any]] = []
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    all_complete = bool(safe_rows) and all(row.get("cobertura_depositos_completa") is True for row in safe_rows)

    for row in safe_rows:
        store = str(row.get("loja") or "").strip()
        sku = str(row.get("sku") or row.get("id_produto") or "").strip()
        title = str(row.get("produto") or "").strip()
        row_complete = row.get("cobertura_depositos_completa") is True
        partial_quantity = round(_num(row.get("saldo_loja_parcial")), 3)
        reliable_quantity = round(_num(row.get("saldo_loja_total")), 3) if row_complete else None
        ranking.append(
            {
                "sku": sku,
                "title": title,
                "store": store,
                "quantity": reliable_quantity if reliable_quantity is not None else partial_quantity,
                "classified_quantity": partial_quantity,
                "gross_returned": round(_num(row.get("saldo_bruto_retornado")), 3),
                "coverage_complete": row_complete,
                "quantity_reliable": row_complete,
            }
        )
        for deposit in row.get("depositos") if isinstance(row.get("depositos"), list) else []:
            if not isinstance(deposit, dict):
                continue
            included.append(
                {
                    "store": store,
                    "sku": sku,
                    "deposit_id": deposit.get("id"),
                    "name": str(deposit.get("descricao") or "Depósito").strip(),
                    "quantity": round(_num(deposit.get("saldo_fisico")), 3),
                    "included": True,
                }
            )
        for deposit in row.get("depositos_excluidos") if isinstance(row.get("depositos_excluidos"), list) else []:
            if not isinstance(deposit, dict):
                continue
            excluded.append(
                {
                    "store": store,
                    "sku": sku,
                    "deposit_id": deposit.get("id"),
                    "name": str(deposit.get("descricao") or "Depósito não classificado").strip(),
                    "quantity": round(_num(deposit.get("saldo_fisico")), 3),
                    "reason": str(deposit.get("motivo") or deposit.get("motivo_exclusao") or "Excluído do saldo de loja").strip(),
                    "type": str(deposit.get("tipo_detectado") or "").strip(),
                    "included": False,
                }
            )

    ranking.sort(key=lambda item: (-_num(item.get("quantity")), str(item.get("store") or ""), str(item.get("sku") or "")))
    gross_returned = round(sum(_num(row.get("saldo_bruto_retornado")) for row in safe_rows), 3)
    classified_partial = round(sum(_num(row.get("saldo_loja_parcial")) for row in safe_rows), 3)
    store_available = (
        round(sum(_num(row.get("saldo_loja_total")) for row in safe_rows), 3)
        if all_complete
        else None
    )
    excluded_by_reason: dict[str, float] = defaultdict(float)
    for deposit in excluded:
        excluded_by_reason[str(deposit.get("reason") or "Excluído")] += _num(deposit.get("quantity"))

    return {
        "schema": "jk.stock.bling_balances.v1",
        "kind": "bling_stock_balances",
        "stores": [str(store or "").strip() for store in lojas if str(store or "").strip()],
        "metrics": [
            {"key": "gross_returned", "type": "number"},
            {"key": "store_available", "type": "number", "nullable": True},
            {"key": "classified_quantity", "type": "number"},
        ],
        "totals": {
            "skus": len(safe_rows),
            "gross_returned": gross_returned,
            "store_available": store_available,
            "classified_quantity": classified_partial,
            "included_deposits": len(included),
            "excluded_deposits": len(excluded),
            "full_deposits_excluded": sum(1 for item in excluded if str(item.get("type") or "").upper() == "FULL"),
        },
        "ranking": ranking,
        "deposits": {
            "included": included,
            "excluded": excluded,
            "excluded_by_reason": [
                {"reason": reason, "quantity": round(quantity, 3)}
                for reason, quantity in sorted(excluded_by_reason.items())
            ],
        },
        "coverage_complete": all_complete,
        "partial": not all_complete,
        "coverage": {
            "rows": len(safe_rows),
            "rows_complete": sum(1 for row in safe_rows if row.get("cobertura_depositos_completa") is True),
            "rows_incomplete": sum(1 for row in safe_rows if row.get("cobertura_depositos_completa") is not True),
            "included_deposit_rows": len(included),
            "excluded_deposit_rows": len(excluded),
        },
        "pii_included": False,
        "read_only": True,
    }


def _bling_inventory_number(value: Any) -> tuple[float, bool]:
    if value is None or value == "" or isinstance(value, bool):
        return 0.0, False
    try:
        parsed = float(str(value).replace(",", "."))
        return (parsed, True) if math.isfinite(parsed) else (0.0, False)
    except Exception:
        return 0.0, False


def _bling_positive_stock_snapshot(
    access_token: str,
    store: str,
    *,
    deadline: Optional[float],
) -> tuple[dict[str, Any], int]:
    """Count distinct catalog SKUs with positive non-Full stock for one store."""

    limiter = _BlingAdaptiveLimiter(min_interval=0.34, start_interval=0.34)
    reasons: list[str] = []
    products, product_status, products_complete, product_meta = _list_complete_paginated(
        access_token,
        "/produtos",
        {},
        max_records=POSITIVE_STOCK_PRODUCT_LIMIT,
        deadline=deadline,
        limiter=limiter,
    )
    if product_status != 200 or not products_complete:
        reason = str(product_meta.get("reason") or f"catalogo de produtos retornou HTTP {product_status}")
        return {
            "schema": "jk.stock.bling_positive_sku_count.v1",
            "store": store,
            "loja": store,
            "positive_sku_count": None,
            "store_available": None,
            "catalog_products_scanned": len(products),
            "catalog_distinct_skus": 0,
            "balances_requested": 0,
            "balances_returned": 0,
            "duplicate_skus_collapsed": 0,
            "products_without_sku_positive": 0,
            "coverage_complete": False,
            "partial_reason": reason,
            "failed_stage": "products",
            "products_pages": int(product_meta.get("pages") or 0),
            "stock_scope": "bling_non_full_only",
            "full_excluded": True,
            "api_consulted": True,
            "read_only": True,
        }, product_status

    products_by_id: dict[str, dict[str, Any]] = {}
    products_without_id = 0
    services_excluded = 0
    for product in products:
        if str(product.get("tipo") or "").strip().upper() == "S":
            services_excluded += 1
            continue
        product_id = str(product.get("id") or "").strip()
        if not product_id:
            products_without_id += 1
            continue
        products_by_id.setdefault(product_id, product)
    product_ids = list(products_by_id)
    sku_product_ids: dict[str, set[str]] = defaultdict(set)
    for product_id, product in products_by_id.items():
        sku = str(product.get("codigo") or product.get("sku") or "").strip().upper()
        if sku:
            sku_product_ids[sku].add(product_id)
    duplicate_skus = sum(max(0, len(ids) - 1) for ids in sku_product_ids.values())

    base = {
        "schema": "jk.stock.bling_positive_sku_count.v1",
        "store": store,
        "loja": store,
        "catalog_products_scanned": len(products),
        "inventory_products_scanned": len(product_ids),
        "catalog_distinct_skus": len(sku_product_ids),
        "balances_requested": len(product_ids),
        "balances_returned": 0,
        "duplicate_skus_collapsed": duplicate_skus,
        "products_without_id": products_without_id,
        "services_excluded": services_excluded,
        "products_without_sku_positive": 0,
        "products_pages": int(product_meta.get("pages") or 0),
        "deposit_pages": 0,
        "balance_batches": 0,
        "full_deposit_rows_excluded": 0,
        "stock_scope": "bling_non_full_only",
        "full_excluded": True,
        "api_consulted": True,
        "read_only": True,
    }
    if products_without_id:
        base.update({
            "positive_sku_count": None,
            "store_available": None,
            "coverage_complete": False,
            "partial_reason": f"{products_without_id} produto(s) sem ID nao puderam ser consultados",
            "failed_stage": "products",
        })
        return base, 200
    if not product_ids:
        base.update({
            "positive_sku_count": 0,
            "store_available": 0.0,
            "coverage_complete": True,
            "partial_reason": "",
            "failed_stage": "",
        })
        return base, 200

    deposits, deposit_status, deposits_complete, deposit_meta = _list_complete_paginated(
        access_token,
        "/depositos",
        {},
        max_records=POSITIVE_STOCK_DEPOSIT_LIMIT,
        deadline=deadline,
        limiter=limiter,
    )
    base["deposit_pages"] = int(deposit_meta.get("pages") or 0)
    base["deposits_cataloged"] = len(deposits)
    if deposit_status != 200 or not deposits_complete:
        reason = str(deposit_meta.get("reason") or f"catalogo de depositos retornou HTTP {deposit_status}")
        base.update({
            "positive_sku_count": None,
            "store_available": None,
            "coverage_complete": False,
            "partial_reason": reason,
            "failed_stage": "deposits",
        })
        return base, deposit_status
    deposits_by_id = {
        _bling_deposito_id(deposit): deposit
        for deposit in deposits
        if isinstance(deposit, dict) and _bling_deposito_id(deposit)
    }

    product_totals: dict[str, float] = defaultdict(float)
    returned_ids: set[str] = set()
    invalid_quantity_rows = 0
    unclassified_deposit_ids: set[str] = set()
    balance_batches = 0
    for start in range(0, len(product_ids), POSITIVE_STOCK_BATCH_SIZE):
        batch = product_ids[start:start + POSITIVE_STOCK_BATCH_SIZE]
        params = [("idsProdutos[]", product_id) for product_id in batch]
        data, status = _get_json(
            access_token,
            "/estoques/saldos",
            params,
            deadline=deadline,
            limiter=limiter,
        )
        balance_batches += 1
        base["balance_batches"] = balance_batches
        if status != 200 or not isinstance(data, list):
            base.update({
                "positive_sku_count": None,
                "store_available": None,
                "balances_returned": len(returned_ids),
                "coverage_complete": False,
                "partial_reason": _response_error_text(data, status) if status != 200 else "resposta de saldos invalida",
                "failed_stage": "balances",
            })
            return base, status if status != 200 else 502
        for item in data:
            if not isinstance(item, dict):
                reasons.append("linha de saldo invalida")
                continue
            product = item.get("produto") if isinstance(item.get("produto"), dict) else {}
            product_id = str(product.get("id") or item.get("idProduto") or "").strip()
            if not product_id or product_id not in products_by_id:
                reasons.append("saldo retornado para produto nao solicitado")
                continue
            if product_id in returned_ids:
                reasons.append("produto retornado mais de uma vez na consulta de saldos")
                continue
            returned_ids.add(product_id)
            if not str(products_by_id[product_id].get("codigo") or products_by_id[product_id].get("sku") or "").strip():
                balance_sku = str(product.get("codigo") or product.get("sku") or "").strip()
                if balance_sku:
                    products_by_id[product_id] = {**products_by_id[product_id], "codigo": balance_sku}
            for deposit_row in item.get("depositos") if isinstance(item.get("depositos"), list) else []:
                if not isinstance(deposit_row, dict):
                    reasons.append("linha de deposito invalida")
                    continue
                deposit_id = _bling_deposito_id(deposit_row)
                deposit = deposits_by_id.get(deposit_id)
                if not deposit:
                    unclassified_deposit_ids.add(deposit_id or "nao informado")
                    continue
                active = _bling_deposito_ativo(deposit)
                ignored = _bling_deposito_desconsidera_saldo(deposit)
                description = str(deposit.get("descricao") or deposit.get("nome") or "").strip()
                if active is None or ignored is None or not description:
                    unclassified_deposit_ids.add(deposit_id or "nao informado")
                    continue
                if not active or ignored:
                    continue
                if _bling_deposito_eh_full(deposit):
                    base["full_deposit_rows_excluded"] = int(base["full_deposit_rows_excluded"] or 0) + 1
                    continue
                quantity, valid_quantity = _bling_inventory_number(deposit_row.get("saldoFisico"))
                if not valid_quantity:
                    invalid_quantity_rows += 1
                    continue
                product_totals[product_id] += quantity

    missing_balance_ids = set(product_ids) - returned_ids
    if missing_balance_ids:
        reasons.append(f"{len(missing_balance_ids)} produto(s) sem retorno de saldo")
    if unclassified_deposit_ids:
        reasons.append(f"{len(unclassified_deposit_ids)} deposito(s) nao classificado(s)")
    if invalid_quantity_rows:
        reasons.append(f"{invalid_quantity_rows} saldo(s) sem quantidade valida")

    final_sku_product_ids: dict[str, set[str]] = defaultdict(set)
    for product_id, product in products_by_id.items():
        sku = str(product.get("codigo") or product.get("sku") or "").strip().upper()
        if sku:
            final_sku_product_ids[sku].add(product_id)
    base["catalog_distinct_skus"] = len(final_sku_product_ids)
    base["duplicate_skus_collapsed"] = sum(
        max(0, len(ids) - 1) for ids in final_sku_product_ids.values()
    )

    sku_totals: dict[str, float] = defaultdict(float)
    products_without_sku_positive = 0
    for product_id, total in product_totals.items():
        product = products_by_id.get(product_id, {})
        sku = str(product.get("codigo") or product.get("sku") or "").strip().upper()
        if sku:
            sku_totals[sku] += total
        elif total > 0:
            products_without_sku_positive += 1
    coverage_complete = not reasons
    positive_sku_count = sum(1 for total in sku_totals.values() if total > 0)
    store_available = round(sum(product_totals.values()), 3)
    base.update({
        "positive_sku_count": positive_sku_count if coverage_complete else None,
        "positive_sku_count_observed": positive_sku_count,
        "store_available": store_available if coverage_complete else None,
        "store_available_observed": store_available,
        "balances_returned": len(returned_ids),
        "products_without_sku_positive": products_without_sku_positive,
        "coverage_complete": coverage_complete,
        "partial_reason": "; ".join(reasons)[:500],
        "failed_stage": "" if coverage_complete else "balances",
    })
    return base, 200


def tool_bling_positive_stock_sku_count(
    client_id: str,
    message: str,
    loja: Optional[str],
    query_deadline: Optional[float] = None,
    **_: Any,
) -> dict[str, Any]:
    """Return an exact distinct-SKU count only when the whole Bling scan completed."""

    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    tenant = str(client_id or "").strip()
    if not tenant or tenant == "default":
        warnings.append("Sessao sem tenant autenticado; a consulta Bling foi bloqueada por seguranca.")
        return _result(
            "bling_positive_stock_sku_count",
            {"mensagem": message, "loja": loja or ""},
            "inventory_summary",
            rows,
            lojas=[],
            sources=sources,
            warnings=warnings,
            extra={"stock_scope": "bling_non_full_only", "coverage_complete": False},
        )

    stores, store_warnings = _connected_stores(
        tenant,
        loja,
        require_exact=True,
        allow_default_fallback=False,
    )
    warnings.extend(store_warnings)
    deadline = query_deadline if query_deadline is not None else time.monotonic() + QUERY_TIMEOUT_SECONDS
    for store, cfg in stores:
        snapshot, status, _cfg = _call_store(
            tenant,
            store,
            cfg,
            lambda token, _store=store: _bling_positive_stock_snapshot(token, _store, deadline=deadline),
        )
        if not isinstance(snapshot, dict):
            snapshot = {
                "schema": "jk.stock.bling_positive_sku_count.v1",
                "store": store,
                "loja": store,
                "positive_sku_count": None,
                "store_available": None,
                "coverage_complete": False,
                "partial_reason": "resposta agregada invalida",
                "failed_stage": "unknown",
                "stock_scope": "bling_non_full_only",
                "full_excluded": True,
                "api_consulted": True,
                "read_only": True,
            }
        rows.append(snapshot)
        if status != 200:
            warnings.append(f"{store}: contagem de SKUs com estoque falhou: {_response_error_text(snapshot, status)}.")
        elif snapshot.get("coverage_complete") is not True:
            warnings.append(f"{store}: contagem incompleta: {str(snapshot.get('partial_reason') or 'cobertura parcial')[:300]}.")
        sources.extend([
            _source(store, "/produtos", int(snapshot.get("catalog_products_scanned") or 0), {"pages": snapshot.get("products_pages")}, status=200 if snapshot.get("failed_stage") != "products" else status),
            _source(store, "/depositos", int(snapshot.get("deposits_cataloged") or 0), {"pages": snapshot.get("deposit_pages")}, status=200 if snapshot.get("failed_stage") not in {"products", "deposits"} else status),
            _source(store, "/estoques/saldos", int(snapshot.get("balances_returned") or 0), {"batches": snapshot.get("balance_batches"), "requested": snapshot.get("balances_requested")}, status=status),
        ])
    coverage_complete = bool(rows) and all(row.get("coverage_complete") is True for row in rows)
    return _result(
        "bling_positive_stock_sku_count",
        {"mensagem": message, "loja": loja or ""},
        "inventory_summary",
        rows,
        lojas=[store for store, _cfg in stores],
        sources=sources,
        warnings=warnings,
        extra={
            "schema": "jk.stock.bling_positive_sku_count.v1",
            "stock_scope": "bling_non_full_only",
            "full_provider": "mercado_livre_api_only",
            "coverage_complete": coverage_complete,
            "partial_response": not coverage_complete,
        },
    )


def tool_bling_stock_balances(client_id: str, message: str, loja: Optional[str], limit: int = DEFAULT_LIMIT, **_: Any) -> dict[str, Any]:
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    stores, store_warnings = _connected_stores(client_id, loja)
    warnings.extend(store_warnings)
    ref = _extract_ref(message)
    explicit_product_ids = _extract_bling_product_ids(message)
    for nome, cfg in stores:
        product_payload, status_prod, cfg = _call_store(
            client_id,
            nome,
            cfg,
            lambda token: _search_products_payload(token, message, _limit(limit)),
        )
        used = product_payload.get("used") if isinstance(product_payload, dict) and isinstance(product_payload.get("used"), dict) else {}
        products = product_payload.get("products") if status_prod == 200 and isinstance(product_payload, dict) and isinstance(product_payload.get("products"), list) else []
        if status_prod in {401, 403}:
            warnings.append(f"{nome}: busca de produto na Bling falhou: {_response_error_text(product_payload, status_prod)}.")
            sources.append(_source(nome, "/produtos", 0, {"produto_search": used}, status=status_prod))
            # Autenticacao/permissao nao muda entre endpoints da mesma conta.
            # Uma unica falha encerra as chamadas externas desta loja.
            continue
        product_ids = _unique_texts(
            explicit_product_ids
            + [str(item.get("id") or "").strip() for item in products if isinstance(item, dict) and item.get("id")],
            _limit(limit),
        )
        attempts: list[tuple[str, list[tuple[str, Any]]]] = []
        if product_ids:
            params_ids: list[tuple[str, Any]] = []
            _params_add(params_ids, "idsProdutos[]", product_ids[:_limit(limit)])
            attempts.append(("idsProdutos[]", params_ids))
        if ref:
            params_codes: list[tuple[str, Any]] = []
            _params_add(params_codes, "codigos[]", [ref])
            attempts.append(("codigos[]", params_codes))
        if not attempts:
            warnings.append(f"{nome}: informe SKU/codigo ou produto para saldo Bling mais preciso.")
            if not products:
                sources.append(_source(nome, "/estoques/saldos", 0, {"produto_search": used}, status=status_prod))
                continue

        product_by_id = {str(item.get("id") or ""): item for item in products if isinstance(item, dict)}
        catalogo_data, catalogo_status, cfg = _call_store(
            client_id,
            nome,
            cfg,
            lambda token: _list_paginated(token, "/depositos", {}, REPORT_LIMIT, 3),
        )
        catalogo_items = catalogo_data if catalogo_status == 200 and isinstance(catalogo_data, list) else []
        catalogo_por_id = {
            _bling_deposito_id(dep): dep
            for dep in catalogo_items
            if isinstance(dep, dict) and _bling_deposito_id(dep)
        }
        catalogo_disponivel = catalogo_status == 200 and len(catalogo_items) < REPORT_LIMIT
        sources.append(_source(nome, "/depositos", len(catalogo_items), status=catalogo_status))
        if catalogo_status != 200:
            warnings.append(
                f"{nome}: catalogo de depositos retornou HTTP {catalogo_status}; "
                "o saldo de loja nao foi calculado sem classificacao confiavel."
            )
        elif len(catalogo_items) >= REPORT_LIMIT:
            warnings.append(
                f"{nome}: catalogo de depositos atingiu o limite de {REPORT_LIMIT} registros; "
                "o saldo de loja nao foi considerado completo."
            )
        any_success = False
        for attempt_name, params in attempts:
            data, status, cfg = _call_store(client_id, nome, cfg, lambda token, _params=params: _get_json(token, "/estoques/saldos", _params))
            if status != 200:
                warnings.append(f"{nome}: saldo Bling falhou usando {attempt_name}: {_response_error_text(data, status)}.")
                sources.append(_source(nome, "/estoques/saldos", 0, params, status=status))
                continue
            any_success = True
            items = data if isinstance(data, list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                prod = item.get("produto") if isinstance(item.get("produto"), dict) else {}
                pid = str(prod.get("id") or item.get("idProduto") or "").strip()
                cadastro = product_by_id.get(pid, {})
                depositos = item.get("depositos") if isinstance(item.get("depositos"), list) else []
                saldo_bruto_retornado = sum(_num(dep.get("saldoFisico")) for dep in depositos if isinstance(dep, dict))
                saldo_loja_parcial = 0.0
                depositos_loja: list[dict[str, Any]] = []
                depositos_excluidos: list[dict[str, Any]] = []
                depositos_full_ignorados = 0
                cobertura_completa = bool(catalogo_disponivel)
                ids_nao_classificados: list[str] = []

                for dep in depositos:
                    if not isinstance(dep, dict):
                        cobertura_completa = False
                        continue
                    dep_id = _bling_deposito_id(dep)
                    catalogo = catalogo_por_id.get(dep_id)
                    if not catalogo:
                        cobertura_completa = False
                        ids_nao_classificados.append(dep_id or "nao informado")
                        depositos_excluidos.append(
                            _bling_deposito_resultado(
                                dep,
                                None,
                                incluido=False,
                                motivo="ID nao encontrado no catalogo de depositos",
                            )
                        )
                        continue

                    ativo = _bling_deposito_ativo(catalogo)
                    desconsidera = _bling_deposito_desconsidera_saldo(catalogo)
                    descricao = str(catalogo.get("descricao") or catalogo.get("nome") or "").strip()
                    if ativo is None or desconsidera is None or not descricao:
                        cobertura_completa = False
                        ids_nao_classificados.append(dep_id or "nao informado")
                        depositos_excluidos.append(
                            _bling_deposito_resultado(
                                dep,
                                catalogo,
                                incluido=False,
                                motivo="Metadados insuficientes para classificar o deposito",
                            )
                        )
                    elif not ativo:
                        depositos_excluidos.append(
                            _bling_deposito_resultado(
                                dep,
                                catalogo,
                                incluido=False,
                                motivo="Deposito inativo na Bling",
                            )
                        )
                    elif _bling_deposito_eh_full(catalogo):
                        depositos_full_ignorados += 1
                        depositos_excluidos.append(
                            _bling_deposito_resultado(
                                dep,
                                catalogo,
                                incluido=False,
                                motivo="Estoque Full/Fulfillment",
                            )
                        )
                    elif desconsidera:
                        depositos_excluidos.append(
                            _bling_deposito_resultado(
                                dep,
                                catalogo,
                                incluido=False,
                                motivo="desconsiderarSaldo=true na Bling",
                            )
                        )
                    else:
                        saldo_loja_parcial += _num(dep.get("saldoFisico"))
                        depositos_loja.append(_bling_deposito_resultado(dep, catalogo, incluido=True))

                if depositos_full_ignorados:
                    warnings.append(
                        f"{nome}: {depositos_full_ignorados} deposito(s) Full foram ignorados; "
                        "estoque Full deve ser consultado exclusivamente na API do Mercado Livre."
                    )
                if ids_nao_classificados:
                    warnings.append(
                        f"{nome}: deposito(s) {', '.join(_unique_texts(ids_nao_classificados, 20))} nao puderam ser classificados; "
                        "o total de loja ficou indisponivel por seguranca."
                    )
                saldo_total = saldo_loja_parcial if cobertura_completa else None
                rows.append(
                    {
                        "loja": nome,
                        "id_produto": pid,
                        "sku": prod.get("codigo") or cadastro.get("codigo") or ref,
                        "produto": prod.get("nome") or cadastro.get("nome"),
                        "saldo_bruto_retornado": saldo_bruto_retornado,
                        "saldo_total": saldo_total,
                        "saldo_loja_total": saldo_total,
                        "saldo_loja_parcial": saldo_loja_parcial,
                        "saldo_total_confiavel": cobertura_completa,
                        "cobertura_depositos_completa": cobertura_completa,
                        "full_excluido": bool(cobertura_completa and depositos_full_ignorados),
                        "depositos": depositos_loja,
                        "depositos_excluidos": depositos_excluidos,
                    }
                )
            sources.append(_source(nome, "/estoques/saldos", len(items), params, status=status))
            if items:
                break
        if not any_success and status_prod != 200:
            warnings.append(f"{nome}: busca de produto na Bling falhou: {_response_error_text(product_payload, status_prod)}.")
    returned_rows = rows[:_limit(limit)]
    chart_data = _bling_stock_chart_data(returned_rows, [nome for nome, _ in stores])
    return _result(
        "bling_stock_balances",
        {"mensagem": message, "loja": loja or "", "limite": _limit(limit), "ref": ref, "ids_bling": explicit_product_ids},
        "saldos",
        returned_rows,
        lojas=[nome for nome, _ in stores],
        sources=sources,
        warnings=warnings,
        extra={
            "stock_scope": "bling_non_full_only",
            "full_provider": "mercado_livre_api_only",
            "chart_data": chart_data,
        },
    )


def tool_bling_deposits(client_id: str, message: str, loja: Optional[str], limit: int = DEFAULT_LIMIT, **_: Any) -> dict[str, Any]:
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    stores, store_warnings = _connected_stores(client_id, loja)
    warnings.extend(store_warnings)
    for nome, cfg in stores:
        data, status, _ = _call_store(client_id, nome, cfg, lambda token: _list_paginated(token, "/depositos", {"situacao": 1}, _limit(limit), 3))
        if status != 200:
            warnings.append(f"{nome}: depositos retornaram HTTP {status}.")
            sources.append(_source(nome, "/depositos", 0, {"situacao": 1}, status=status))
            continue
        items = data if isinstance(data, list) else []
        for dep in items:
            if not isinstance(dep, dict):
                continue
            nome_dep = str(dep.get("descricao") or "")
            tipo = "FULL" if re.search(r"full|fulfillment|mercado livre|envio", nome_dep, re.I) else ("LOJA_PADRAO" if dep.get("padrao") else "LOJA_SECUNDARIA")
            rows.append(
                {
                    "loja": nome,
                    "id": dep.get("id"),
                    "descricao": nome_dep,
                    "situacao": dep.get("situacao"),
                    "padrao": bool(dep.get("padrao")),
                    "desconsiderar_saldo": bool(dep.get("desconsiderarSaldo")),
                    "tipo_detectado": tipo,
                }
            )
        sources.append(_source(nome, "/depositos", len(items), {"situacao": 1}, status=status))
    return _result("bling_deposits", {"loja": loja or ""}, "depositos", rows[:_limit(limit)], lojas=[nome for nome, _ in stores], sources=sources, warnings=warnings)


def _compact_order(order: dict[str, Any], loja: str) -> dict[str, Any]:
    situacao = order.get("situacao") if isinstance(order.get("situacao"), dict) else {}
    loja_obj = order.get("loja") if isinstance(order.get("loja"), dict) else {}
    return {
        "loja": loja,
        "id": order.get("id"),
        "numero": order.get("numero"),
        "numero_loja": order.get("numeroPedidoLoja"),
        "data": order.get("data") or order.get("dataSaida"),
        "situacao": situacao.get("nome") or order.get("situacao"),
        "valor_total": order.get("total") or order.get("valorTotal"),
        "canal": loja_obj.get("descricao"),
    }


def _items_from_order(order: dict[str, Any]) -> list[dict[str, Any]]:
    items = order.get("itens") if isinstance(order.get("itens"), list) else []
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        produto = item.get("produto") if isinstance(item.get("produto"), dict) else {}
        sku = item.get("codigo") or produto.get("codigo") or produto.get("sku")
        normalized.append(
            {
                "sku": sku or "",
                "produto": item.get("descricao") or produto.get("nome") or produto.get("descricao") or "",
                "quantidade": _num(item.get("quantidade")),
                "valor_unitario": _num(item.get("valor")) or _num(item.get("valorUnitario")),
                "id_produto": produto.get("id"),
            }
        )
    return normalized


def tool_bling_sales_orders(
    client_id: str,
    message: str,
    loja: Optional[str],
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    status: Any = None,
    query_deadline: Optional[float] = None,
    **_: Any,
) -> dict[str, Any]:
    query_limit = _limit(limit, maximum=QUERY_ONLY_LIMIT)
    try:
        offset_value = max(0, min(int(offset or 0), QUERY_ONLY_LIMIT))
    except (TypeError, ValueError):
        offset_value = 0
    page_limit = max(0, min(query_limit, QUERY_ONLY_LIMIT - offset_value))
    status_values = [
        _norm(item)
        for item in re.split(r"[,;|]+", str(status or ""))
        if _norm(item)
    ]
    scan_limit = QUERY_ONLY_LIMIT if status_values else min(QUERY_ONLY_LIMIT, offset_value + page_limit + 1)
    warnings: list[str] = []
    orders: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    top: dict[str, dict[str, Any]] = {}
    stores, store_warnings = _connected_stores(client_id, loja, require_exact=True)
    warnings.extend(store_warnings)
    store_resolution = "ok" if stores else ("store_required" if not str(loja or "").strip() else "store_not_found")
    params_base = {"dataInicial": data_inicio or "", "dataFinal": data_fim or ""}
    detail_cap = min(query_limit, DETAIL_LIMIT)
    deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
    if query_deadline is not None:
        try:
            deadline = min(deadline, float(query_deadline))
        except (TypeError, ValueError):
            pass
    for nome, cfg in stores:
        if page_limit <= 0:
            warnings.append(f"{nome}: o teto read-only de {QUERY_ONLY_LIMIT} pedidos por consulta foi atingido.")
            continue
        rows, status, cfg = _call_store(
            client_id,
            nome,
            cfg,
            lambda token, _params=params_base: _list_paginated(
                token,
                "/pedidos/vendas",
                _params,
                scan_limit,
                10,
                deadline=deadline,
            ),
        )
        if status != 200:
            warnings.append(f"{nome}: pedidos de venda indisponiveis: {_response_error_text(rows, status)}.")
            sources.append(_source(nome, "/pedidos/vendas", 0, params_base, status=status))
            continue
        rows = rows if isinstance(rows, list) else []
        if status_values:
            rows = [
                row
                for row in rows
                if isinstance(row, dict)
                and _norm(
                    ((row.get("situacao") or {}).get("nome") if isinstance(row.get("situacao"), dict) else row.get("situacao"))
                ) in status_values
            ]
            warnings.append(f"{nome}: o filtro de status foi aplicado localmente sobre ate {QUERY_ONLY_LIMIT} pedidos retornados pela API.")
        selected_rows = rows[offset_value : offset_value + page_limit]
        for order in selected_rows:
            if isinstance(order, dict):
                orders.append(_compact_order(order, nome))
        details_done = 0
        for order in selected_rows[:detail_cap]:
            if time.monotonic() >= deadline:
                warnings.append(f"{nome}: detalhes interrompidos ao atingir o timeout total de 60 segundos.")
                break
            oid = str((order or {}).get("id") or "").strip()
            if not oid:
                continue
            detail, detail_status, _ = _call_store(
                client_id,
                nome,
                cfg,
                lambda token, _oid=oid: _get_json(
                    token,
                    f"/pedidos/vendas/{_oid}",
                    deadline=deadline,
                ),
            )
            if detail_status != 200 or not isinstance(detail, dict):
                continue
            details_done += 1
            for item in _items_from_order(detail):
                sku = str(item.get("sku") or "SEM_SKU").strip() or "SEM_SKU"
                if sku not in top:
                    top[sku] = {"sku": sku, "produto": item.get("produto") or "", "quantidade_vendida": 0.0, "valor_total": 0.0, "pedidos": 0, "lojas": set()}
                top[sku]["quantidade_vendida"] += _num(item.get("quantidade"))
                top[sku]["valor_total"] += _num(item.get("quantidade")) * _num(item.get("valor_unitario"))
                top[sku]["pedidos"] += 1
                top[sku]["lojas"].add(nome)
        if len(selected_rows) > details_done:
            warnings.append(f"{nome}: ranking por SKU detalhou {details_done} de {len(selected_rows)} pedido(s) retornados para respeitar limite.")
        sources.append(_source(nome, "/pedidos/vendas", len(rows), params_base, status=status))
        sources.append(_source(nome, "/pedidos/vendas/{idPedidoVenda}", details_done, {"detail_cap": detail_cap}, status=200))
    top_rows = []
    for item in top.values():
        lojas_item = sorted(item.pop("lojas", set()))
        item["lojas"] = lojas_item
        top_rows.append(item)
    top_rows.sort(key=lambda item: (_num(item.get("quantidade_vendida")), _num(item.get("valor_total"))), reverse=True)
    primary_rows = top_rows[:query_limit] if top_rows else orders[:query_limit]
    primary_key = "top_skus" if top_rows else "pedidos"
    provider_statuses = [int((source or {}).get("status") or 0) for source in sources]
    provider_error = ""
    if not orders and provider_statuses:
        if 401 in provider_statuses:
            provider_error = "reconnect_required"
        elif 429 in provider_statuses:
            provider_error = "rate_limited"
        elif 504 in provider_statuses:
            provider_error = "timeout"
        elif any(status >= 500 for status in provider_statuses):
            provider_error = "provider_unavailable"
        elif any(status >= 400 for status in provider_statuses):
            provider_error = "provider_error"
    scanned_count = max(
        [int((source or {}).get("records") or 0) for source in sources if (source or {}).get("path") == "/pedidos/vendas"]
        or [0]
    )
    has_more = bool(scanned_count > offset_value + len(orders) and offset_value + len(orders) < QUERY_ONLY_LIMIT)
    extra = {
        "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
        "provider": "bling",
        "method": "GET",
        "api_primary": True,
        "status": provider_error or store_resolution,
        "pedidos": orders[:query_limit],
        "top_skus": top_rows[:query_limit],
        "ranking_fields": ["sku", "produto", "quantidade_vendida", "valor_total", "pedidos", "lojas"],
        "pedidos_total": len(orders),
        "faturamento_total": sum(_num(item.get("valor_total")) for item in orders),
        "paging": {
            "offset": offset_value,
            "limit": page_limit,
            "returned": len(orders),
            "scanned": scanned_count,
            "next_offset": (
                offset_value + len(orders)
                if has_more
                else None
            ),
            "has_more": has_more,
            "truncated": has_more,
        },
        "coverage": {
            "orders_fetched": len(orders),
            "orders_detailed": sum(int((source or {}).get("records") or 0) for source in sources if (source or {}).get("path") == "/pedidos/vendas/{idPedidoVenda}"),
        },
        "reconnect_required": 401 in provider_statuses,
        "rate_limited": 429 in provider_statuses,
    }
    if provider_error:
        extra["error"] = provider_error
    return _result(
        "bling_sales_orders",
        {"loja": loja or "", "data_inicio": data_inicio, "data_fim": data_fim, "status": status_values, "offset": offset_value, "limite": page_limit},
        primary_key,
        primary_rows,
        lojas=[nome for nome, _ in stores],
        sources=sources,
        warnings=warnings,
        extra=extra,
    )


def tool_bling_sales_order_detail(client_id: str, message: str, loja: Optional[str], limit: int = DEFAULT_LIMIT, **_: Any) -> dict[str, Any]:
    oid = _extract_first_id(message)
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    stores, store_warnings = _connected_stores(client_id, loja, require_exact=True)
    warnings.extend(store_warnings)
    if not oid:
        warnings.append("Informe o ID do pedido Bling para consultar o detalhe.")
    for nome, cfg in stores:
        if not oid:
            continue
        detail, status, _ = _call_store(client_id, nome, cfg, lambda token, _oid=oid: _get_json(token, f"/pedidos/vendas/{_oid}"))
        if status == 200 and isinstance(detail, dict):
            compact = _compact_order(detail, nome)
            compact["itens"] = _items_from_order(detail)
            rows.append(compact)
        else:
            warnings.append(f"{nome}: detalhe do pedido {oid} retornou HTTP {status}.")
        sources.append(_source(nome, "/pedidos/vendas/{idPedidoVenda}", 1 if rows else 0, {"id": oid}, status=status))
    return _result("bling_sales_order_detail", {"id": oid, "loja": loja or ""}, "pedidos", rows[:_limit(limit)], lojas=[nome for nome, _ in stores], sources=sources, warnings=warnings)


def tool_bling_fiscal_nfe(
    client_id: str,
    message: str,
    loja: Optional[str],
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    **_: Any,
) -> dict[str, Any]:
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    stores, store_warnings = _connected_stores(client_id, loja)
    warnings.extend(store_warnings)
    text = _norm(message)
    tipo = None
    if "entrada" in text or "compra" in text:
        tipo = 0
    elif "saida" in text or "venda" in text:
        tipo = 1
    params: dict[str, Any] = {"dataEmissaoInicial": f"{data_inicio} 00:00:00" if data_inicio else "", "dataEmissaoFinal": f"{data_fim} 23:59:59" if data_fim else ""}
    if tipo is not None:
        params["tipo"] = tipo
    key = _extract_invoice_key(message)
    if key:
        params = {"chaveAcesso": key}
    numero = _extract_first_id(message)
    if numero and not key and re.search(r"\b(numero|nf|nfe|nota)\b", text):
        params["numero"] = numero
    for nome, cfg in stores:
        notas, status, _ = _call_store(client_id, nome, cfg, lambda token, _params=params: _list_paginated(token, "/nfe", _params, _limit(limit), 10))
        if status != 200:
            warnings.append(f"{nome}: NF-e retornou HTTP {status}.")
            sources.append(_source(nome, "/nfe", 0, params, status=status))
            continue
        for nota in notas if isinstance(notas, list) else []:
            if not isinstance(nota, dict):
                continue
            rows.append(
                {
                    "loja": nome,
                    "id": nota.get("id"),
                    "numero": nota.get("numero"),
                    "serie": nota.get("serie"),
                    "situacao": nota.get("situacao"),
                    "tipo": nota.get("tipo"),
                    "data_emissao": nota.get("dataEmissao"),
                    "valor": nota.get("valorNota") or nota.get("valor"),
                    "chave_acesso": nota.get("chaveAcesso"),
                }
            )
        sources.append(_source(nome, "/nfe", len(notas or []), params, status=status))
    return _result(
        "bling_fiscal_nfe",
        {"loja": loja or "", "data_inicio": data_inicio, "data_fim": data_fim, "limite": _limit(limit), "tipo": tipo},
        "notas",
        rows[:_limit(limit)],
        lojas=[nome for nome, _ in stores],
        sources=sources,
        warnings=warnings,
        extra={"periodo": {"data_inicio": data_inicio, "data_fim": data_fim}},
    )


def tool_bling_fiscal_nfe_detail(client_id: str, message: str, loja: Optional[str], limit: int = DEFAULT_LIMIT, **kwargs: Any) -> dict[str, Any]:
    nid = _extract_first_id(message)
    key = _extract_invoice_key(message)
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    stores, store_warnings = _connected_stores(client_id, loja)
    warnings.extend(store_warnings)
    for nome, cfg in stores:
        target_id = nid
        if key and not target_id:
            raw = tool_bling_fiscal_nfe(client_id, key, nome, limit=1, **kwargs)
            raw_result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
            notas = raw_result.get("notas") if isinstance(raw_result.get("notas"), list) else []
            if notas:
                target_id = str((notas[0] or {}).get("id") or "")
        if not target_id:
            continue
        detail, status, _ = _call_store(client_id, nome, cfg, lambda token, _id=target_id: _get_json(token, f"/nfe/{_id}"))
        if status == 200 and isinstance(detail, dict):
            detail = dict(detail)
            detail["loja"] = nome
            rows.append(detail)
        else:
            warnings.append(f"{nome}: detalhe da NF-e retornou HTTP {status}.")
        sources.append(_source(nome, "/nfe/{idNotaFiscal}", 1 if rows else 0, {"id": target_id, "chave": key}, status=status))
    if not nid and not key:
        warnings.append("Informe ID ou chave de acesso da NF-e para consultar o detalhe.")
    return _result("bling_fiscal_nfe_detail", {"id": nid, "chave": key, "loja": loja or ""}, "notas", rows[:_limit(limit)], lojas=[nome for nome, _ in stores], sources=sources, warnings=warnings)


def tool_bling_operation_natures(client_id: str, message: str, loja: Optional[str], limit: int = DEFAULT_LIMIT, **_: Any) -> dict[str, Any]:
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    stores, store_warnings = _connected_stores(client_id, loja)
    warnings.extend(store_warnings)
    for nome, cfg in stores:
        naturezas, status, _ = _call_store(client_id, nome, cfg, _bling_listar_naturezas)
        if status != 200:
            warnings.append(f"{nome}: naturezas retornaram HTTP {status}.")
            sources.append(_source(nome, "/naturezas-operacoes", 0, status=status))
            continue
        for nid, descricao in (naturezas or {}).items() if isinstance(naturezas, dict) else []:
            rows.append({"loja": nome, "id": nid, "descricao": descricao})
        sources.append(_source(nome, "/naturezas-operacoes", len(naturezas or {}), status=status))
    return _result("bling_operation_natures", {"loja": loja or ""}, "naturezas", rows[:_limit(limit)], lojas=[nome for nome, _ in stores], sources=sources, warnings=warnings)


def tool_bling_lots(client_id: str, message: str, loja: Optional[str], limit: int = DEFAULT_LIMIT, **_: Any) -> dict[str, Any]:
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    stores, store_warnings = _connected_stores(client_id, loja)
    warnings.extend(store_warnings)
    for nome, cfg in stores:
        product_payload, status_prod, cfg = _call_store(client_id, nome, cfg, lambda token: _search_products_payload(token, message, min(20, _limit(limit))))
        products = product_payload.get("products") if status_prod == 200 and isinstance(product_payload, dict) and isinstance(product_payload.get("products"), list) else []
        if status_prod != 200 or not isinstance(products, list) or not products:
            detail = _response_error_text(product_payload, status_prod) if status_prod != 200 else "produto nao encontrado."
            warnings.append(f"{nome}: nao encontrei produto Bling para consultar lotes: {detail}")
            sources.append(_source(nome, "/produtos", 0, status=status_prod))
            continue
        total = 0
        for product in products[:20]:
            pid = str((product or {}).get("id") or "").strip()
            if not pid:
                continue
            lotes, status, _ = _call_store(client_id, nome, cfg, lambda token, _pid=pid: _bling_listar_lotes_produto(token, _pid))
            if status != 200:
                warnings.append(f"{nome}: lotes do produto {pid} retornaram HTTP {status}.")
                continue
            for lote in lotes if isinstance(lotes, list) else []:
                if isinstance(lote, dict):
                    row = dict(lote)
                    row["loja"] = nome
                    row["id_produto"] = pid
                    row["sku"] = product.get("codigo")
                    row["produto"] = product.get("nome")
                    rows.append(row)
                    total += 1
        sources.append(_source(nome, "/produtos/lotes", total, status=200))
    return _result("bling_lots", {"mensagem": message, "loja": loja or ""}, "lotes", rows[:_limit(limit)], lojas=[nome for nome, _ in stores], sources=sources, warnings=warnings)


def tool_bling_lot_movements(client_id: str, message: str, loja: Optional[str], limit: int = DEFAULT_LIMIT, **kwargs: Any) -> dict[str, Any]:
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    lote_id = _extract_first_id(message)
    stores, store_warnings = _connected_stores(client_id, loja)
    warnings.extend(store_warnings)
    lot_ids: list[tuple[str, str, dict[str, Any]]] = []
    if lote_id:
        for nome, _cfg in stores:
            lot_ids.append((nome, lote_id, {}))
    else:
        raw_lots = tool_bling_lots(client_id, message, loja, limit=min(20, _limit(limit)), **kwargs)
        lot_result = raw_lots.get("result") if isinstance(raw_lots.get("result"), dict) else {}
        warnings.extend(w for w in lot_result.get("warnings", []) if isinstance(w, str))
        for lote in lot_result.get("lotes", []) if isinstance(lot_result.get("lotes"), list) else []:
            if isinstance(lote, dict) and lote.get("id"):
                lot_ids.append((str(lote.get("loja") or ""), str(lote.get("id")), lote))
    cfg_by_store = {nome: cfg for nome, cfg in stores}
    for nome, lid, lote in lot_ids[: min(40, _limit(limit))]:
        cfg = cfg_by_store.get(nome)
        if not cfg:
            continue
        movs, status, _ = _call_store(client_id, nome, cfg, lambda token, _lid=lid: _bling_listar_lancamentos_lote(token, _lid))
        if status != 200:
            warnings.append(f"{nome}: lancamentos do lote {lid} retornaram HTTP {status}.")
            sources.append(_source(nome, "/produtos/lotes/{idLote}/lancamentos", 0, {"idLote": lid}, status=status))
            continue
        for mov in movs if isinstance(movs, list) else []:
            if isinstance(mov, dict):
                row = dict(mov)
                row["loja"] = nome
                row["id_lote"] = lid
                row["sku"] = lote.get("sku")
                row["produto"] = lote.get("produto")
                rows.append(row)
        sources.append(_source(nome, "/produtos/lotes/{idLote}/lancamentos", len(movs or []), {"idLote": lid}, status=status))
    if not rows and not lot_ids:
        warnings.append("Informe ID de lote ou SKU/produto para consultar lancamentos de lote.")
    return _result("bling_lot_movements", {"mensagem": message, "loja": loja or ""}, "lancamentos", rows[:_limit(limit)], lojas=[nome for nome, _ in stores], sources=sources, warnings=warnings)


def _sum_finance(rows: list[dict[str, Any]]) -> float:
    total = 0.0
    for item in rows:
        if isinstance(item, dict):
            total += _num(item.get("valor") or item.get("valorTotal") or item.get("saldo"))
    return total


def tool_bling_finance_summary(
    client_id: str,
    message: str,
    loja: Optional[str],
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    **_: Any,
) -> dict[str, Any]:
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    stores, store_warnings = _connected_stores(client_id, loja)
    warnings.extend(store_warnings)
    text = _norm(message)
    wants_receber = "receber" in text or "receita" in text or "financeiro" in text or "conta" in text
    wants_pagar = "pagar" in text or "despesa" in text or "financeiro" in text or "conta" in text
    wants_caixa = "caixa" in text or "banco" in text or "financeiro" in text
    if not any((wants_receber, wants_pagar, wants_caixa)):
        wants_receber = wants_pagar = wants_caixa = True
    for nome, cfg in stores:
        if wants_receber:
            params = {"tipoFiltroData": "V", "dataInicial": data_inicio or "", "dataFinal": data_fim or ""}
            contas, status, _ = _call_store(client_id, nome, cfg, lambda token, _params=params: _list_paginated(token, "/contas/receber", _params, _limit(limit), 5))
            contas = contas if status == 200 and isinstance(contas, list) else []
            rows.append({"loja": nome, "tipo": "contas_receber", "quantidade": len(contas), "valor_total": _sum_finance(contas), "amostra": contas[:10]})
            sources.append(_source(nome, "/contas/receber", len(contas), params, status=status))
            if status != 200:
                warnings.append(f"{nome}: contas a receber retornaram HTTP {status}.")
        if wants_pagar:
            params = {"dataVencimentoInicial": data_inicio or "", "dataVencimentoFinal": data_fim or ""}
            contas, status, _ = _call_store(client_id, nome, cfg, lambda token, _params=params: _list_paginated(token, "/contas/pagar", _params, _limit(limit), 5))
            contas = contas if status == 200 and isinstance(contas, list) else []
            rows.append({"loja": nome, "tipo": "contas_pagar", "quantidade": len(contas), "valor_total": _sum_finance(contas), "amostra": contas[:10]})
            sources.append(_source(nome, "/contas/pagar", len(contas), params, status=status))
            if status != 200:
                warnings.append(f"{nome}: contas a pagar retornaram HTTP {status}.")
        if wants_caixa:
            params = {"dataInicial": data_inicio or "", "dataFinal": data_fim or ""}
            caixas, status, _ = _call_store(client_id, nome, cfg, lambda token, _params=params: _list_paginated(token, "/caixas", _params, _limit(limit), 5))
            caixas = caixas if status == 200 and isinstance(caixas, list) else []
            rows.append({"loja": nome, "tipo": "caixas", "quantidade": len(caixas), "valor_total": _sum_finance(caixas), "amostra": caixas[:10]})
            sources.append(_source(nome, "/caixas", len(caixas), params, status=status))
            if status != 200:
                warnings.append(f"{nome}: caixas retornaram HTTP {status}.")
    return _result(
        "bling_finance_summary",
        {"loja": loja or "", "data_inicio": data_inicio, "data_fim": data_fim},
        "financeiro",
        rows,
        lojas=[nome for nome, _ in stores],
        sources=sources,
        warnings=warnings,
        extra={"periodo": {"data_inicio": data_inicio, "data_fim": data_fim}},
    )


_RESOURCE_KEYWORDS = [
    (("contas a receber", "receber", "boleto"), "/contas/receber"),
    (("contas a pagar", "pagar", "despesa"), "/contas/pagar"),
    (("caixa", "banco"), "/caixas"),
    (("canal", "canais", "loja virtual"), "/canais-venda"),
    (("contato", "cliente", "fornecedor"), "/contatos"),
    (("categoria produto", "categorias produto"), "/categorias/produtos"),
    (("categoria receita", "categoria despesa"), "/categorias/receitas-despesas"),
    (("deposito", "depositos"), "/depositos"),
    (("saldo", "estoque"), "/estoques/saldos"),
    (("natureza", "operacao"), "/naturezas-operacoes"),
    (("nfse", "servico"), "/nfse"),
    (("nfce", "consumidor"), "/nfce"),
    (("nfe", "nota", "fiscal"), "/nfe"),
    (("pedido de compra", "compras"), "/pedidos/compras"),
    (("pedido", "venda", "pedidos"), "/pedidos/vendas"),
    (("produto", "sku", "gtin", "ean"), "/produtos"),
    (("lote", "validade"), "/produtos/lotes"),
    (("proposta",), "/propostas-comerciais"),
    (("vendedor", "vendedores"), "/vendedores"),
    (("logistica", "etiqueta", "remessa"), "/logisticas"),
]


def _resource_from_message(message: str) -> str:
    text = _norm(message)
    direct = re.search(r"(/(?:[a-z0-9-]+/?)+(?:\{[a-zA-Z0-9]+\})?)", str(message or ""))
    if direct:
        candidate = direct.group(1).rstrip("/")
        for item in BLING_READ_ONLY_RESOURCES:
            if item["path"] == candidate:
                return candidate
    for keywords, path in _RESOURCE_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return path
    return ""


def _params_for_resource(path: str, message: str, data_inicio: Optional[str], data_fim: Optional[str]) -> Any:
    ref = _extract_ref(message)
    explicit_ids = _extract_bling_product_ids(message)
    key = _extract_invoice_key(message)
    params: list[tuple[str, Any]] = []
    if path in {"/pedidos/vendas", "/pedidos/compras", "/propostas-comerciais"}:
        if data_inicio:
            params.append(("dataInicial", data_inicio))
        if data_fim:
            params.append(("dataFinal", data_fim))
    elif path in {"/nfe", "/nfce", "/nfse"}:
        if data_inicio:
            params.append(("dataEmissaoInicial", f"{data_inicio} 00:00:00" if path == "/nfe" else data_inicio))
        if data_fim:
            params.append(("dataEmissaoFinal", f"{data_fim} 23:59:59" if path == "/nfe" else data_fim))
        if key:
            params.append(("chaveAcesso", key))
    elif path == "/contas/receber":
        params.extend([("tipoFiltroData", "V"), ("dataInicial", data_inicio or ""), ("dataFinal", data_fim or "")])
    elif path == "/contas/pagar":
        params.extend([("dataVencimentoInicial", data_inicio or ""), ("dataVencimentoFinal", data_fim or "")])
    elif path == "/caixas":
        params.extend([("dataInicial", data_inicio or ""), ("dataFinal", data_fim or "")])
    elif path == "/produtos":
        if explicit_ids:
            _params_add(params, "idsProdutos[]", explicit_ids)
        elif ref:
            params.append(("codigos[]", ref))
        else:
            term = _extract_name_term(message)
            if term:
                params.append(("nome", term))
    elif path == "/estoques/saldos":
        if explicit_ids:
            _params_add(params, "idsProdutos[]", explicit_ids)
        if ref:
            params.append(("codigos[]", ref))
    elif path == "/produtos/lotes":
        if explicit_ids:
            _params_add(params, "idsProdutos[]", explicit_ids)
        elif ref.isdigit():
            params.append(("idsProdutos[]", ref))
    elif path == "/depositos":
        params.append(("situacao", 1))
    return params


def tool_bling_resource_query(
    client_id: str,
    message: str,
    loja: Optional[str],
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    **_: Any,
) -> dict[str, Any]:
    path = _resource_from_message(message)
    if not path:
        resources = BLING_READ_ONLY_RESOURCES[:_limit(limit, 30, 80)]
        return _result(
            "bling_resource_query",
            {"mensagem": message, "loja": loja or ""},
            "resources",
            resources,
            lojas=[],
            sources=[],
            warnings=["Nao identifiquei uma rota Bling especifica; retornando catalogo read-only disponivel."],
            extra={"total_resources": len(BLING_READ_ONLY_RESOURCES), "mutating_routes_blocked": True},
        )
    if "{" in path:
        rid = _extract_first_id(message) or _extract_invoice_key(message)
        if not rid:
            return _result(
                "bling_resource_query",
                {"path": path, "loja": loja or ""},
                "rows",
                [],
                lojas=[],
                sources=[],
                warnings=[f"A rota {path} exige ID/chave para consulta read-only."],
            )
        concrete_path = re.sub(r"\{[^}]+\}", rid, path, count=1)
    else:
        concrete_path = path
    stores, warnings = _connected_stores(client_id, loja)
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    params = _params_for_resource(path, message, data_inicio, data_fim)
    for nome, cfg in stores:
        if "{" in path:
            data, status, _ = _call_store(client_id, nome, cfg, lambda token, _path=concrete_path, _params=params: _get_json(token, _path, _params))
            records = 1 if status == 200 and isinstance(data, dict) else 0
            if records:
                row = dict(data)
                row["loja"] = nome
                rows.append(row)
            else:
                warnings.append(f"{nome}: {path} retornou HTTP {status}.")
        else:
            data, status, _ = _call_store(client_id, nome, cfg, lambda token, _path=path, _params=params: _list_paginated(token, _path, _params, _limit(limit), 5))
            if status == 200 and isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        row = dict(item)
                        row["loja"] = nome
                        rows.append(row)
                records = len(data)
            else:
                records = 0
                warnings.append(f"{nome}: {path} retornou HTTP {status}.")
        sources.append(_source(nome, path, records, params, status=status))
    return _result(
        "bling_resource_query",
        {"path": path, "concrete_path": concrete_path, "loja": loja or "", "data_inicio": data_inicio, "data_fim": data_fim},
        "rows",
        rows[:_limit(limit)],
        lojas=[nome for nome, _ in stores],
        sources=sources,
        warnings=warnings,
        extra={"resource": _resource_meta(path), "mutating_routes_blocked": True},
    )


BLING_TOOL_EXECUTORS: dict[str, Callable[..., dict[str, Any]]] = {
    "bling_status": tool_bling_status,
    "bling_products": tool_bling_products,
    "bling_fiscal_product": tool_bling_fiscal_product,
    "bling_positive_stock_sku_count": tool_bling_positive_stock_sku_count,
    "bling_stock_balances": tool_bling_stock_balances,
    "bling_deposits": tool_bling_deposits,
    "bling_sales_orders": tool_bling_sales_orders,
    "bling_sales_order_detail": tool_bling_sales_order_detail,
    "bling_fiscal_nfe": tool_bling_fiscal_nfe,
    "bling_fiscal_nfe_detail": tool_bling_fiscal_nfe_detail,
    "bling_operation_natures": tool_bling_operation_natures,
    "bling_lots": tool_bling_lots,
    "bling_lot_movements": tool_bling_lot_movements,
    "bling_finance_summary": tool_bling_finance_summary,
    "bling_resource_query": tool_bling_resource_query,
}


def execute_bling_tool(
    client_id: str,
    tool_id: str,
    message: str,
    loja: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    status: Any = None,
    sku: Optional[str] = None,
    item_id: Optional[str] = None,
    id_pedido: Optional[str] = None,
    force_refresh: bool = False,
    query_deadline: Optional[float] = None,
) -> dict[str, Any]:
    executor = BLING_TOOL_EXECUTORS.get(str(tool_id or ""))
    if not executor:
        return {
            "function": str(tool_id or "bling_unknown"),
            "arguments": {"tool_id": tool_id},
            "result": {"error": f"Ferramenta Bling desconhecida: {tool_id}", "records": 0, "read_only": True},
        }
    started = time.time()
    try:
        raw = executor(
            client_id=client_id,
            message=message,
            loja=loja,
            data_inicio=data_inicio,
            data_fim=data_fim,
            limit=limit,
            offset=offset,
            status=status,
            sku=sku,
            item_id=item_id,
            id_pedido=id_pedido,
            force_refresh=force_refresh,
            query_deadline=query_deadline,
        )
    except Exception as exc:
        logger.exception("[Codex Bling] Falha ao executar %s", tool_id)
        raw = {
            "function": tool_id,
            "arguments": {"loja": loja or "", "data_inicio": data_inicio, "data_fim": data_fim},
            "result": {"error": str(exc)[:300], "records": 0, "read_only": True, "warnings": [str(exc)[:300]]},
        }
    result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
    result["elapsed_ms"] = int((time.time() - started) * 1000)
    result.setdefault("read_only", True)
    raw["result"] = result
    return raw


def bling_resources_public() -> dict[str, Any]:
    return {
        "success": True,
        "generated_at": _now(),
        "read_only": True,
        "mutating_routes_blocked": True,
        "resources": BLING_READ_ONLY_RESOURCES,
        "read_only_exceptions": BLING_READ_ONLY_EXCEPTIONS,
        "total": len(BLING_READ_ONLY_RESOURCES),
    }


__all__ = [
    "BLING_READ_ONLY_RESOURCES",
    "BLING_READ_ONLY_EXCEPTIONS",
    "execute_bling_tool",
    "bling_resources_public",
]
