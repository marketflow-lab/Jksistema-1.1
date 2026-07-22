"""Internal slice for favoritos_core."""

from __future__ import annotations

from __future__ import annotations
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
from backend.services.transport_security import requests_tls_verify


def configure_favoritos_ranking_ia_runtime(runtime_module=None, peers=None):
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


configure_favoritos_ranking_ia_runtime()


def _favoritos_normalizar_texto_pesquisa(texto: str) -> str:
    limpo = re.sub(r"\s+", " ", str(texto or "").strip())
    limpo = limpo.strip("\"'â€œâ€â€˜â€™").strip()
    return limpo[:140]


def _favoritos_codigo_compacto(valor: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(valor or "").lower())


def _favoritos_extrair_codigos_pesquisa(texto: str) -> list[str]:
    texto = html_lib.unescape(str(texto or ""))
    vistos: set[str] = set()
    codigos: list[str] = []

    def adicionar(valor: str):
        codigo = str(valor or "").strip().strip(".,;:()[]{}")
        if not codigo:
            return
        compacto = _favoritos_codigo_compacto(codigo)
        if len(compacto) < 5 or compacto in vistos:
            return
        if compacto.isdigit() and len(compacto) == 4 and 1900 <= int(compacto) <= 2099:
            return
        vistos.add(compacto)
        codigos.append(codigo)

    padroes_com_rotulo = [
        r"\b(?:c[oó]d(?:igo)?|cod|cód|oem|part\s*number|part|ref(?:er[eê]ncia)?|n[ºo]\s*da\s*pe[cç]a)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9./-]{3,}[A-Z0-9])",
        r"\b(?:modelo|aplica[cç][aã]o)\s*[:#\-]?\s*([A-Z0-9]{2,}(?:[-./][A-Z0-9]{2,})+)",
    ]
    for padrao in padroes_com_rotulo:
        for match in re.finditer(padrao, texto, flags=re.IGNORECASE):
            adicionar(match.group(1))

    for match in re.finditer(r"\b[A-Z0-9]{2,}(?:[-./][A-Z0-9]{2,}){1,5}\b", texto, flags=re.IGNORECASE):
        candidato = match.group(0)
        if any(ch.isdigit() for ch in candidato):
            adicionar(candidato)

    for match in re.finditer(r"\b(?:[A-Z]{1,5}\d{3,}[A-Z0-9]*|\d{5,}[A-Z0-9-]*)\b", texto, flags=re.IGNORECASE):
        adicionar(match.group(0))

    return codigos


def _favoritos_codigo_pesquisa_2(produto: str, descricao: str) -> str:
    texto = html_lib.unescape(f"{produto or ''}\n{descricao or ''}")
    vistos: set[str] = set()
    codigos: list[str] = []

    def adicionar(valor: str):
        codigo = str(valor or "").strip().strip(".,;:()[]{}")
        if not codigo:
            return
        compacto = _favoritos_codigo_compacto(codigo)
        if len(compacto) < 5 or compacto in vistos:
            return
        if compacto.isdigit() and len(compacto) == 4 and 1900 <= int(compacto) <= 2099:
            return
        vistos.add(compacto)
        codigos.append(codigo)

    rotulos_codigo = (
        r"c[oó]d(?:igo)?",
        r"cod",
        r"cód",
        r"oem",
        r"part\s*number",
        r"part",
        r"ref(?:er[eê]ncia)?",
        r"n[ºo]\s*da\s*pe[çc]a",
        r"n[uú]mero\s*da\s*pe[çc]a",
    )
    padrao_rotulado = rf"\b(?:{'|'.join(rotulos_codigo)})\s*[:#\-]?\s*([A-Z0-9][A-Z0-9./-]{{3,}}[A-Z0-9])"
    for match in re.finditer(padrao_rotulado, texto, flags=re.IGNORECASE):
        adicionar(match.group(1))

    # Fallback para códigos técnicos sem rótulo. Evita capturar modelos curtos como CB500.
    for match in re.finditer(r"\b[A-Z0-9]{2,}(?:[-./][A-Z0-9]{2,}){1,5}\b", texto, flags=re.IGNORECASE):
        candidato = match.group(0)
        if any(ch.isdigit() for ch in candidato):
            adicionar(candidato)

    for match in re.finditer(r"\b(?:[A-Z]{1,5}\d{5,}[A-Z0-9]*|\d{6,}[A-Z0-9-]*)\b", texto, flags=re.IGNORECASE):
        adicionar(match.group(0))

    return codigos[0] if codigos else ""


def _favoritos_texto_contem_codigo(texto: str, codigos: list[str]) -> bool:
    texto_compacto = _favoritos_codigo_compacto(texto)
    return any(_favoritos_codigo_compacto(codigo) in texto_compacto for codigo in codigos or [])


def _favoritos_normalizar_sem_acentos(texto: str) -> str:
    texto_norm = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(ch for ch in texto_norm if not unicodedata.combining(ch)).lower()


def _favoritos_veiculo_marcas() -> dict[str, str]:
    return {
        "honda": "Honda",
        "toyota": "Toyota",
        "chevrolet": "Chevrolet",
        "gm": "Chevrolet",
        "ford": "Ford",
        "fiat": "Fiat",
        "volkswagen": "Volkswagen",
        "vw": "Volkswagen",
        "renault": "Renault",
        "peugeot": "Peugeot",
        "citroen": "Citroen",
        "citroën": "Citroen",
        "hyundai": "Hyundai",
        "kia": "Kia",
        "nissan": "Nissan",
        "mitsubishi": "Mitsubishi",
        "jeep": "Jeep",
        "audi": "Audi",
        "bmw": "BMW",
        "mercedes": "Mercedes-Benz",
        "mercedes-benz": "Mercedes-Benz",
        "volvo": "Volvo",
        "suzuki": "Suzuki",
        "yamaha": "Yamaha",
        "kawasaki": "Kawasaki",
    }


def _favoritos_extrair_marca_global(texto: str) -> str:
    texto_norm = _favoritos_normalizar_sem_acentos(texto)
    for alias, marca in _favoritos_veiculo_marcas().items():
        alias_norm = _favoritos_normalizar_sem_acentos(alias)
        if re.search(rf"\b{re.escape(alias_norm)}\b", texto_norm):
            return marca
    return ""


def _favoritos_limpar_modelo_veiculo(texto: str, marca: str = "") -> str:
    texto = re.sub(r"[^A-Za-zÀ-ÿ0-9. -]+", " ", str(texto or ""))
    texto = re.sub(r"\b(?:19\d{2}|20\d{2})\b", " ", texto)
    texto = re.sub(r"\b\d+[.,]\d+\b", " ", texto)
    texto = re.sub(r"\b\d+\s*v\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\s+", " ", texto).strip(" -")
    tokens = re.findall(r"[A-Za-zÀ-ÿ0-9.]+", texto)
    stop = {
        "a", "ate", "até", "ano", "anos", "para", "compativel", "compatível", "com", "do", "da", "de", "dos", "das",
        "peca", "peça", "produto", "sensor", "temperatura", "radiador", "cebolao", "cebolão",
        "cebola", "interruptor", "termico", "térmico", "junta", "valvula", "válvula", "solenoide",
        "motor", "moto", "carro", "veiculo", "veículo", "novo", "nova", "original"
    }
    marcas_norm = {_favoritos_normalizar_sem_acentos(alias) for alias in _favoritos_veiculo_marcas()}
    marca_norm = _favoritos_normalizar_sem_acentos(marca)
    limpos = []
    for token in tokens:
        token_norm = _favoritos_normalizar_sem_acentos(token)
        if token_norm in stop or token_norm in marcas_norm or token_norm == marca_norm:
            continue
        if token_norm.isdigit():
            continue
        limpos.append(token)
    return " ".join(limpos[-3:]).strip()


def _favoritos_extrair_aplicacoes_veiculares(produto: str, descricao: str) -> list[dict]:
    texto_original = html_lib.unescape(f"{produto or ''}\n{descricao or ''}")
    texto_original = re.sub(r"\\n|\\\\n", "\n", texto_original)
    texto_original = re.sub(r"\s+", " ", texto_original).strip()
    if not texto_original:
        return []

    marca_global = _favoritos_extrair_marca_global(texto_original)
    marcas = _favoritos_veiculo_marcas()
    vistos: set[tuple[str, str, str]] = set()
    aplicacoes: list[dict] = []

    padrao_ano = re.compile(r"\b(19\d{2}|20\d{2})(?:\s*(?:a|até|ate|-|/)\s*(19\d{2}|20\d{2}))?\b", flags=re.IGNORECASE)
    for match in padrao_ano.finditer(texto_original):
        ano_inicio = match.group(1)
        ano_fim = match.group(2) or ""
        anos = f"{ano_inicio} a {ano_fim}" if ano_fim and ano_fim != ano_inicio else ano_inicio
        contexto = texto_original[max(0, match.start() - 95):match.start()]
        contexto_norm = _favoritos_normalizar_sem_acentos(contexto)

        marca = ""
        marca_pos = -1
        for alias, nome_marca in marcas.items():
            alias_norm = _favoritos_normalizar_sem_acentos(alias)
            achados = list(re.finditer(rf"\b{re.escape(alias_norm)}\b", contexto_norm))
            if achados and achados[-1].start() > marca_pos:
                marca = nome_marca
                marca_pos = achados[-1].end()
        if not marca:
            marca = marca_global

        trecho_modelo = contexto
        if marca_pos >= 0:
            trecho_modelo = contexto[marca_pos:]
            partes_pos_marca = [p.strip() for p in re.split(r"[-;|,/]", trecho_modelo) if p.strip()]
            if partes_pos_marca:
                trecho_modelo = partes_pos_marca[-1]
        else:
            partes = re.split(r"[-;|,/]", contexto)
            trecho_modelo = partes[-1] if partes else contexto
        modelo = _favoritos_limpar_modelo_veiculo(trecho_modelo, marca)
        if not modelo:
            continue
        chave = (marca.lower(), modelo.lower(), anos)
        if chave in vistos:
            continue
        vistos.add(chave)
        aplicacoes.append({"marca": marca, "modelo": modelo, "anos": anos})
        if len(aplicacoes) >= 6:
            break
    return aplicacoes


def _favoritos_aplicacao_texto(aplicacao: dict) -> str:
    partes = [
        str(aplicacao.get("marca") or "").strip(),
        str(aplicacao.get("modelo") or "").strip(),
        str(aplicacao.get("anos") or "").strip(),
    ]
    return " ".join([p for p in partes if p]).strip()


def _favoritos_pesquisa_tem_aplicacao(texto: str, aplicacao: dict) -> bool:
    texto_norm = _favoritos_normalizar_sem_acentos(texto)
    modelo = str(aplicacao.get("modelo") or "").strip()
    anos = re.findall(r"\b(19\d{2}|20\d{2})\b", str(aplicacao.get("anos") or ""))
    modelo_ok = bool(modelo and _favoritos_normalizar_sem_acentos(modelo).split()[0] in texto_norm)
    ano_ok = bool(anos and any(ano in texto_norm for ano in anos))
    marca = str(aplicacao.get("marca") or "").strip()
    marca_ok = bool(not marca or _favoritos_normalizar_sem_acentos(marca).split()[0] in texto_norm)
    return modelo_ok and ano_ok and marca_ok


def _favoritos_garantir_aplicacao_pesquisa(termo: str, aplicacao: dict) -> str:
    termo_limpo = _favoritos_normalizar_texto_pesquisa(termo)
    aplicacao_txt = _favoritos_aplicacao_texto(aplicacao)
    if not aplicacao_txt or _favoritos_pesquisa_tem_aplicacao(termo_limpo, aplicacao):
        return termo_limpo
    return _favoritos_normalizar_texto_pesquisa(f"{termo_limpo} {aplicacao_txt}".strip())


def _favoritos_gerar_pesquisa_heuristica(produto: str, descricao: str) -> tuple[str, str, str]:
    base = str(produto or "").strip() or str(descricao or "").strip()
    if not base:
        return "", "", ""
    tokenizacao = re.findall(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9.+-]*", str(base).lower())
    stop = {
        "de", "da", "do", "dos", "das", "a", "o", "as", "os", "e", "ou", "para", "com", "sem",
        "novo", "nova", "novidade", "para", "de", "para", "peça", "peca", "kit", "sistema"
    }
    palavras = []
    for token in tokenizacao:
        if len(token) < 2:
            continue
        if token.lower() in stop:
            continue
        if token not in palavras:
            palavras.append(token)
    if not palavras:
        return ("", "", "")
    pesquisa1 = " ".join(palavras[:6]).strip()
    pesquisa2 = " ".join(palavras[1:7] or palavras[:6]).strip()
    pesquisa3 = " ".join(palavras[2:8] or palavras[:6]).strip()
    return (
        _favoritos_normalizar_texto_pesquisa(pesquisa1),
        _favoritos_normalizar_texto_pesquisa(pesquisa2),
        _favoritos_normalizar_texto_pesquisa(pesquisa3),
    )


def _favoritos_ia_texto_resposta(
    client_id: str,
    mensagem: str,
    model: str | None = None,
) -> str:
    req = IAChatRequest(message=mensagem, model=model, page="Favoritos")
    model_req = _normalizar_ia_modelo_padrao(model or _ia_modelo_favoritos_configurado())
    req.model = model_req
    try:
        if _modelo_eh_vertex_ai(model_req):
            return _chamar_vertex_ai_chat(req, client_id)
        if _modelo_eh_gemini_api(model_req):
            return _chamar_gemini_chat(req, client_id)
        if model_req.startswith("deepseek-"):
            return _chamar_deepseek_chat(req, client_id)
        return _chamar_openai_responses(req, client_id)
    except Exception as exc:
        logger.warning("[Favoritos IA] Falha ao gerar texto de pesquisa: %s", exc)
        return ""


def _favoritos_ia_gemini_pesquisa_texto(mensagem: str, model: str | None = None) -> str:
    req = IAChatRequest(message=mensagem, model=_normalizar_ia_modelo_padrao(model), page="Favoritos")
    return _chamar_gemini_chat(req, "default")


def _favoritos_ia_vertex_pesquisa_texto(mensagem: str, model: str | None = None) -> str:
    model_name = _vertex_modelo_nome_curto(model) or _vertex_ai_modelo_padrao()
    headers, project_id = _vertex_ai_headers_e_project()
    location = _vertex_ai_location()
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    url = f"https://{host}/v1/projects/{project_id}/locations/{location}/publishers/google/models/{model_name}:generateContent"

    resp = requests.post(
        url,
        headers=headers,
        json={
            "systemInstruction": {
                "parts": [{
                    "text": (
                        "Você gera termos de busca para marketplace. "
                        "Responda somente com o termo solicitado, sem saudação, sem explicação, sem Markdown e sem aspas extras."
                    )
                }]
            },
            "contents": [{"role": "user", "parts": [{"text": mensagem}]}],
            "generationConfig": {"temperature": 0.2},
        },
        verify=requests_tls_verify(),
        timeout=60,
    )
    if not resp.ok:
        detail = resp.text[:300]
        try:
            erro = resp.json().get("error") or {}
            detail = str(erro.get("message") or detail)
        except Exception:
            pass
        raise RuntimeError(f"Vertex Gemini HTTP {resp.status_code}: {detail}")

    try:
        partes = resp.json()["candidates"][0]["content"]["parts"]
        return "\n".join(str(parte.get("text") or "").strip() for parte in partes if str(parte.get("text") or "").strip()).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Vertex Gemini retornou sem texto ({exc})")


def _favoritos_ia_pesquisa_texto(client_id: str, mensagem: str, model: str | None = None) -> str:
    model_name = _normalizar_ia_modelo_padrao(model or _ia_modelo_favoritos_configurado())
    if _modelo_eh_vertex_ai(model_name):
        return _favoritos_ia_vertex_pesquisa_texto(mensagem, model=model_name)
    if _modelo_eh_gemini_api(model_name):
        return _favoritos_ia_texto_resposta(client_id, mensagem, model=model_name)
    return _favoritos_ia_texto_resposta(client_id, mensagem, model=model_name)


def _favoritos_ia_extrair_json(texto: str) -> list[dict]:
    if not isinstance(texto, str):
        return []
    limpo = str(texto).strip()
    if not limpo:
        return []

    candidatos = []
    bloco = re.findall(r"```(?:json)?\s*(.*?)\s*```", limpo, flags=re.DOTALL | re.IGNORECASE)
    if bloco:
        candidatos.extend(bloco)
    candidatos.append(limpo)

    for candidato in candidatos:
        trecho = str(candidato).strip()
        if not trecho:
            continue
        for match in re.finditer(r"(\{.*?\}|\[.*?\])", trecho, flags=re.DOTALL):
            bloco_json = match.group(1).strip()
            try:
                data = json.loads(bloco_json)
            except Exception:
                continue

            if isinstance(data, dict) and "resultados" in data and isinstance(data["resultados"], list):
                return data["resultados"] if isinstance(data["resultados"], list) else []
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                if any(chave in data for chave in (
                    "campo_pesquisa_1", "campo_pesquisa_2", "campo_pesquisa_3",
                    "pesquisa_1", "pesquisa_2", "pesquisa_3"
                )):
                    return [data]
                itens = []
                for _, valor in data.items():
                    if isinstance(valor, dict):
                        itens.append(valor)
                if itens:
                    return itens
    return []


def _favoritos_parse_resultado_ia(
    sku_origem: str,
    produto: str,
    descricao: str,
    resposta_obj: list[dict],
) -> tuple[str, str, str]:
    alvo = _normalizar_sku_match_favoritos(sku_origem).lower()
    melhor = None
    for item in resposta_obj or []:
        if not isinstance(item, dict):
            continue
        sku_item = _normalizar_sku_match_favoritos(str(item.get("sku") or item.get("SKU") or "")).lower()
        if sku_item and sku_item == alvo:
            melhor = item
            break
        if not sku_item and melhor is None and str(item.get("produto") or "").strip().lower() == str(produto or "").strip().lower():
            melhor = item
    if melhor is None and len(resposta_obj or []) == 1 and isinstance((resposta_obj or [None])[0], dict):
        melhor = (resposta_obj or [None])[0]
    if melhor:
        p1 = _favoritos_normalizar_texto_pesquisa(str(melhor.get("campo_pesquisa_1") or melhor.get("pesquisa_1") or melhor.get("pesquisa1") or melhor.get("Pesquisa 1") or "").strip())
        p2 = _favoritos_normalizar_texto_pesquisa(str(melhor.get("campo_pesquisa_2") or melhor.get("pesquisa_2") or melhor.get("pesquisa2") or melhor.get("Pesquisa 2") or "").strip())
        p3 = _favoritos_normalizar_texto_pesquisa(str(melhor.get("campo_pesquisa_3") or melhor.get("pesquisa_3") or melhor.get("pesquisa3") or melhor.get("Pesquisa 3") or "").strip())
        if p1 or p2 or p3:
            return p1, p2, p3

    return "", "", ""


def _favoritos_limpar_resposta_campo_ia(texto: str, campo: str) -> str:
    bruto = str(texto or "").strip()
    if not bruto:
        return ""

    for item in _favoritos_ia_extrair_json(bruto):
        if not isinstance(item, dict):
            continue
        valor = (
            item.get(campo)
            or item.get(campo.replace("campo_", ""))
            or item.get(campo.replace("_", " "))
        )
        if valor:
            return _favoritos_normalizar_texto_pesquisa(str(valor))

    limpo = re.sub(r"^```(?:json)?\s*|\s*```$", "", bruto, flags=re.IGNORECASE | re.DOTALL).strip()
    padrao_campo = re.escape(campo).replace("\\_", r"[_\s-]?")
    match = re.search(rf"{padrao_campo}\s*[:=]\s*[\"']?([^\"'\n\r}}]+)", limpo, flags=re.IGNORECASE)
    if match:
        limpo = match.group(1).strip()
    else:
        linhas = [linha.strip(" -*\t") for linha in limpo.splitlines() if linha.strip()]
        if linhas:
            limpo = linhas[0]
    limpo = re.sub(r"^(?:pesquisa\s*[123]|campo[_\s-]?pesquisa[_\s-]?[123])\s*[:=-]\s*", "", limpo, flags=re.IGNORECASE).strip()
    if not limpo or limpo.startswith("{") or "instabilidade moment" in _favoritos_normalizar_sem_acentos(limpo):
        return ""
    return _favoritos_normalizar_texto_pesquisa(limpo)


def _favoritos_prompt_campo_pesquisa(campo_numero: int, produto: str, descricao: str) -> str:
    if campo_numero == 1:
        regra = (
            "Gere APENAS o valor de campo_pesquisa_1 (Direto/Técnico). "
            "Identifique o nome da peça e a aplicação veicular mais provável no título/descrição. "
            "Use nome da peça + marca + modelo + um ano específico do veículo. "
            "Se a descrição trouxer intervalo de anos para o modelo escolhido, selecione apenas um ano dentro desse intervalo, nunca retorne o intervalo completo. "
            "Exemplo de formato: cebolão radiador Honda Civic EX 2003."
        )
    elif campo_numero == 2:
        regra = (
            "Gere APENAS o valor de campo_pesquisa_2 (Código/OEM ou Variação Direta). "
            "Se houver número de peça, código OEM, part number, referência ou código de conversão no título ou descrição, "
            "retorne somente UM código, sem nome do produto, sem marca, sem aplicação e sem palavras extras. "
            "Caso não exista código/numeração, crie uma segunda variação direta e objetiva seguindo a mesma lógica da Pesquisa 1."
        )
    else:
        regra = (
            "Gere APENAS o valor de campo_pesquisa_3 (Informal/Coloquial). "
            "Use nome da peça + veículo/aplicação de forma menos formal, com termos populares, abreviações ou jeito comum de busca do cliente. "
            "Quando houver mais de uma aplicação/veículo no título ou descrição, use preferencialmente um modelo diferente do usado na Pesquisa 1. "
            "Se o modelo escolhido tiver intervalo de anos, escolha apenas um ano específico dentro do intervalo, nunca retorne o intervalo completo."
        )

    return (
        "Você é um especialista em comportamento do consumidor e SEO de e-commerce automotivo/produtos.\n"
        "Analise o Título e a Descrição do produto fornecidos.\n\n"
        f"{regra}\n\n"
        "Retorne somente o termo de busca limpo, sem JSON, sem explicações, sem aspas extras e sem blocos de código.\n\n"
        f"Título do produto:\n{str(produto or '').strip()}\n\n"
        f"Descrição do produto:\n{str(descricao or '').strip()}"
    )


def _favoritos_gerar_campo_pesquisa_ia(
    client_id: str,
    model: str | None,
    produto: str,
    descricao: str,
    campo_numero: int,
) -> str:
    campo = f"campo_pesquisa_{campo_numero}"
    prompt = _favoritos_prompt_campo_pesquisa(campo_numero, produto, descricao)
    ultimo_erro = ""
    for tentativa in range(2):
        prompt_tentativa = prompt
        if tentativa:
            prompt_tentativa += (
                f"\n\nA resposta anterior não serviu para {campo}. "
                "Responda agora apenas com um único termo de busca, sem frase explicativa."
            )
        try:
            resposta = _favoritos_ia_pesquisa_texto(client_id, prompt_tentativa, model=model)
            valor = _favoritos_limpar_resposta_campo_ia(resposta, campo)
            if valor:
                return valor
        except Exception as exc:
            ultimo_erro = str(exc)
            logger.warning("[Favoritos IA] Falha ao gerar %s: %s", campo, exc)
    if ultimo_erro:
        raise ValueError(f"IA não retornou a Pesquisa {campo_numero}: {ultimo_erro}")
    raise ValueError(f"IA não retornou a Pesquisa {campo_numero}.")


def _favoritos_ranking_anuncio_id(anuncio: dict) -> str:
    for chave in ("id", "mlb", "item_id", "itemId", "url", "permalink", "link"):
        valor = str((anuncio or {}).get(chave) or "").strip()
        if not valor:
            continue
        item_id = _extrair_item_id(valor)
        if item_id:
            return item_id
        texto = valor.upper().replace("-", "")
        match = re.search(r"MLB(\d+)", texto)
        if match:
            return f"MLB{match.group(1)}"
    return ""


def _favoritos_ranking_descricao_anuncio(anuncio: dict) -> str:
    anuncio = anuncio or {}
    for chave in (
        "descricao",
        "descricao_ml",
        "description",
        "description_plain",
        "plain_text",
        "text",
        "subtitle",
    ):
        valor = anuncio.get(chave)
        if isinstance(valor, str) and valor.strip():
            return re.sub(r"\s+", " ", valor).strip()

    for chave in ("description_info", "descriptionInfo", "descricao_info"):
        valor = anuncio.get(chave)
        if isinstance(valor, (dict, str)):
            texto = _ml_favoritos_extrair_texto_descricao(valor)
            if texto:
                return texto
    return ""


def _favoritos_ranking_buscar_descricao_publica(item_id: str) -> str:
    item_id = (_extrair_item_id(str(item_id or "")) or str(item_id or "").strip().upper().replace("-", ""))
    if not item_id:
        return ""
    try:
        resp = requests.get(
            f"https://api.mercadolibre.com/items/{item_id}/description",
            headers={"Accept": "application/json"},
            timeout=8,
        )
        if resp.status_code != 200:
            return ""
        return _ml_favoritos_extrair_texto_descricao(resp.json() or "")
    except Exception as exc:
        logger.debug("[Favoritos IA] Nao foi possivel buscar descricao publica do anuncio %s: %s", item_id, exc)
        return ""


def _favoritos_ranking_completar_descricoes(anuncios_norm: list[dict]) -> None:
    pendentes = [item for item in anuncios_norm if item.get("id") and not str(item.get("descricao") or "").strip()]
    if not pendentes:
        return
    max_workers = min(8, len(pendentes))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_favoritos_ranking_buscar_descricao_publica, item.get("id")): item
            for item in pendentes
        }
        for future in as_completed(future_map):
            item = future_map[future]
            try:
                descricao = future.result()
            except Exception:
                descricao = ""
            if descricao:
                item["descricao"] = descricao[:1800]


FAVORITOS_RANKING_DECISOES_LOCK = threading.Lock()


FAVORITOS_RANKING_DECISAO_VERSAO = 4


def _favoritos_ranking_decisoes_cache_path(client_id: str, sku: str) -> str:
    sku_norm = _normalizar_sku_match_favoritos(str(sku or "")).strip() or "sku"
    nome_seguro = re.sub(r"[^A-Za-z0-9_-]+", "_", sku_norm).strip("_")[:80] or "sku"
    digest = hashlib.sha1(sku_norm.encode("utf-8", errors="ignore")).hexdigest()[:10]
    pasta = os.path.join(get_tenant_path(client_id), "favoritos_decisoes_ia")
    os.makedirs(pasta, exist_ok=True)
    return os.path.join(pasta, f"{nome_seguro}_{digest}.json")


def _favoritos_ranking_ler_decisoes_cache(client_id: str, sku: str) -> dict:
    caminho = _favoritos_ranking_decisoes_cache_path(client_id, sku)
    if not os.path.exists(caminho):
        return {"sku": str(sku or "").strip(), "decisoes": {}}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f) or {}
        if not isinstance(dados, dict):
            return {"sku": str(sku or "").strip(), "decisoes": {}}
        if not isinstance(dados.get("decisoes"), dict):
            dados["decisoes"] = {}
        return dados
    except Exception:
        return {"sku": str(sku or "").strip(), "decisoes": {}}


def _favoritos_ranking_texto_norm(*partes: str) -> str:
    texto = " ".join(str(parte or "") for parte in partes if str(parte or "").strip())
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    texto = texto.upper()
    return re.sub(r"\s+", " ", texto).strip()


def _favoritos_ranking_codigo_norm(valor: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(valor or "").upper())


def _favoritos_ranking_extrair_codigos(*textos: str) -> list[str]:
    bruto = _favoritos_ranking_texto_norm(*textos)
    codigos = []

    def _codigo_invalido(norm: str) -> bool:
        if not norm or len(norm) < 5:
            return True
        if re.fullmatch(r"(19|20)\d{2}", norm):
            return True
        if re.fullmatch(r"(?:19|20)\d{2}(?:19|20)\d{2}", norm):
            return True
        return False

    for codigo in _favoritos_busca_externa_extrair_codigos(bruto):
        norm = _favoritos_ranking_codigo_norm(codigo)
        if not _codigo_invalido(norm) and norm not in codigos:
            codigos.append(norm)

    extras = re.findall(r"\b(?=[A-Z0-9-]*\d)[A-Z0-9]{2,}(?:[-./][A-Z0-9]{2,})+\b|\b(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{5,}\b|\b\d{5,12}\b", bruto)
    for codigo in extras:
        norm = _favoritos_ranking_codigo_norm(codigo)
        if not norm or norm in codigos:
            continue
        if _codigo_invalido(norm):
            continue
        codigos.append(norm)
        if len(codigos) >= 10:
            break
    return codigos


def _favoritos_ranking_extrair_anos(*textos: str) -> list[int]:
    texto = _favoritos_ranking_texto_norm(*textos)
    anos: set[int] = set()
    limite_superior = datetime.now().year + 3

    for m in re.finditer(r"\b((?:19|20)\d{2})\s*(?:A|ATE|AT[EÉ]|-|–|â€”)\s*((?:19|20)\d{2})\b", texto):
        ini = int(m.group(1))
        fim = int(m.group(2))
        if ini > fim:
            ini, fim = fim, ini
        if 1950 <= ini <= limite_superior and 1950 <= fim <= limite_superior and (fim - ini) <= 40:
            anos.update(range(ini, fim + 1))

    for ano_txt in re.findall(r"\b(?:19|20)\d{2}\b", texto):
        ano = int(ano_txt)
        if 1950 <= ano <= limite_superior:
            anos.add(ano)
    return sorted(anos)


FAVORITOS_RANKING_MARCAS = {
    "HONDA", "FORD", "CHEVROLET", "GM", "VOLKSWAGEN", "VW", "FIAT", "TOYOTA", "NISSAN",
    "RENAULT", "PEUGEOT", "CITROEN", "CITROEN", "JEEP", "HYUNDAI", "KIA", "MITSUBISHI",
    "BMW", "AUDI", "MERCEDES", "MERCEDES BENZ", "YAMAHA", "SUZUKI", "KAWASAKI", "DAFRA",
    "IVECO", "SCANIA", "VOLVO", "AGRALE", "TROLLER", "CHERY", "JAC",
}


FAVORITOS_RANKING_MODELOS = {
    "ACCORD", "AGILE", "AMAROK", "ARGO", "ASTRA", "BIZ", "BLAZER", "BROS", "CARGO",
    "CB300", "CB500", "CB600", "CBR", "CELTA", "CERATO", "CIVIC", "CLASSIC", "COBALT",
    "COROLLA", "CORSA", "COURIER", "CRUZE", "DREAM", "DUCATO", "ECOSPORT", "FAN",
    "FIESTA", "FIT", "FOCUS", "FOX", "FUSION", "GOL", "GOLF", "HILUX", "HORNET",
    "JETTA", "KA", "KOMBI", "LOGAN", "MARCH", "MONDEO", "MONTANA", "ONIX", "PALIO",
    "PARATI", "POLO", "PRISMA", "RANGER", "SAVEIRO", "SHADOW", "SIENA", "S10",
    "SPIN", "STRADA", "T4", "TITAN", "TORO", "UNO", "VECTRA", "VOYAGE", "XRE",
    "ZAFIRA",
}


FAVORITOS_RANKING_PECAS = {
    "CEBOLAO_RADIADOR": ("CEBOLAO RADIADOR", "CEBOLINHA RADIADOR", "SENSOR TEMPERATURA RADIADOR", "INTERRUPTOR VENTOINHA"),
    "SENSOR_PRESSAO_COMBUSTIVEL": ("SENSOR PRESSAO COMBUSTIVEL", "SENSOR PRESSAO FLAUTA", "REGULADOR PRESSAO", "VALVULA REGULADORA BOMBA"),
    "PORTINHOLA_TANQUE": ("PORTINHOLA TANQUE", "TAMPA PORTINHOLA TANQUE", "PORTA TANQUE", "TAMPA TANQUE COMBUSTIVEL"),
    "BASE_ANTENA": ("BASE ANTENA", "BASE PARA ANTENA", "PE DA ANTENA"),
    "HASTE_ANTENA": ("HASTE ANTENA", "ANTENA HASTE"),
    "AMORTECEDOR": ("AMORTECEDOR", "MOLA GAS", "MOLA A GAS"),
    "JUNTA": ("JUNTA",),
    "VALVULA": ("VALVULA",),
    "BOMBA": ("BOMBA",),
    "PASTILHA_FREIO": ("PASTILHA FREIO", "PASTILHA DE FREIO"),
    "FILTRO": ("FILTRO",),
    "SENSOR_NIVEL": ("SENSOR NIVEL", "BOIA COMBUSTIVEL", "MEDIDOR COMBUSTIVEL"),
}


def _favoritos_ranking_extrair_campos_tecnicos(*textos: str) -> dict:
    texto = _favoritos_ranking_texto_norm(*textos)
    marcas = sorted({marca for marca in FAVORITOS_RANKING_MARCAS if re.search(rf"\b{re.escape(marca)}\b", texto)})
    if "GM" in marcas and "CHEVROLET" not in marcas:
        marcas.append("CHEVROLET")
    if "VW" in marcas and "VOLKSWAGEN" not in marcas:
        marcas.append("VOLKSWAGEN")

    modelos = sorted({modelo for modelo in FAVORITOS_RANKING_MODELOS if re.search(rf"\b{re.escape(modelo)}\b", texto)})

    pecas = []
    for chave, padroes in FAVORITOS_RANKING_PECAS.items():
        if any(padrao in texto for padrao in padroes):
            pecas.append(chave)

    posicoes = {}
    if re.search(r"\b(DIREITO|DIREITA|LD|LADO DIREITO)\b", texto):
        posicoes["lado"] = "DIREITO"
    if re.search(r"\b(ESQUERDO|ESQUERDA|LE|LADO ESQUERDO)\b", texto):
        posicoes["lado"] = "ESQUERDO" if "lado" not in posicoes else "AMBOS"
    if re.search(r"\b(DIANTEIRO|DIANTEIRA|FRENTE|FRONTAL)\b", texto):
        posicoes["eixo"] = "DIANTEIRO"
    if re.search(r"\b(TRASEIRO|TRASEIRA)\b", texto):
        posicoes["eixo"] = "TRASEIRO" if "eixo" not in posicoes else "AMBOS"
    if re.search(r"\b(SUPERIOR|CIMA)\b", texto):
        posicoes["altura"] = "SUPERIOR"
    if re.search(r"\b(INFERIOR|BAIXO)\b", texto):
        posicoes["altura"] = "INFERIOR" if "altura" not in posicoes else "AMBOS"

    return {
        "codigos": _favoritos_ranking_extrair_codigos(*textos),
        "anos": _favoritos_ranking_extrair_anos(*textos),
        "marcas": sorted(set(marcas)),
        "modelos": modelos,
        "pecas": sorted(set(pecas)),
        "posicoes": posicoes,
    }


def _favoritos_ranking_assinatura_decisao(sku: str, base: dict, anuncio: dict) -> str:
    payload = {
        "versao": FAVORITOS_RANKING_DECISAO_VERSAO,
        "sku": str(sku or "").strip(),
        "base_titulo": str(base.get("titulo_sku") or base.get("produto_cadastro") or "")[:700],
        "base_descricao": str(base.get("descricao_sku") or base.get("descricao_cadastro") or "")[:1600],
        "id": str(anuncio.get("id") or ""),
        "titulo": str(anuncio.get("titulo") or "")[:400],
        "descricao": str(anuncio.get("descricao") or "")[:1600],
    }
    bruto = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(bruto.encode("utf-8", errors="ignore")).hexdigest()


def _favoritos_ranking_decisao_cache_valida(item: dict, assinatura: str) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("versao") != FAVORITOS_RANKING_DECISAO_VERSAO:
        return False
    if str(item.get("assinatura") or "") != assinatura:
        return False
    decisao = str(item.get("decisao") or "").lower()
    return decisao in {"manter", "remover"}


def _favoritos_ranking_preavaliar(base_campos: dict, anuncio_campos: dict) -> dict:
    base_codigos = set(base_campos.get("codigos") or [])
    anuncio_codigos = set(anuncio_campos.get("codigos") or [])
    codigos_iguais = sorted(base_codigos & anuncio_codigos)
    if codigos_iguais:
        return {
            "decisao": "manter",
            "motivo": "codigo de peca coincidente",
            "confianca": 100,
            "codigos": codigos_iguais[:5],
        }

    motivos = []

    base_pecas = set(base_campos.get("pecas") or [])
    anuncio_pecas = set(anuncio_campos.get("pecas") or [])
    if base_pecas and anuncio_pecas and not (base_pecas & anuncio_pecas):
        motivos.append("tipo de peca diferente")

    base_marcas = set(base_campos.get("marcas") or [])
    anuncio_marcas = set(anuncio_campos.get("marcas") or [])
    if base_marcas and anuncio_marcas and not (base_marcas & anuncio_marcas):
        motivos.append("marca de veiculo diferente")

    base_modelos = set(base_campos.get("modelos") or [])
    anuncio_modelos = set(anuncio_campos.get("modelos") or [])
    if base_modelos and anuncio_modelos and not (base_modelos & anuncio_modelos):
        motivos.append("modelo de veiculo diferente")

    base_anos = set(base_campos.get("anos") or [])
    anuncio_anos = set(anuncio_campos.get("anos") or [])
    if base_anos and anuncio_anos and not (base_anos & anuncio_anos):
        motivos.append("intervalo de ano diferente")

    base_pos = base_campos.get("posicoes") or {}
    anuncio_pos = anuncio_campos.get("posicoes") or {}
    for grupo, valor_base in base_pos.items():
        valor_anuncio = anuncio_pos.get(grupo)
        if valor_base and valor_anuncio and "AMBOS" not in {valor_base, valor_anuncio} and valor_base != valor_anuncio:
            motivos.append(f"posicao diferente ({grupo})")

    if motivos:
        conflitos_fortes = [
            motivo for motivo in motivos
            if motivo.startswith("posicao diferente")
        ]
        if conflitos_fortes:
            return {
                "decisao": "remover",
                "motivo": "; ".join(conflitos_fortes[:3]),
                "confianca": 95,
            }
        return {
            "decisao": "ia",
            "motivo": "possivel diferenca de aplicacao; validar com IA",
            "confianca": 0,
            "alertas": motivos[:4],
        }

    return {"decisao": "ia", "motivo": "sem conflito deterministico", "confianca": 0}


FAVORITOS_BUSCA_CACHE_LOCK = threading.Lock()


def _favoritos_busca_externa_limpar_texto(valor: object, limite: int = 360) -> str:
    texto = re.sub(r"\s+", " ", str(valor or "")).strip()
    if len(texto) > limite:
        return texto[:limite].rstrip() + "..."
    return texto


def _favoritos_busca_externa_cache_path(client_id: str, sku: str) -> str:
    sku_norm = _normalizar_sku_match_favoritos(str(sku or "")).strip() or "sku"
    nome_seguro = re.sub(r"[^A-Za-z0-9_-]+", "_", sku_norm).strip("_")[:80] or "sku"
    digest = hashlib.sha1(sku_norm.encode("utf-8", errors="ignore")).hexdigest()[:10]
    pasta = os.path.join(get_tenant_path(client_id), "favoritos_busca_cache")
    os.makedirs(pasta, exist_ok=True)
    return os.path.join(pasta, f"{nome_seguro}_{digest}.json")


def _favoritos_busca_externa_ler_cache(client_id: str, sku: str) -> dict:
    caminho = _favoritos_busca_externa_cache_path(client_id, sku)
    if not os.path.exists(caminho):
        return {"sku": str(sku or "").strip(), "consultas": {}}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f) or {}
        if not isinstance(dados, dict):
            return {"sku": str(sku or "").strip(), "consultas": {}}
        if not isinstance(dados.get("consultas"), dict):
            dados["consultas"] = {}
        return dados
    except Exception:
        return {"sku": str(sku or "").strip(), "consultas": {}}


def _favoritos_busca_externa_cache_key(query: str) -> str:
    normalizada = re.sub(r"\s+", " ", str(query or "").strip().lower())
    return hashlib.sha256(normalizada.encode("utf-8", errors="ignore")).hexdigest()


def _favoritos_busca_externa_cache_valido(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    ttl_dias = int(os.getenv("FAVORITOS_BUSCA_EXTERNA_CACHE_TTL_DIAS", "30") or "30")
    criado = str(item.get("created_at") or "").strip()
    if not criado:
        return False
    try:
        criado_dt = datetime.fromisoformat(criado)
    except Exception:
        return False
    return datetime.now() - criado_dt <= timedelta(days=max(1, ttl_dias))


def _favoritos_busca_externa_normalizar_resultados(resultados: list[dict], provider: str, query: str, max_results: int) -> dict:
    itens = []
    for item in resultados or []:
        if not isinstance(item, dict):
            continue
        titulo = _favoritos_busca_externa_limpar_texto(item.get("titulo") or item.get("title"), 160)
        url = _favoritos_busca_externa_limpar_texto(item.get("url") or item.get("link"), 260)
        trecho = _favoritos_busca_externa_limpar_texto(
            item.get("trecho") or item.get("snippet") or item.get("description") or item.get("content"),
            360,
        )
        if not (titulo or trecho or url):
            continue
        itens.append({"titulo": titulo, "url": url, "trecho": trecho})
        if len(itens) >= max_results:
            break
    return {
        "provider": provider,
        "query": query,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "resultados": itens,
    }


def _favoritos_busca_externa_provedores_configurados(*, incluir_fallback_publico: bool = True) -> list[str]:
    provedores: list[str] = []
    if os.getenv("TAVILY_API_KEY", "").strip():
        provedores.append("tavily")
    if os.getenv("BRAVE_SEARCH_API_KEY", "").strip():
        provedores.append("brave")
    if os.getenv("SERPAPI_KEY", "").strip():
        provedores.append("serpapi")
    if incluir_fallback_publico:
        provedores.append("duckduckgo_html")
    return provedores


def _favoritos_busca_externa_chamar_api(
    query: str,
    max_results: int = 4,
    timeout_s: float = 18,
    *,
    provider: str = "",
) -> dict:
    query = re.sub(r"\s+", " ", str(query or "").strip())
    if not query:
        return _favoritos_busca_externa_normalizar_resultados([], "none", query, max_results)
    timeout_s = max(2.0, min(float(timeout_s or 18), 30.0))

    provider_normalizado = str(provider or "").strip().lower()
    provedores = _favoritos_busca_externa_provedores_configurados(incluir_fallback_publico=True)
    if not provider_normalizado:
        provider_normalizado = provedores[0] if provedores else "duckduckgo_html"
    if provider_normalizado not in {"tavily", "brave", "serpapi", "duckduckgo_html"}:
        raise ValueError("Provedor de busca externa nao permitido.")

    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    if provider_normalizado == "tavily":
        if not tavily_key:
            return _favoritos_busca_externa_normalizar_resultados([], "tavily", query, max_results)
        resp = requests.post(
            "https://api.tavily.com/search",
            headers={"Content-Type": "application/json"},
            json={
                "api_key": tavily_key,
                "query": query,
                "search_depth": "basic",
                "max_results": max_results,
                "include_answer": False,
            },
            timeout=timeout_s,
            verify=requests_tls_verify(),
        )
        resp.raise_for_status()
        data = resp.json() or {}
        return _favoritos_busca_externa_normalizar_resultados(data.get("results") or [], "tavily", query, max_results)

    brave_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if provider_normalizado == "brave":
        if not brave_key:
            return _favoritos_busca_externa_normalizar_resultados([], "brave", query, max_results)
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": brave_key},
            params={"q": query, "count": max_results, "country": "br", "search_lang": "pt-br"},
            timeout=timeout_s,
            verify=requests_tls_verify(),
        )
        resp.raise_for_status()
        data = resp.json() or {}
        return _favoritos_busca_externa_normalizar_resultados(
            (data.get("web") or {}).get("results") or [],
            "brave",
            query,
            max_results,
        )

    serpapi_key = os.getenv("SERPAPI_KEY", "").strip()
    if provider_normalizado == "serpapi":
        if not serpapi_key:
            return _favoritos_busca_externa_normalizar_resultados([], "serpapi", query, max_results)
        resp = requests.get(
            "https://serpapi.com/search.json",
            params={"engine": "google", "q": query, "hl": "pt-br", "gl": "br", "num": max_results, "api_key": serpapi_key},
            timeout=timeout_s,
            verify=requests_tls_verify(),
        )
        resp.raise_for_status()
        data = resp.json() or {}
        return _favoritos_busca_externa_normalizar_resultados(data.get("organic_results") or [], "serpapi", query, max_results)

    resp = requests.get(
        "https://duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": "Mozilla/5.0 JK-Sistema/1.0"},
        timeout=timeout_s,
        verify=requests_tls_verify(),
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text or "", "lxml")
    resultados = []
    for node in soup.select(".result"):
        link = node.select_one(".result__a")
        snippet = node.select_one(".result__snippet")
        if not link:
            continue
        href = link.get("href") or ""
        if "duckduckgo.com/y.js" in href or "ad_domain=" in href:
            continue
        resultados.append({
            "titulo": link.get_text(" ", strip=True),
            "url": href,
            "trecho": snippet.get_text(" ", strip=True) if snippet else "",
        })
        if len(resultados) >= max_results:
            break
    return _favoritos_busca_externa_normalizar_resultados(resultados, "duckduckgo_html", query, max_results)


def _favoritos_busca_externa_cached(client_id: str, sku: str, query: str, max_results: int = 4) -> dict:
    query = re.sub(r"\s+", " ", str(query or "").strip())
    if not query:
        return {}
    chave = _favoritos_busca_externa_cache_key(query)
    with FAVORITOS_BUSCA_CACHE_LOCK:
        cache = _favoritos_busca_externa_ler_cache(client_id, sku)
        item_cache = (cache.get("consultas") or {}).get(chave)
        if item_cache and _favoritos_busca_externa_cache_valido(item_cache):
            return item_cache

    try:
        resultado = _favoritos_busca_externa_chamar_api(query, max_results=max_results)
    except Exception as exc:
        logger.warning("[Favoritos IA] Falha na busca externa para SKU %s: %s", sku, exc)
        if item_cache:
            return item_cache
        resultado = {
            "provider": "unavailable",
            "query": query,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "resultados": [],
            "erro": str(exc)[:240],
        }

    with FAVORITOS_BUSCA_CACHE_LOCK:
        cache = _favoritos_busca_externa_ler_cache(client_id, sku)
        cache.setdefault("consultas", {})[chave] = resultado
        _favoritos_busca_externa_salvar_cache(client_id, sku, cache)
    return resultado


def _favoritos_busca_externa_extrair_codigos(*textos: str) -> list[str]:
    bruto = " ".join(str(item or "") for item in textos)
    candidatos = re.findall(r"\b(?=[A-Z0-9-]*\d)[A-Z0-9]{2,}(?:[-./][A-Z0-9]{2,})+\b|\b(?=[A-Z0-9]*\d)[A-Z]{1,5}[0-9][A-Z0-9]{4,}\b", bruto.upper())
    codigos = []
    for codigo in candidatos:
        codigo = codigo.strip("-./ ")
        if not codigo or re.fullmatch(r"(19|20)\d{2}", codigo):
            continue
        if codigo not in codigos:
            codigos.append(codigo)
        if len(codigos) >= 6:
            break
    return codigos


def _favoritos_busca_externa_query(*partes: str) -> str:
    texto = " ".join(str(parte or "").strip() for parte in partes if str(parte or "").strip())
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:220]


def _favoritos_ranking_contexto_busca_externa(
    client_id: str,
    sku: str,
    titulo: str,
    descricao: str,
    pesquisas: list[str],
    anuncios_norm: list[dict],
) -> dict:
    if str(os.getenv("FAVORITOS_EXTERNAL_SEARCH_ENABLED", "1")).strip().lower() in {"0", "false", "no", "nao"}:
        return {}

    sku_norm = _normalizar_sku_match_favoritos(str(sku or "")).strip() or str(sku or "").strip()
    max_ads = int(os.getenv("FAVORITOS_EXTERNAL_SEARCH_MAX_ADS", "12") or "12")
    max_results = int(os.getenv("FAVORITOS_EXTERNAL_SEARCH_RESULTS", "4") or "4")
    max_ads = max(0, min(max_ads, 40))
    max_results = max(1, min(max_results, 8))

    try:
        codigos_base = _favoritos_busca_externa_extrair_codigos(titulo, descricao, " ".join(pesquisas or []))
        query_base = _favoritos_busca_externa_query(
            "autopeca aplicacao veiculo",
            " ".join(codigos_base[:3]),
            titulo,
            (pesquisas or [""])[0] if pesquisas else "",
        )
        contexto = {
            "cache": "info/<cliente>/favoritos_busca_cache por SKU",
            "produto_cadastro": [],
            "anuncios": [],
        }
        if query_base:
            base = _favoritos_busca_externa_cached(client_id, sku_norm, query_base, max_results=max_results)
            if base.get("resultados"):
                contexto["produto_cadastro"].append(base)

        consultas_vistas = {query_base.lower()} if query_base else set()
        for anuncio in anuncios_norm[:max_ads]:
            codigos = _favoritos_busca_externa_extrair_codigos(anuncio.get("titulo"), anuncio.get("descricao"))
            query = _favoritos_busca_externa_query(
                "autopeca aplicacao veiculo",
                " ".join(codigos[:3]),
                anuncio.get("titulo") or "",
            )
            chave_query = query.lower()
            if not query or chave_query in consultas_vistas:
                continue
            consultas_vistas.add(chave_query)
            resultado = _favoritos_busca_externa_cached(client_id, sku_norm, query, max_results=max_results)
            if resultado.get("resultados"):
                contexto["anuncios"].append({
                    "id": anuncio.get("id"),
                    "query": query,
                    "provider": resultado.get("provider"),
                    "resultados": resultado.get("resultados") or [],
                })

        if not contexto["produto_cadastro"] and not contexto["anuncios"]:
            return {}
        return contexto
    except Exception as exc:
        logger.warning("[Favoritos IA] Falha ao montar contexto de busca externa do SKU %s: %s", sku, exc)
        return {}


def _favoritos_ranking_json_obj(texto: str) -> dict:
    limpo = str(texto or "").strip()
    if not limpo:
        return {}
    limpo = re.sub(r"^```(?:json)?\s*", "", limpo, flags=re.IGNORECASE).strip()
    limpo = re.sub(r"\s*```$", "", limpo, flags=re.IGNORECASE).strip()
    for candidato in (limpo, limpo[limpo.find("{"):limpo.rfind("}") + 1] if "{" in limpo and "}" in limpo else ""):
        candidato = str(candidato or "").strip()
        if not candidato:
            continue
        try:
            data = json.loads(candidato)
        except Exception:
            continue
        return data if isinstance(data, dict) else {}
    return {}


def _favoritos_ranking_lista_ids(valor) -> set[str]:
    ids: set[str] = set()
    if not isinstance(valor, list):
        return ids
    for item in valor:
        if isinstance(item, dict):
            bruto = item.get("id") or item.get("mlb") or item.get("item_id") or item.get("anuncio")
        else:
            bruto = item
        item_id = _extrair_item_id(str(bruto or "")) or str(bruto or "").strip().upper().replace("-", "")
        if item_id:
            ids.add(item_id)
    return ids


def _favoritos_ranking_chamar_ia_json(
    client_id: str,
    mensagem: str,
    system_prompt: str,
    model: str | None = None,
) -> dict:
    model_name = _normalizar_ia_modelo_padrao(model or _ia_modelo_favoritos_configurado())
    if _modelo_eh_vertex_ai(model_name):
        model_curto = _vertex_modelo_nome_curto(model_name) or _vertex_ai_modelo_padrao()
        headers, project_id = _vertex_ai_headers_e_project()
        location = _vertex_ai_location()
        host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
        payload_json = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": mensagem}]}],
            "generationConfig": _vertex_generation_config(model_curto, json_mode=True),
        }
        resp = requests.post(
            f"https://{host}/v1/projects/{project_id}/locations/{location}/publishers/google/models/{model_curto}:generateContent",
            headers=headers,
            json=payload_json,
            verify=requests_tls_verify(),
            timeout=75,
        )
        if not resp.ok and resp.status_code == 400:
            payload_json["generationConfig"].pop("responseMimeType", None)
            resp = requests.post(
                f"https://{host}/v1/projects/{project_id}/locations/{location}/publishers/google/models/{model_curto}:generateContent",
                headers=headers,
                json=payload_json,
                verify=requests_tls_verify(),
                timeout=75,
            )
        if not resp.ok:
            raise RuntimeError(f"Vertex Gemini HTTP {resp.status_code}: {resp.text[:300]}")
        partes = resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
        texto = "\n".join(str(parte.get("text") or "").strip() for parte in partes if str(parte.get("text") or "").strip())
        return _favoritos_ranking_json_obj(texto)

    resposta = _favoritos_ia_texto_resposta(
        client_id,
        f"{system_prompt}\n\n{mensagem}",
        model=model_name,
    )
    return _favoritos_ranking_json_obj(resposta)


def _favoritos_ranking_filtrar_com_ia(
    client_id: str,
    sku: str,
    titulo: str,
    descricao: str,
    pesquisas: list[str],
    anuncios: list[dict],
    meus_anuncios: list[dict] | None = None,
    max_confirmados: int | None = None,
    usar_imagem: bool | None = None,
    model: str | None = None,
) -> dict:
    anuncios_norm = []
    usar_imagem_comparacao = bool(usar_imagem) or _ia_favoritos_usar_imagem_configurado()
    alvo_confirmados = None
    if max_confirmados is not None:
        try:
            alvo_confirmados = max(1, min(int(max_confirmados), 20))
        except Exception:
            alvo_confirmados = 8

    def _normalizar_anuncio_ia(anuncio: dict) -> dict | None:
        item_id = _favoritos_ranking_anuncio_id(anuncio)
        if not item_id:
            return None
        item_norm = {
            "id": item_id,
            "titulo": str((anuncio or {}).get("titulo") or (anuncio or {}).get("title") or "")[:240],
            "descricao": _favoritos_ranking_descricao_anuncio(anuncio)[:1800],
            "vendedor": str((anuncio or {}).get("vendedor") or "")[:120],
            "loja": str((anuncio or {}).get("loja") or (anuncio or {}).get("loja_sync") or "")[:120],
            "sku": str((anuncio or {}).get("sku") or (anuncio or {}).get("seller_sku") or "")[:120],
        }
        if usar_imagem_comparacao:
            imagem = str(
                (anuncio or {}).get("imagem")
                or (anuncio or {}).get("foto")
                or (anuncio or {}).get("thumbnail")
                or (anuncio or {}).get("thumbnail_url")
                or (anuncio or {}).get("secure_thumbnail")
                or ""
            ).strip()
            if imagem:
                item_norm["imagem"] = imagem[:700]
        return item_norm

    for anuncio in anuncios or []:
        item_norm = _normalizar_anuncio_ia(anuncio)
        if item_norm:
            anuncios_norm.append(item_norm)

    if not anuncios_norm:
        return {"manter_ids": [], "remover_ids": [], "removidos": []}

    _favoritos_ranking_completar_descricoes(anuncios_norm)
    meus_anuncios_norm = [
        item for item in (_normalizar_anuncio_ia(anuncio) for anuncio in (meus_anuncios or []))
        if item
    ][:12]
    if meus_anuncios_norm:
        _favoritos_ranking_completar_descricoes(meus_anuncios_norm)

    system_prompt = (
        "Voce e um especialista em catalogacao de autopecas e inteligencia de mercado automotivo. "
        "Sua tarefa e comparar anuncios de concorrentes com o MEU ANUNCIO e classificar cada item como "
        "mesmo produto, duvidoso ou nao e o mesmo produto. "
        "Quando houver duvida real por falta de dados, nao elimine o anuncio: marque como duvidoso. "
        "Retorne somente JSON valido. "
        "Nao use Markdown, explicacoes ou texto fora do JSON."
    )
    base = {
        "sku": str(sku or "").strip(),
        "titulo_sku": str(titulo or "").strip()[:700],
        "descricao_sku": str(descricao or "").strip()[:2400],
        "produto_cadastro": str(titulo or "").strip()[:700],
        "descricao_cadastro": str(descricao or "").strip()[:2400],
        "pesquisas_usadas": [str(item or "").strip() for item in (pesquisas or []) if str(item or "").strip()][:3],
        "meus_anuncios": meus_anuncios_norm,
        "max_confirmados": alvo_confirmados,
    }
    base_campos = _favoritos_ranking_extrair_campos_tecnicos(
        base["titulo_sku"],
        base["descricao_sku"],
        " ".join(base["pesquisas_usadas"]),
    )
    base["campos_tecnicos_sku"] = base_campos

    with FAVORITOS_RANKING_DECISOES_LOCK:
        decisoes_cache = _favoritos_ranking_ler_decisoes_cache(client_id, sku)
    decisoes_cache_alterado = False
    manter_ids: set[str] = set()
    remover_ids: set[str] = set()
    removidos_map: dict[str, str] = {}
    anuncios_para_ia: list[dict] = []

    def _registrar_remocao(item_id: str, motivo: str) -> None:
        if not item_id:
            return
        remover_ids.add(item_id)
        removidos_map[item_id] = str(motivo or "nao confirmado como mesmo produto").strip()[:180]

    def _registrar_decisao_cache(item: dict, decisao: str, motivo: str, origem: str, confianca: int = 0) -> None:
        nonlocal decisoes_cache_alterado
        item_id = str(item.get("id") or "").strip()
        assinatura = str(item.get("_assinatura_decisao") or "").strip()
        if not item_id or not assinatura:
            return
        decisoes_cache.setdefault("decisoes", {})[item_id] = {
            "versao": FAVORITOS_RANKING_DECISAO_VERSAO,
            "assinatura": assinatura,
            "decisao": decisao,
            "motivo": str(motivo or "").strip()[:180],
            "origem": origem,
            "confianca": int(confianca or 0),
            "campos_sku": base_campos,
            "campos_anuncio": item.get("campos_tecnicos") or {},
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        decisoes_cache_alterado = True

    def _atingiu_alvo_confirmados() -> bool:
        return alvo_confirmados is not None and len(manter_ids - remover_ids) >= alvo_confirmados

    for item in anuncios_norm:
        if _atingiu_alvo_confirmados():
            break
        item_id = str(item.get("id") or "").strip()
        item["campos_tecnicos"] = _favoritos_ranking_extrair_campos_tecnicos(
            item.get("titulo"),
            item.get("descricao"),
        )
        item["_assinatura_decisao"] = _favoritos_ranking_assinatura_decisao(sku, base, item)
        item_cache = (decisoes_cache.get("decisoes") or {}).get(item_id)
        if _favoritos_ranking_decisao_cache_valida(item_cache, item["_assinatura_decisao"]):
            decisao_cache = str(item_cache.get("decisao") or "").lower()
            motivo_cache = str(item_cache.get("motivo") or "decisao em cache").strip()
            if decisao_cache == "manter":
                manter_ids.add(item_id)
            else:
                _registrar_remocao(item_id, motivo_cache)
            continue

        pre = _favoritos_ranking_preavaliar(base_campos, item["campos_tecnicos"])
        item["pre_analise"] = pre
        if pre.get("decisao") == "manter":
            manter_ids.add(item_id)
            _registrar_decisao_cache(item, "manter", pre.get("motivo") or "codigo coincidente", "regra", pre.get("confianca") or 100)
            continue
        if pre.get("decisao") == "remover":
            motivo = pre.get("motivo") or "divergencia tecnica objetiva"
            _registrar_remocao(item_id, motivo)
            _registrar_decisao_cache(item, "remover", motivo, "regra", pre.get("confianca") or 95)
            continue

        anuncios_para_ia.append(item)

    contexto_busca_externa = _favoritos_ranking_contexto_busca_externa(
        client_id=client_id,
        sku=sku,
        titulo=titulo,
        descricao=descricao,
        pesquisas=pesquisas or [],
        anuncios_norm=anuncios_para_ia,
    )
    contexto_anuncios_por_id = {
        str(item.get("id") or ""): item
        for item in (contexto_busca_externa.get("anuncios") or [])
        if isinstance(item, dict) and item.get("id")
    } if contexto_busca_externa else {}

    chunk_tamanho = 18
    for inicio in range(0, len(anuncios_para_ia), chunk_tamanho):
        if _atingiu_alvo_confirmados():
            break
        chunk = anuncios_para_ia[inicio:inicio + chunk_tamanho]
        payload = dict(base)
        payload["anuncios"] = [
            {
                "id": item.get("id"),
                "titulo": item.get("titulo"),
                "descricao": item.get("descricao"),
                "vendedor": item.get("vendedor"),
                "campos_tecnicos": item.get("campos_tecnicos") or {},
                "pre_analise": item.get("pre_analise") or {},
                **({"imagem": item.get("imagem")} if usar_imagem_comparacao and item.get("imagem") else {}),
            }
            for item in chunk
        ]
        if contexto_busca_externa:
            payload["contexto_busca_externa"] = {
                "produto_cadastro": contexto_busca_externa.get("produto_cadastro") or [],
                "anuncios": [
                    contexto_anuncios_por_id.get(str(item.get("id") or ""))
                    for item in chunk
                    if contexto_anuncios_por_id.get(str(item.get("id") or ""))
                ],
            }
        mensagem = (
            "Analise e compare cada anuncio concorrente com o MEU ANUNCIO informado em produto_cadastro, "
            "descricao_cadastro, titulo_sku, descricao_sku, sku, pesquisas_usadas e, principalmente, meus_anuncios "
            "quando estiverem presentes. meus_anuncios sao os anuncios da nossa loja para o mesmo SKU; use titulo, "
            "descricao, SKU, loja e imagem deles como referencia principal. Cada item em anuncios contem titulo, "
            "descricao, imagem e posicao do anuncio rankeado quando disponivel. O objetivo e remover somente anuncios "
            "que claramente nao vendem a mesma peca/produto; anuncios compativeis ou duvidosos devem permanecer "
            "para revisao no ranking.\n\n"
            "DIRETRIZES DE COMPARACAO, em ordem de prioridade obrigatoria:\n"
            "1. Codigo do Produto (Part Number / SKU): se o codigo for identico, trate como o mesmo produto. "
            "Tenha atencao a codigos equivalentes de marcas diferentes, por exemplo Bosch vs Magneti Marelli, "
            "e nao considere codigo parecido como identico sem evidencia.\n"
            "2. Modelo da Peca: valide o componente exato. Exemplo: pastilha de freio dianteira nao e a mesma "
            "coisa que pastilha traseira; sensor, valvula, tampa, filtro, bico, junta e suporte nao devem ser "
            "misturados se a peca nao for exatamente a mesma.\n"
            "3. Titulo do Anuncio: analise palavras-chave essenciais e ignore apenas diferencas comerciais "
            "irrelevantes como frete, novo, original, promocao ou envio rapido.\n"
            "4. Aplicacao Veicular: valide marca, modelo, motorizacao e ano. A peca deve servir rigorosamente "
            "para a mesma frota quando essa informacao estiver clara. Se o MEU ANUNCIO serve para Gol 2010 "
            "a 2014 e o concorrente afirma somente Gol 2015 a 2020, remova. Se o concorrente listar apenas "
            "parte da aplicacao, omitir anos/modelos ou usar titulo mais generico, classifique como duvidoso "
            "em vez de remover, a menos que exista conflito objetivo.\n\n"
            + (
                "COMPARACAO POR IMAGEM:\n"
                "Quando o campo imagem estiver presente em meus_anuncios ou anuncios, use a foto como pista auxiliar "
                "para validar formato, componente, lado, kit e aparencia geral. Compare visualmente nossas fotos com "
                "as fotos dos anuncios rankeados quando isso ajudar a diferenciar produtos parecidos. "
                "A imagem nunca deve prevalecer contra codigo, titulo, descricao ou aplicacao veicular objetiva; "
                "ela serve apenas para reduzir duvidas quando os textos forem incompletos.\n\n"
                if usar_imagem_comparacao else ""
            )
            + "RECONHECIMENTO DE DUVIDAS E PESQUISA ATIVA:\n"
            "Quando existir contexto_busca_externa nos dados, use esses resultados de internet como apoio "
            "para pesquisar codigo da peca, modelo e aplicacao veicular antes de decidir. Se as informacoes "
            "do concorrente forem escassas, confusas ou se voce nao tiver certeza sobre a compatibilidade "
            "do codigo da peca, compare os resultados externos com as especificacoes do MEU ANUNCIO. "
            "Se o contexto externo nao estiver disponivel ou nao for conclusivo, seja conservador a favor "
            "de nao perder concorrentes corretos: use duvidoso quando faltar dado, e remova apenas quando "
            "houver divergencia tecnica evidente.\n\n"
            "REGRAS DE DECISAO:\n"
            "- MANTER: somente quando for o mesmo produto/peca, com codigo, modelo ou aplicacao veicular "
            "compativeis de forma clara com o MEU ANUNCIO.\n"
            "- DUVIDOSO: parece a mesma peca ou pode ser a mesma aplicacao, mas o concorrente omite codigo, "
            "anos, modelos, motorizacao ou descricao completa. Tambem use duvidoso quando a diferenca de "
            "ano/modelo puder ser apenas aplicacao parcial da mesma peca.\n"
            "- REMOVER: outra peca, outro lado/posicao, outra medida, outro kit, outro produto, codigo conflitante "
            "ou aplicacao explicitamente incompativel.\n\n"
            "Retorne no formato exato:\n"
            '{"manter":["MLB123"],"duvidosos":["MLB789"],"remover":[{"id":"MLB456","motivo":"produto diferente","confianca":95}]}\n\n'
            "Tudo que estiver em manter ou duvidosos ficara no ranking. Use remover apenas para incompatibilidade clara.\n\n"
            f"Dados:\n{json.dumps(payload, ensure_ascii=False)}"
        )
        data = _favoritos_ranking_chamar_ia_json(client_id, mensagem, system_prompt, model=model)
        ids_chunk = {item["id"] for item in chunk}
        ids_manter = _favoritos_ranking_lista_ids(
            data.get("manter") or data.get("manter_ids") or data.get("ids_manter") or data.get("mantidos")
        )
        ids_duvidosos = _favoritos_ranking_lista_ids(
            data.get("duvidosos") or data.get("duvidoso") or data.get("incertos") or data.get("revisar") or data.get("possiveis")
        )
        ids_remover = _favoritos_ranking_lista_ids(
            data.get("remover") or data.get("remover_ids") or data.get("ids_remover") or data.get("removidos")
        )
        tem_manter = any(chave in data for chave in ("manter", "manter_ids", "ids_manter", "mantidos"))
        tem_duvidosos = any(chave in data for chave in ("duvidosos", "duvidoso", "incertos", "revisar", "possiveis"))
        motivos_ia = {}
        for item in data.get("remover") or data.get("removidos") or []:
            if not isinstance(item, dict):
                continue
            item_id = _extrair_item_id(str(item.get("id") or item.get("mlb") or "")) or str(item.get("id") or "").strip().upper().replace("-", "")
            if item_id:
                motivos_ia[item_id] = str(item.get("motivo") or item.get("reason") or "removido pela IA").strip()[:180]

        if tem_manter or tem_duvidosos:
            ids_rejeitados = (ids_remover & ids_chunk) if ids_remover else set()
            ids_confirmados = ids_chunk - ids_rejeitados
            manter_ids.update(ids_confirmados)
            for item_id in ids_rejeitados:
                _registrar_remocao(item_id, motivos_ia.get(item_id) or "IA nao confirmou como mesmo produto")
        elif ids_remover:
            ids_rejeitados = ids_remover & ids_chunk
            ids_confirmados = ids_chunk - ids_rejeitados
            manter_ids.update(ids_confirmados)
            for item_id in ids_rejeitados:
                _registrar_remocao(item_id, motivos_ia.get(item_id) or "removido pela IA")
        else:
            ids_confirmados = set(ids_chunk)
            ids_rejeitados = set()
            manter_ids.update(ids_confirmados)

        itens_por_id = {str(item.get("id") or ""): item for item in chunk}
        for item_id in ids_confirmados:
            item = itens_por_id.get(item_id)
            if item:
                motivo = "duvidoso mantido pela IA" if item_id in ids_duvidosos else "confirmado pela IA"
                _registrar_decisao_cache(item, "manter", motivo, "ia", 80)
        for item_id in ids_rejeitados:
            item = itens_por_id.get(item_id)
            if item:
                _registrar_decisao_cache(item, "remover", removidos_map.get(item_id) or motivos_ia.get(item_id) or "removido pela IA", "ia", 80)
        if _atingiu_alvo_confirmados():
            break

    if decisoes_cache_alterado:
        with FAVORITOS_RANKING_DECISOES_LOCK:
            _favoritos_ranking_salvar_decisoes_cache(client_id, sku, decisoes_cache)

    removidos = [
        {"id": item_id, "motivo": motivo}
        for item_id, motivo in sorted(removidos_map.items())
    ]

    for item in anuncios_norm:
        item.pop("_assinatura_decisao", None)
        item.pop("campos_tecnicos", None)
        item.pop("pre_analise", None)

    return {
        "manter_ids": sorted(manter_ids - remover_ids),
        "remover_ids": sorted(remover_ids),
        "removidos": removidos,
        "interrompido_apos_confirmados": _atingiu_alvo_confirmados(),
        "confirmados_total": len(manter_ids - remover_ids),
    }


ML_FAVORITOS_STATUS_SKUS = ("active", "paused")


def _favoritos_ml_preco_ranking_simulado(req: FavoritosEfetivarPromocaoRequest) -> Optional[float]:
    sim = req.simulacao if isinstance(req.simulacao, dict) else {}
    for campo in ("precoRanking", "preco_ranking", "ranking_price"):
        valor = _parse_float_flex(sim.get(campo))
        if valor is not None and valor > 0:
            return float(valor)

    preco_competitivo = _parse_float_flex(
        sim.get("precoCompetitivo")
        if isinstance(sim, dict)
        else None
    )
    if preco_competitivo is None:
        preco_competitivo = _parse_float_flex(req.preco_competitivo)
    desconto_sorteado = _parse_float_flex(sim.get("descontoSorteado")) if isinstance(sim, dict) else None
    if preco_competitivo is not None and desconto_sorteado is not None:
        estimado = float(preco_competitivo) + float(desconto_sorteado)
        if estimado > 0:
            return estimado
    return None

PEER_EXPORTS = ['_favoritos_normalizar_texto_pesquisa', '_favoritos_codigo_compacto', '_favoritos_extrair_codigos_pesquisa', '_favoritos_codigo_pesquisa_2', '_favoritos_texto_contem_codigo', '_favoritos_normalizar_sem_acentos', '_favoritos_veiculo_marcas', '_favoritos_extrair_marca_global', '_favoritos_limpar_modelo_veiculo', '_favoritos_extrair_aplicacoes_veiculares', '_favoritos_aplicacao_texto', '_favoritos_pesquisa_tem_aplicacao', '_favoritos_garantir_aplicacao_pesquisa', '_favoritos_gerar_pesquisa_heuristica', '_favoritos_ia_texto_resposta', '_favoritos_ia_gemini_pesquisa_texto', '_favoritos_ia_vertex_pesquisa_texto', '_favoritos_ia_pesquisa_texto', '_favoritos_ia_extrair_json', '_favoritos_parse_resultado_ia', '_favoritos_limpar_resposta_campo_ia', '_favoritos_prompt_campo_pesquisa', '_favoritos_gerar_campo_pesquisa_ia', '_favoritos_ranking_anuncio_id', '_favoritos_ranking_descricao_anuncio', '_favoritos_ranking_buscar_descricao_publica', '_favoritos_ranking_completar_descricoes', 'FAVORITOS_RANKING_DECISOES_LOCK', 'FAVORITOS_RANKING_DECISAO_VERSAO', '_favoritos_ranking_decisoes_cache_path', '_favoritos_ranking_ler_decisoes_cache', '_favoritos_ranking_texto_norm', '_favoritos_ranking_codigo_norm', '_favoritos_ranking_extrair_codigos', '_favoritos_ranking_extrair_anos', 'FAVORITOS_RANKING_MARCAS', 'FAVORITOS_RANKING_MODELOS', 'FAVORITOS_RANKING_PECAS', '_favoritos_ranking_extrair_campos_tecnicos', '_favoritos_ranking_assinatura_decisao', '_favoritos_ranking_decisao_cache_valida', '_favoritos_ranking_preavaliar', 'FAVORITOS_BUSCA_CACHE_LOCK', '_favoritos_busca_externa_limpar_texto', '_favoritos_busca_externa_cache_path', '_favoritos_busca_externa_ler_cache', '_favoritos_busca_externa_cache_key', '_favoritos_busca_externa_cache_valido', '_favoritos_busca_externa_normalizar_resultados', '_favoritos_busca_externa_provedores_configurados', '_favoritos_busca_externa_chamar_api', '_favoritos_busca_externa_cached', '_favoritos_busca_externa_extrair_codigos', '_favoritos_busca_externa_query', '_favoritos_ranking_contexto_busca_externa', '_favoritos_ranking_json_obj', '_favoritos_ranking_lista_ids', '_favoritos_ranking_chamar_ia_json', '_favoritos_ranking_filtrar_com_ia', 'ML_FAVORITOS_STATUS_SKUS', '_favoritos_ml_preco_ranking_simulado']
__all__ = PEER_EXPORTS + ["configure_favoritos_ranking_ia_runtime"]

configure_favoritos_ranking_ia_runtime()
