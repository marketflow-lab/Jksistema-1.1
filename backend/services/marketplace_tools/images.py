"""Marketplace tool domain extracted from the legacy service."""

from __future__ import annotations

import base64
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import quote_plus

import requests

from backend.schemas import IAChatRequest
from . import runtime as _runtime

logger = logging.getLogger(__name__)

def bling_tax_value(produto: dict, campo: str) -> str:
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

def find_listing_image(client_id: str, sku: str, produto: dict | None = None, cadastro: dict | None = None) -> dict:
    sku_ref = str(sku or "").strip()
    item_ids = _runtime.extract_item_ids(
        (produto or {}).get("mlb_principal"),
        (produto or {}).get("mlb_ids"),
        (cadastro or {}).get("mlb_principal"),
        (cadastro or {}).get("mlb_ids"),
    )

    for nome_loja in _runtime.ml_connected_stores(client_id):
        try:
            cfg = _runtime.ml_config(client_id, nome_loja)

            if item_ids:
                itens, cfg = _runtime.ml_fetch_items_batch(client_id, nome_loja, cfg, item_ids[:10])
                for item in itens:
                    if not isinstance(item, dict):
                        continue
                    imagem = _runtime.ml_question_item_photo(item)
                    if imagem:
                        return {
                            "imagem_url": imagem,
                            "item_id": str(item.get("id") or "").strip(),
                            "titulo": str(item.get("title") or "").strip(),
                            "loja": nome_loja,
                        }

            if sku_ref:
                itens, cfg = _runtime.ml_favorites_items_by_sku(client_id, nome_loja, cfg, sku_ref)
                for item in itens:
                    if not isinstance(item, dict):
                        continue
                    imagem = _runtime.ml_question_item_photo(item)
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

def build_prompt(client_id: str, mensagem: str) -> tuple[str, dict]:
    produto_tool = _runtime.get_product_data(client_id, mensagem, limite=1)
    result = (produto_tool or {}).get("result") or {}
    matches = result.get("matches") or []
    produto = matches[0] if matches else {}
    sku = str(produto.get("sku") or result.get("canonical_sku") or "").strip()
    nome = str(produto.get("nome") or "").strip()
    categoria = str(produto.get("categoria") or "").strip()
    marca = str(produto.get("marca") or "").strip()

    cadastro = _runtime.get_product_registry_info(client_id, mensagem, produto_tool=produto_tool, limite=1) if sku else None
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

def save_generated_image(client_id: str, image_bytes: bytes, sku: str = "") -> tuple[str, str]:
    tenant_path = _runtime.tenant_path(client_id)
    pasta = os.path.join(tenant_path, "ia_imagens")
    os.makedirs(pasta, exist_ok=True)
    sku_norm = re.sub(r"[^A-Za-z0-9_-]+", "_", str(sku or "sku").strip()).strip("_") or "sku"
    nome = f"ia_{sku_norm}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
    caminho = os.path.join(pasta, nome)
    with open(caminho, "wb") as f:
        f.write(image_bytes)
    return caminho, f"/api/ia/imagens/{quote_plus(nome)}"

def generate_response(payload: IAChatRequest, client_id: str) -> Optional[str]:
    mensagem = str(payload.message or "").strip()
    if not _runtime.chat_requests_image_generation(mensagem):
        return None

    if not _runtime.active_provider("openai"):
        return "A geracao de imagem esta desativada pelo administrador nas configuracoes de IA."

    api_key = _runtime.openai_api_key()
    if not api_key:
        return "Nao consegui gerar a imagem porque a chave da OpenAI nao esta configurada."

    prompt, meta = build_prompt(client_id, mensagem)
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

    b64_img = _runtime.extract_openai_image_b64(resp.json())
    if not b64_img:
        logger.warning("[IA IMG] OpenAI retornou sem imagem em base64.")
        return "A OpenAI respondeu, mas nao retornou uma imagem utilizavel."

    try:
        image_bytes = base64.b64decode(b64_img)
        _, url = save_generated_image(client_id, image_bytes, meta.get("sku") or "")
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
