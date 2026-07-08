"""Internal slice for ia_tools."""

from __future__ import annotations

from __future__ import annotations
import asyncio
import base64
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import parse_qs, quote, quote_plus, unquote, urlencode, urlparse
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import dotenv_values
from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from jose import JWTError
from backend.schemas import (
    IAAgentQueryRequest,
    IAChatRequest,
    IASalvarConversaRequest,
    IARagIndexRequest,
    IARagReindexRequest,
)
import base64
import copy
import csv
import functools
import hashlib
import html as html_lib
import io
import json
import logging
import math
import os
import random
import re
import sqlite3
import tempfile
import threading
import time
import traceback
import unicodedata
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from fastapi import Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.ia_common import *
from backend.services.ia_context import get_tenant_id, get_tenant_path
from backend.services.ia_state import *


def configure_ia_tools_marketplaces_runtime(runtime_module=None, peers=None):
    _configure_common = globals().get("configure_promocoes_common_runtime")
    if callable(_configure_common):
        try:
            _configure_common(runtime_module)
        except TypeError:
            _configure_common()
    runtime = bind_runtime_globals(globals(), runtime_module)
    if peers:
        globals().update(peers)
    return runtime


configure_ia_tools_marketplaces_runtime()


def _ia_lojas_bling_conectadas(client_id: str) -> list[str]:
    lojas = []
    for loja in carregar_lojas(client_id) or []:
        if not isinstance(loja, dict):
            continue
        nome = str(loja.get("nome") or "").strip()
        integracoes = loja.get("integracoes") or {}
        cfg = integracoes.get("bling") if isinstance(integracoes, dict) else {}
        if nome and isinstance(cfg, dict) and str(cfg.get("access_token") or "").strip():
            lojas.append(nome)
    return lojas


def _ia_lojas_com_integracao(client_id: str, provedor: str, loja: Optional[str] = None) -> list[str]:
    provedor_norm = str(provedor or "").strip().lower()
    conectadas = _ia_lojas_ml_conectadas(client_id) if provedor_norm in {"ml", "mercadolivre", "mercado_livre"} else _ia_lojas_bling_conectadas(client_id)
    loja_txt = str(loja or "").strip()
    if not loja_txt or loja_txt in {"__todas", "Todas as lojas"}:
        return conectadas[:5]

    alvo_norm = _normalizar_texto(loja_txt)
    for nome in conectadas:
        if _normalizar_texto(nome) == alvo_norm:
            return [nome]
    for nome in conectadas:
        nome_norm = _normalizar_texto(nome)
        if alvo_norm and (alvo_norm in nome_norm or nome_norm in alvo_norm):
            return [nome]
    return conectadas[:5]


def _ia_obter_cfg_bling(client_id: str, nome_loja: str) -> dict:
    loja = buscar_loja(client_id, nome_loja)
    if not loja:
        raise HTTPException(status_code=404, detail="Loja nao encontrada")

    integracoes = loja.get("integracoes") or {}
    cfg = dict(integracoes.get("bling") or {})
    if not cfg:
        raise HTTPException(status_code=400, detail="Integracao Bling nao configurada para esta loja")

    cfg["id"] = cfg.get("id") or cfg.get("client_id")
    cfg["secret"] = cfg.get("secret") or cfg.get("client_secret")
    if not cfg.get("access_token"):
        raise HTTPException(status_code=401, detail="Token Bling ausente. Refaca a autenticacao OAuth.")
    return cfg


def _ia_tool_get_integrations_status(client_id: str, loja: Optional[str] = None) -> Optional[dict]:
    try:
        loja_filtro = str(loja or "").strip()
        registros = []
        for loja_cfg in carregar_lojas(client_id) or []:
            if not isinstance(loja_cfg, dict):
                continue
            nome = str(loja_cfg.get("nome") or "").strip()
            if not nome:
                continue
            if loja_filtro and loja_filtro not in {"__todas", "Todas as lojas"}:
                if _normalizar_texto(loja_filtro) not in _normalizar_texto(nome):
                    continue
            integracoes = loja_cfg.get("integracoes") or {}
            cfg_ml = integracoes.get("mercadolivre") if isinstance(integracoes, dict) else {}
            cfg_bling = integracoes.get("bling") if isinstance(integracoes, dict) else {}
            registros.append({
                "loja": nome,
                "mercado_livre_conectado": bool(isinstance(cfg_ml, dict) and str(cfg_ml.get("access_token") or "").strip()),
                "mercado_livre_user_id": str((cfg_ml or {}).get("user_id") or "").strip() if isinstance(cfg_ml, dict) else "",
                "bling_conectado": bool(isinstance(cfg_bling, dict) and str(cfg_bling.get("access_token") or "").strip()),
                "bling_cliente_configurado": bool(isinstance(cfg_bling, dict) and str((cfg_bling or {}).get("id") or (cfg_bling or {}).get("client_id") or "").strip()),
            })

        return {
            "function": "get_integrations_status",
            "arguments": {"loja": loja_filtro or ""},
            "result": {
                "lojas": registros,
                "total_lojas": len(registros),
                "ml_conectadas": sum(1 for item in registros if item.get("mercado_livre_conectado")),
                "bling_conectadas": sum(1 for item in registros if item.get("bling_conectado")),
            },
        }
    except Exception as exc:
        logger.warning("[IA TOOLS] Falha ao consultar status das integracoes: %s", exc)
        return None


def _ia_ml_precisa_descricao(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    return any(chave in texto for chave in ("DESCRICAO", "DESCRICAO DO ANUNCIO", "TEXTO DO ANUNCIO", "ANUNCIO COMPLETO"))


def _ia_ml_item_resumo(item: dict, loja: str, descricao: str = "") -> dict:
    variacoes = []
    for var in (item.get("variations") or [])[:8]:
        if not isinstance(var, dict):
            continue
        variacoes.append({
            "id": str(var.get("id") or "").strip(),
            "sku": _ml_extrair_sku(var),
            "price": var.get("price"),
            "available_quantity": var.get("available_quantity"),
            "sold_quantity": var.get("sold_quantity"),
        })
    return {
        "loja": loja,
        "id": str(item.get("id") or "").strip(),
        "title": str(item.get("title") or "").strip(),
        "status": str(item.get("status") or "").strip(),
        "sub_status": item.get("sub_status") or [],
        "seller_sku": _ml_extrair_sku(item),
        "price": item.get("price"),
        "base_price": item.get("base_price"),
        "original_price": item.get("original_price"),
        "available_quantity": item.get("available_quantity"),
        "sold_quantity": item.get("sold_quantity"),
        "listing_type_id": str(item.get("listing_type_id") or "").strip(),
        "category_id": str(item.get("category_id") or "").strip(),
        "permalink": str(item.get("permalink") or "").strip(),
        "thumbnail": str(item.get("thumbnail") or "").strip(),
        "health": item.get("health"),
        "catalog_listing": bool(item.get("catalog_listing")),
        "variations": variacoes,
        "description": descricao[:1500] if descricao else "",
    }


def _ia_ml_obter_descricao_item(client_id: str, loja: str, cfg: dict, item_id: str) -> tuple[str, dict]:
    item_id_txt = str(item_id or "").strip()
    if not item_id_txt:
        return "", cfg
    try:
        resp, cfg = _ml_api_request(
            client_id,
            loja,
            cfg,
            "GET",
            f"https://api.mercadolibre.com/items/{item_id_txt}/description",
            timeout=15,
        )
        if resp.status_code != 200:
            return "", cfg
        data = resp.json() or {}
        return str(data.get("plain_text") or data.get("text") or "").strip(), cfg
    except Exception as exc:
        logger.warning("[IA TOOLS] Falha ao consultar descricao ML %s/%s: %s", loja, item_id_txt, exc)
        return "", cfg


def _ia_ml_listar_anuncios(client_id: str, loja: str, cfg: dict, status_item: str, limite: int = 10) -> tuple[list[dict], dict]:
    user_id = str(cfg.get("user_id") or "").strip()
    if not user_id:
        return [], cfg
    resp, cfg = _ml_api_request(
        client_id,
        loja,
        cfg,
        "GET",
        f"https://api.mercadolibre.com/users/{user_id}/items/search",
        params={"offset": 0, "limit": max(1, min(int(limite or 10), 20)), "status": status_item},
        timeout=20,
    )
    if resp.status_code != 200:
        return [], cfg
    ids = []
    for item in (resp.json() or {}).get("results") or []:
        ids.append(str((item.get("id") if isinstance(item, dict) else item) or "").strip())
    ids = [item_id for item_id in ids if item_id]
    if not ids:
        return [], cfg
    return _ml_buscar_itens_batch(client_id, loja, cfg, ids[:limite])


def _ia_tool_get_mercado_livre_listing(
    client_id: str,
    mensagem: str,
    loja: Optional[str] = None,
    produto_tool: Optional[dict] = None,
    limite: int = 8,
    incluir_descricao: bool = False,
) -> Optional[dict]:
    try:
        item_ids = _ia_extrair_item_ids_ml(mensagem)
        sku = _ia_tool_resolver_sku(client_id, mensagem, produto_tool)
        ref = _ia_extrair_referencia_produto_mensagem(mensagem)
        if not sku and ref.get("sku"):
            sku = _normalizar_sku_mes(str(ref.get("sku") or "").strip()).upper()

        texto_norm = _normalizar_texto(mensagem)
        listar_sem_ref = bool(
            not item_ids
            and not sku
            and any(chave in texto_norm for chave in ("ANUNCIOS", "ANUNCIO", "ITENS ATIVOS", "ITENS PAUSADOS", "LISTE", "LISTAR"))
        )
        if not item_ids and not sku and not listar_sem_ref:
            return None

        lojas = _ia_lojas_com_integracao(client_id, "mercadolivre", loja)
        if not lojas:
            return {
                "function": "get_mercado_livre_listing",
                "arguments": {"sku": sku, "item_ids": item_ids, "loja": loja or ""},
                "result": {"found": False, "matches": [], "message": "Nenhuma loja com Mercado Livre conectado."},
            }

        matches = []
        erros = []
        incluir_descricao = bool(incluir_descricao or _ia_ml_precisa_descricao(mensagem))
        status_item = "paused" if "PAUSAD" in texto_norm else "active"
        for nome_loja in lojas:
            if len(matches) >= limite:
                break
            try:
                cfg = _obter_cfg_ml(client_id, nome_loja)
                itens = []
                if item_ids:
                    itens, cfg = _ml_buscar_itens_batch(client_id, nome_loja, cfg, item_ids[:limite])
                elif sku:
                    itens, cfg = _ml_favoritos_buscar_itens_por_sku(client_id, nome_loja, cfg, sku)
                elif listar_sem_ref:
                    itens, cfg = _ia_ml_listar_anuncios(client_id, nome_loja, cfg, status_item, limite=limite)

                for item in itens:
                    if not isinstance(item, dict) or len(matches) >= limite:
                        continue
                    descricao = ""
                    if incluir_descricao:
                        descricao, cfg = _ia_ml_obter_descricao_item(client_id, nome_loja, cfg, str(item.get("id") or ""))
                    matches.append(_ia_ml_item_resumo(item, nome_loja, descricao=descricao))
            except Exception as exc:
                erros.append({"loja": nome_loja, "erro": str(exc)[:180]})
                logger.warning("[IA TOOLS] Falha ao consultar Mercado Livre para IA (%s): %s", nome_loja, exc)

        return {
            "function": "get_mercado_livre_listing",
            "arguments": {
                "sku": sku,
                "item_ids": item_ids,
                "loja": loja or "",
                "modo": "lista" if listar_sem_ref else ("item_id" if item_ids else "sku"),
            },
            "result": {
                "found": bool(matches),
                "matches": matches,
                "errors": erros[:3],
                "read_only": True,
            },
        }
    except Exception as exc:
        logger.warning("[IA TOOLS] Falha geral ao consultar Mercado Livre: %s", exc)
        return None


def _ia_bling_valor_tributario(produto: dict, campo: str) -> str:
    trib = produto.get("tributacao") if isinstance(produto, dict) else {}
    candidatos = []
    if isinstance(trib, dict):
        candidatos.append(trib.get(campo))
    candidatos.append((produto or {}).get(campo))
    for valor in candidatos:
        if isinstance(valor, dict):
            valor = valor.get("codigo") or valor.get("id") or valor.get("valor")
        valor_txt = str(valor or "").strip()
        if valor_txt:
            return valor_txt
    return ""


def _ia_buscar_imagem_ml_sku(client_id: str, sku: str, produto: dict | None = None, cadastro: dict | None = None) -> dict:
    sku_ref = str(sku or "").strip()
    item_ids = _ia_extrair_item_ids_ml(
        (produto or {}).get("mlb_principal"),
        (produto or {}).get("mlb_ids"),
        (cadastro or {}).get("mlb_principal"),
        (cadastro or {}).get("mlb_ids"),
    )

    for nome_loja in _ia_lojas_ml_conectadas(client_id):
        try:
            cfg = _obter_cfg_ml(client_id, nome_loja)

            if item_ids:
                itens, cfg = _ml_buscar_itens_batch(client_id, nome_loja, cfg, item_ids[:10])
                for item in itens:
                    if not isinstance(item, dict):
                        continue
                    imagem = _ml_perguntas_foto_item(item)
                    if imagem:
                        return {
                            "imagem_url": imagem,
                            "item_id": str(item.get("id") or "").strip(),
                            "titulo": str(item.get("title") or "").strip(),
                            "loja": nome_loja,
                        }

            if sku_ref:
                itens, cfg = _ml_favoritos_buscar_itens_por_sku(client_id, nome_loja, cfg, sku_ref)
                for item in itens:
                    if not isinstance(item, dict):
                        continue
                    imagem = _ml_perguntas_foto_item(item)
                    if imagem:
                        return {
                            "imagem_url": imagem,
                            "item_id": str(item.get("id") or "").strip(),
                            "titulo": str(item.get("title") or "").strip(),
                            "loja": nome_loja,
                        }
        except Exception as exc:
            logger.warning("[IA TOOLS] Falha ao buscar imagem do SKU %s no Mercado Livre (%s): %s", sku_ref, nome_loja, exc)
            continue

    return {}


def _ia_montar_prompt_geracao_imagem_sku(client_id: str, mensagem: str) -> tuple[str, dict]:
    produto_tool = _ia_tool_get_product_data(client_id, mensagem, limite=1)
    result = (produto_tool or {}).get("result") or {}
    matches = result.get("matches") or []
    produto = matches[0] if matches else {}
    sku = str(produto.get("sku") or result.get("canonical_sku") or "").strip()
    nome = str(produto.get("nome") or "").strip()
    categoria = str(produto.get("categoria") or "").strip()
    marca = str(produto.get("marca") or "").strip()

    cadastro = _ia_tool_get_product_registry_info(client_id, mensagem, produto_tool=produto_tool, limite=1) if sku else None
    cadastro_matches = ((cadastro or {}).get("result") or {}).get("matches") or []
    if cadastro_matches:
        cad = cadastro_matches[0] or {}
        nome = nome or str(cad.get("nome") or "").strip()
        categoria = categoria or str(cad.get("categoria") or "").strip()
        marca = marca or str(cad.get("marca") or "").strip()
        descricao = str(cad.get("descricao") or "").strip()
    else:
        descricao = ""

    detalhes = []
    if sku:
        detalhes.append(f"SKU: {sku}")
    if nome:
        detalhes.append(f"Produto: {nome}")
    if categoria:
        detalhes.append(f"Categoria: {categoria}")
    if marca:
        detalhes.append(f"Marca: {marca}")
    if descricao:
        detalhes.append(f"Descricao do cadastro: {descricao[:700]}")

    prompt = (
        "Crie uma imagem comercial realista e profissional para e-commerce/marketplace.\n"
        "Use a solicitacao do usuario como direcao criativa principal.\n"
        "Nao inclua textos, logos, marcas d'agua, codigos, SKU escrito ou legendas na imagem.\n"
        "Mostre o produto de forma clara, com iluminacao limpa e contexto coerente.\n\n"
        f"Solicitacao do usuario: {mensagem}\n"
        + ("\nDados do produto no cadastro:\n" + "\n".join(detalhes) if detalhes else "")
    ).strip()

    return prompt, {
        "sku": sku,
        "produto": nome,
        "categoria": categoria,
        "marca": marca,
    }


def _ia_salvar_imagem_gerada(client_id: str, image_bytes: bytes, sku: str = "") -> tuple[str, str]:
    tenant_path = get_tenant_path(client_id)
    pasta = os.path.join(tenant_path, "ia_imagens")
    os.makedirs(pasta, exist_ok=True)
    sku_norm = re.sub(r"[^A-Za-z0-9_-]+", "_", str(sku or "sku").strip()).strip("_") or "sku"
    nome = f"ia_{sku_norm}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
    caminho = os.path.join(pasta, nome)
    with open(caminho, "wb") as f:
        f.write(image_bytes)
    return caminho, f"/api/ia/imagens/{quote_plus(nome)}"


def _ia_gerar_imagem_sku_resposta(payload: IAChatRequest, client_id: str) -> Optional[str]:
    mensagem = str(payload.message or "").strip()
    if not _ia_chat_pede_geracao_imagem(mensagem):
        return None

    if not _ia_provedor_ativo("openai"):
        return "A geracao de imagem esta desativada pelo administrador nas configuracoes de IA."

    api_key = _obter_openai_api_key()
    if not api_key:
        return "Nao consegui gerar a imagem porque a chave da OpenAI nao esta configurada."

    prompt, meta = _ia_montar_prompt_geracao_imagem_sku(client_id, mensagem)
    if len(prompt) > 4000:
        prompt = prompt[:4000]

    model = (os.getenv("OPENAI_IMAGE_MODEL") or "gpt-image-1").strip()
    try:
        resp = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "prompt": prompt,
                "size": "1024x1024",
                "quality": "medium",
                "n": 1,
            },
            timeout=180,
        )
    except requests.RequestException as exc:
        logger.warning(f"[IA IMG] Falha de conexao ao gerar imagem: {exc}")
        return "Nao consegui gerar a imagem agora por uma falha de conexao com a OpenAI."

    if not resp.ok:
        detail = "falha desconhecida"
        try:
            detail = str((resp.json().get("error") or {}).get("message") or detail)
        except Exception:
            pass
        logger.warning(f"[IA IMG] OpenAI imagens HTTP {resp.status_code}: {detail}")
        return f"Nao consegui gerar a imagem agora. Retorno da OpenAI: {detail}"

    b64_img = _extrair_b64_openai_image_response(resp.json())
    if not b64_img:
        logger.warning("[IA IMG] OpenAI retornou sem imagem em base64.")
        return "A OpenAI respondeu, mas nao retornou uma imagem utilizavel."

    try:
        image_bytes = base64.b64decode(b64_img)
        _, url = _ia_salvar_imagem_gerada(client_id, image_bytes, meta.get("sku") or "")
    except Exception as exc:
        logger.warning(f"[IA IMG] Falha ao salvar imagem gerada: {exc}")
        return "A imagem foi gerada, mas nao consegui salvar o arquivo no sistema."

    sku_txt = f" do SKU {meta.get('sku')}" if meta.get("sku") else ""
    produto_txt = f"\nProduto usado como referencia: {meta.get('produto')}" if meta.get("produto") else ""
    return (
        f"Pronto, gerei uma nova imagem{sku_txt} com base no seu pedido.\n\n"
        f"![Imagem gerada{sku_txt}]({url})"
        f"{produto_txt}"
    )

PEER_EXPORTS = ['_ia_lojas_bling_conectadas', '_ia_lojas_com_integracao', '_ia_obter_cfg_bling', '_ia_tool_get_integrations_status', '_ia_ml_precisa_descricao', '_ia_ml_item_resumo', '_ia_ml_obter_descricao_item', '_ia_ml_listar_anuncios', '_ia_tool_get_mercado_livre_listing', '_ia_bling_valor_tributario', '_ia_buscar_imagem_ml_sku', '_ia_montar_prompt_geracao_imagem_sku', '_ia_salvar_imagem_gerada', '_ia_gerar_imagem_sku_resposta']
__all__ = PEER_EXPORTS + ["configure_ia_tools_marketplaces_runtime"]

configure_ia_tools_marketplaces_runtime()
