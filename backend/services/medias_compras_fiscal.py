"""Fiscal, freight and M3 helpers for Medias Compras pedido lists."""

from __future__ import annotations

import os
import re
import unicodedata

import pandas as pd

from backend.services import medias_compras_common as medias_common
from backend.services.impostos import configure_impostos_runtime
from backend.services.impostos_ncm import (
    _conexao_db_ncm_impostos,
    _garantir_base_ncm_populada,
    _indice_ncm_referencia_por_ncm,
)
from backend.services.impostos_regras import _carregar_cadastro_para_impostos
from backend.services.medias_compras_common import *
from backend.services.runtime_bridge import bind_runtime_globals

_RUNTIME_NAMES = (
    "logger",
    "get_tenant_path",
    "TEMP_FILES_STORAGE",
    "TEMP_FILES_META",
    "LISTA_PEDIDO_XLSX_CACHE",
    "LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS",
    "PASTA_INFO",
)


def _sync_common_names() -> None:
    for name in medias_common.COMMON_EXPORTS:
        globals()[name] = getattr(medias_common, name)
    for name in _RUNTIME_NAMES:
        globals()[name] = getattr(medias_common, name)


def _configure_runtime_globals(runtime_module=None):
    if runtime_module is not None:
        configure_impostos_runtime(runtime_module)
    runtime = medias_common.configure_medias_compras_common_runtime(runtime_module)
    bind_runtime_globals(globals(), runtime)
    _sync_common_names()
    return runtime


_META_TITULO_CANDIDATOS_FORTES = [
    "produto bling", "produtos bling", "produto blig", "produtos blig",
    "titulo do produto em ingles", "titulo em ingles", "titulo", "nome",
    "product name", "produto", "traducao ptbr ou nome na bling",
]
_META_TITULO_CANDIDATOS_FALLBACK = [
    *_META_TITULO_CANDIDATOS_FORTES,
    "description", "product description", "descricao", "descrição",
]
_META_OEM_CANDIDATOS = [
    "oem", "codigo oem", "part number", "partnumber", "oem model",
    "oem/ model", "modelo", "numero do modelo", "model number", "cg oem",
]
_META_COR_LADO_CANDIDATOS = [
    "color side", "color/side", "cor lado", "cor/lado", "lado cor",
    "lado/cor", "lado", "cor", "color", "side",
]
_META_LINK_CANDIDATOS = [
    "link aliexpress", "url aliexpress", "aliexpress", "product link",
    "link produto", "url produto", "link", "url", "mlb principal", "url ml",
]
_META_FOTO_CANDIDATOS = ["foto", "imagem", "image", "url foto", "link foto", "foto produto"]


def _normalizar_coluna_cadastro_meta(valor: str) -> str:
    base = unicodedata.normalize("NFKD", str(valor or ""))
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", base.lower())


def _pick_cadastro_meta(row_dict: dict, cands: list[str]) -> str:
    norm_map = {}
    for k, v in (row_dict or {}).items():
        if v is None or not str(v).strip():
            continue
        k_norm = _normalizar_coluna_cadastro_meta(k)
        if k_norm and k_norm not in norm_map:
            norm_map[k_norm] = str(v).strip()

    for cand in (cands or []):
        c_norm = _normalizar_coluna_cadastro_meta(cand)
        if not c_norm:
            continue
        val = norm_map.get(c_norm)
        if val:
            return val
        for mk, mv in norm_map.items():
            if mk == c_norm or c_norm in mk or mk in c_norm:
                if mv:
                    return mv
    return ""


def _merge_meta_sku(destino: dict[str, dict], chave: str, payload: dict) -> None:
    if not chave:
        return
    atual = destino.setdefault(chave, {})
    for campo, valor in (payload or {}).items():
        valor_txt = str(valor or "").strip()
        if not valor_txt:
            continue
        atual_txt = str(atual.get(campo, "") or "").strip()
        if campo == "Link" and atual_txt:
            novo_lower = valor_txt.lower()
            atual_lower = atual_txt.lower()
            if ("aliexpress" in novo_lower and "aliexpress" not in atual_lower) or (
                valor_txt.startswith(("http://", "https://")) and not atual_txt.startswith(("http://", "https://"))
            ):
                atual[campo] = valor_txt
            continue
        if not atual_txt:
            atual[campo] = valor_txt


def _caminhos_cadastros_meta(client_id: str) -> list[str]:
    caminhos: list[str] = []

    def _add(caminho: str | None) -> None:
        caminho_txt = str(caminho or "").strip()
        if caminho_txt and os.path.exists(caminho_txt) and caminho_txt not in caminhos:
            caminhos.append(caminho_txt)

    try:
        _add(_migrar_arquivo_legado_para_tenant(client_id, "cadastro_produtos.csv", ARQUIVO_DB_CADASTRO_PRODUTOS))
    except Exception:
        pass

    try:
        pasta = get_tenant_path(client_id)
        def _ordem_cadastro_meta(nome: str) -> tuple[int, str]:
            nome_lower = str(nome or "").lower()
            if "classificacao_unicode" in nome_lower:
                return (0, nome_lower)
            if "classificacao" in nome_lower:
                return (1, nome_lower)
            if "backup" in nome_lower:
                return (2, nome_lower)
            return (3, nome_lower)

        for nome in sorted(os.listdir(pasta), key=_ordem_cadastro_meta):
            nome_txt = str(nome or "")
            if not nome_txt.lower().startswith("cadastro_produtos") or not nome_txt.lower().endswith(".csv"):
                continue
            _add(os.path.join(pasta, nome_txt))
    except Exception:
        pass

    return caminhos


def _meta_payload_cadastro(
    client_id: str,
    sku: str,
    row_dict: dict,
    permitir_descricao: bool = False,
    store_id: str | None = None,
) -> dict:
    titulo_cands = _META_TITULO_CANDIDATOS_FALLBACK if permitir_descricao else _META_TITULO_CANDIDATOS_FORTES
    return {
        "Foto": _resolver_foto_cadastro_sku(
            client_id,
            sku,
            _pick_cadastro_meta(row_dict, _META_FOTO_CANDIDATOS),
            store_id,
        ),
        TITULO_PRODUTO_INGLES_KEY: _pick_cadastro_meta(row_dict, titulo_cands),
        "OEM": _pick_cadastro_meta(row_dict, _META_OEM_CANDIDATOS),
        COR_LADO_LISTA_PEDIDO_KEY: _pick_cadastro_meta(row_dict, _META_COR_LADO_CANDIDATOS),
        "Link": _pick_cadastro_meta(row_dict, _META_LINK_CANDIDATOS),
    }


def _payload_fiscal_cadastro_row(row) -> dict:
    row_dict = row.to_dict() if hasattr(row, "to_dict") else {}
    row_norm_map = {_normalizar_coluna_cadastro_meta(k): k for k in row_dict.keys()}

    m3_individual = 0.0
    for chave_norm in ("cgm3individual", "m3individual", "m3", "cbm"):
        chave_real = row_norm_map.get(chave_norm)
        if not chave_real:
            continue
        m3_individual = max(0.0, _to_float_cadastro_m3(row_dict.get(chave_real, 0), 0.0))
        if m3_individual > 0:
            break

    return {
        "ncm": _normalizar_codigo_fiscal(row.get("ncm", "")),
        "cest": _normalizar_codigo_fiscal(row.get("cest", "")),
        "m3_individual": m3_individual,
    }


def _to_float_cadastro_m3(valor, padrao: float = 0.0) -> float:
    if isinstance(valor, (int, float)):
        return float(valor)
    txt = str(valor if valor is not None else "").strip()
    if not txt:
        return float(padrao)
    txt_num = txt.replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", txt_num):
        try:
            return float(txt_num)
        except Exception:
            pass
    return _to_float(valor, padrao)


def _merge_fiscal_sku_payload(destino: dict[str, dict], chave: str, payload: dict) -> None:
    if not chave or not isinstance(payload, dict):
        return
    ncm = str(payload.get("ncm", "") or "").strip()
    cest = str(payload.get("cest", "") or "").strip()
    m3_novo = max(0.0, _to_float(payload.get("m3_individual", 0), 0.0))
    if not (ncm or cest or m3_novo > 0):
        return

    atual = destino.setdefault(chave, {})
    if ncm and not str(atual.get("ncm", "") or "").strip():
        atual["ncm"] = ncm
    if cest and not str(atual.get("cest", "") or "").strip():
        atual["cest"] = cest
    if m3_novo > 0 and max(0.0, _to_float(atual.get("m3_individual", 0), 0.0)) <= 0:
        atual["m3_individual"] = m3_novo


def _recalcular_frete_internacional_itens_lista(
    client_id: str,
    itens: list[dict],
    *,
    loja: str = "",
) -> list[dict]:
    itens_norm = [_normalizar_item_lista_pedido(i) for i in (itens or [])]
    if not itens_norm:
        return []

    itens_enriquecidos = _enriquecer_itens_lista_pedido_com_impostos(
        client_id,
        itens_norm,
        incluir_impostos=True,
        loja=loja,
    )

    total_usd = 0.0
    total_m3 = 0.0

    for idx, item in enumerate(itens_norm):
        qtd = max(0.0, _to_float(item.get("Quantidade", 0), 0.0))
        vu = max(0.0, _to_float(item.get("Valor unidade", 0), 0.0))
        vt = max(0.0, _to_float(item.get("Valor total", 0), 0.0))
        total_usd += (vt if vt > 0 else (qtd * vu))

        enr = itens_enriquecidos[idx] if idx < len(itens_enriquecidos) else {}
        m3_item = max(
            0.0,
            _to_float(
                enr.get("MÃ‚Â³", enr.get("M3", enr.get("m3", 0))),
                0.0,
            ),
        )
        total_m3 += m3_item

    valor_por_m3 = (total_usd / total_m3) if (total_usd > 0 and total_m3 > 0) else 0.0

    for idx, item in enumerate(itens_norm):
        enr = itens_enriquecidos[idx] if idx < len(itens_enriquecidos) else {}
        item["Foto"] = str(enr.get("Foto", "") or "").strip()
        for chave_meta in (TITULO_PRODUTO_INGLES_KEY, "OEM", COR_LADO_LISTA_PEDIDO_KEY, "Link"):
            if not str(item.get(chave_meta, "") or "").strip() and str(enr.get(chave_meta, "") or "").strip():
                item[chave_meta] = str(enr.get(chave_meta, "") or "").strip()

        for chave_fiscal in ("NCM", "CEST", "Descricao do NCM", "Imposto"):
            valor_fiscal = enr.get(chave_fiscal)
            if valor_fiscal is not None and str(valor_fiscal).strip():
                item[chave_fiscal] = valor_fiscal

        if str(enr.get("NCM", "") or "").strip():
            for chave_aliquota in ("II", "IPI", "PIS", "COFINS"):
                if chave_aliquota in enr and str(enr.get(chave_aliquota, "")).strip():
                    item[chave_aliquota] = enr.get(chave_aliquota)

        m3_item = max(
            0.0,
            _to_float(
                enr.get("MÃ‚Â³", enr.get("M3", enr.get("m3", 0))),
                0.0,
            ),
        )

        m3_individual = max(
            0.0,
            _to_float(
                enr.get("M3 individual", enr.get("m3_individual", 0)),
                0.0,
            ),
        )

        if m3_item > 0:
            m3_total_fmt = round(m3_item, 6)
            item[CBM_LISTA_PEDIDO_KEY] = m3_total_fmt
            item["M3"] = m3_total_fmt
            item["m3"] = m3_total_fmt
        if m3_individual > 0:
            item["M3 individual"] = round(m3_individual, 6)

        if valor_por_m3 > 0 and m3_item > 0:
            item["Frete Internacional"] = round(valor_por_m3 * m3_item, 2)
        else:
            item["Frete Internacional"] = max(0.0, _to_float(item.get("Frete Internacional", 0), 0.0))

    return itens_norm


def _indice_ncm_referencia_aliquotas(client_id: str) -> dict[str, dict]:
    _garantir_base_ncm_populada(client_id)
    conn = _conexao_db_ncm_impostos(client_id)
    try:
        rows = conn.execute(
            """
            SELECT ncm, ii, ipi, pis, cofins
            FROM ncm_referencia
            """
        ).fetchall()
        idx = {}
        for r in rows:
            ncm = _normalizar_codigo_fiscal(r["ncm"])
            if not ncm:
                continue
            idx[ncm] = {
                "ii": float(r["ii"] or 0),
                "ipi": float(r["ipi"] or 0),
                "pis": float(r["pis"] or 0),
                "cofins": float(r["cofins"] or 0),
            }
        return idx
    finally:
        conn.close()


def _enriquecer_itens_lista_pedido_com_impostos(
    client_id: str,
    itens: list[dict],
    incluir_impostos: bool = True,
    *,
    loja: str = "",
) -> list[dict]:
    lista_itens = itens if isinstance(itens, list) else []
    if not lista_itens:
        return []

    skus_interesse: set[str] = set()
    for item in lista_itens:
        if not isinstance(item, dict):
            continue
        sku_item = str(item.get("SKU", "") or "").strip()
        for k in _sku_lookup_keys_sync_ncm(sku_item):
            if k:
                skus_interesse.add(k)

    def _sku_interessa(k1: str, k2: str, k3: str) -> bool:
        return not skus_interesse or any(k and k in skus_interesse for k in (k1, k2, k3))

    df_cad, caminho_cadastro_principal = _carregar_cadastro_para_impostos(client_id)
    sku_para_fiscal: dict[str, dict] = {}
    sku_para_meta: dict[str, dict] = {}
    sku_para_meta_fallback: dict[str, dict] = {}
    from backend.services.cadastro_compatibilidade import (
        visao_produtos_cadastro_contexto_loja,
    )

    try:
        contexto_cadastro = visao_produtos_cadastro_contexto_loja(
            client_id,
            loja,
        )
    except RuntimeError:
        contexto_cadastro = {
            "produtos": [],
            "store_id": "",
            "loja_resolvida": False,
            "scope": "unavailable",
        }
    store_id_cadastro = str(contexto_cadastro.get("store_id") or "").strip()
    contexto_fotos_indisponivel = (
        str(contexto_cadastro.get("scope") or "").strip() == "unavailable"
    )
    skus_controlados_contexto: set[str] = set()
    for sku_controlado in contexto_cadastro.get("skus_controlados") or set():
        for chave_sku in _sku_lookup_keys_sync_ncm(sku_controlado):
            if chave_sku:
                skus_controlados_contexto.add(chave_sku)
    foto_contextual_por_sku: dict[str, str] = {}

    def _sku_controlado(k1: str, k2: str, k3: str) -> bool:
        return any(
            chave and chave in skus_controlados_contexto
            for chave in (k1, k2, k3)
        )

    if not df_cad.empty:
        def _norm_col_name(v: str) -> str:
            base = unicodedata.normalize("NFKD", str(v or ""))
            base = "".join(ch for ch in base if not unicodedata.combining(ch))
            return re.sub(r"[^a-z0-9]+", "", base.lower())

        def _pick_cadastro(row_dict: dict, cands: list[str]) -> str:
            norm_map = {}
            for k, v in (row_dict or {}).items():
                if v is None or not str(v).strip():
                    continue
                k_norm = _norm_col_name(k)
                if k_norm and k_norm not in norm_map:
                    norm_map[k_norm] = str(v).strip()

            for cand in (cands or []):
                c_norm = _norm_col_name(cand)
                if not c_norm:
                    continue
                val = norm_map.get(c_norm)
                if val:
                    return val
                for mk, mv in norm_map.items():
                    if mk == c_norm or c_norm in mk or mk in c_norm:
                        if mv:
                            return mv
            return ""

        for _, row in df_cad.iterrows():
            sku = str(row.get("sku", "") or "").strip()
            if not sku:
                continue
            k1, k2, k3 = _sku_lookup_keys_sync_ncm(sku)
            if not _sku_interessa(k1, k2, k3):
                continue

            row_dict = row.to_dict() if hasattr(row, "to_dict") else {}
            payload = _payload_fiscal_cadastro_row(row)
            for k in (k1, k2, k3):
                _merge_fiscal_sku_payload(sku_para_fiscal, k, payload)

            titulo_cad = _pick_cadastro(row_dict, _META_TITULO_CANDIDATOS_FORTES)
            foto_cad = ""
            if not contexto_fotos_indisponivel and not _sku_controlado(k1, k2, k3):
                foto_cad = _resolver_foto_cadastro_sku(
                    client_id,
                    sku,
                    _pick_cadastro(row_dict, _META_FOTO_CANDIDATOS),
                    store_id_cadastro or None,
                )
            oem_cad = _pick_cadastro(row_dict, _META_OEM_CANDIDATOS)
            cor_lado_cad = _pick_cadastro(row_dict, _META_COR_LADO_CANDIDATOS)
            link_cad = _pick_cadastro(row_dict, _META_LINK_CANDIDATOS)

            meta_payload = {
                "Foto": foto_cad,
                TITULO_PRODUTO_INGLES_KEY: titulo_cad,
                "OEM": oem_cad,
                COR_LADO_LISTA_PEDIDO_KEY: cor_lado_cad,
                "Link": link_cad,
            }
            meta_payload_fallback = _meta_payload_cadastro(
                client_id,
                sku,
                row_dict,
                permitir_descricao=True,
                store_id=store_id_cadastro or None,
            )
            if contexto_fotos_indisponivel or _sku_controlado(k1, k2, k3):
                meta_payload_fallback["Foto"] = ""
            for k in (k1, k2, k3):
                _merge_meta_sku(sku_para_meta, k, meta_payload)
                _merge_meta_sku(sku_para_meta_fallback, k, meta_payload_fallback)

    caminho_principal_abs = os.path.abspath(caminho_cadastro_principal) if caminho_cadastro_principal else ""
    for caminho_meta in _caminhos_cadastros_meta(client_id):
        try:
            if caminho_principal_abs and os.path.abspath(caminho_meta) == caminho_principal_abs:
                continue
            df_meta = pd.read_csv(caminho_meta, dtype=str).fillna("")
            if df_meta.empty:
                continue
            df_meta.columns = [str(c or "").strip().lower() for c in df_meta.columns]
            if "sku" not in df_meta.columns:
                continue
        except Exception:
            continue

        for _, row in df_meta.iterrows():
            sku = str(row.get("sku", "") or "").strip()
            if not sku:
                continue
            k1, k2, k3 = _sku_lookup_keys_sync_ncm(sku)
            if not _sku_interessa(k1, k2, k3):
                continue
            row_dict = row.to_dict() if hasattr(row, "to_dict") else {}
            payload = _payload_fiscal_cadastro_row(row)
            meta_payload = _meta_payload_cadastro(
                client_id,
                sku,
                row_dict,
                permitir_descricao=False,
                store_id=store_id_cadastro or None,
            )
            meta_payload_fallback = _meta_payload_cadastro(
                client_id,
                sku,
                row_dict,
                permitir_descricao=True,
                store_id=store_id_cadastro or None,
            )
            if contexto_fotos_indisponivel or _sku_controlado(k1, k2, k3):
                meta_payload["Foto"] = ""
                meta_payload_fallback["Foto"] = ""
            for k in (k1, k2, k3):
                _merge_fiscal_sku_payload(sku_para_fiscal, k, payload)
                _merge_meta_sku(sku_para_meta, k, meta_payload)
                _merge_meta_sku(sku_para_meta_fallback, k, meta_payload_fallback)

    for row_dict in contexto_cadastro.get("produtos") or []:
        if not isinstance(row_dict, dict):
            continue
        sku = str(row_dict.get("sku") or "").strip()
        if not sku:
            continue
        k1, k2, k3 = _sku_lookup_keys_sync_ncm(sku)
        if not _sku_interessa(k1, k2, k3):
            continue
        foto_contextual = _resolver_foto_cadastro_sku(
            client_id,
            sku,
            _pick_cadastro_meta(row_dict, _META_FOTO_CANDIDATOS),
            store_id_cadastro or None,
        )
        for k in (k1, k2, k3):
            if not k:
                continue
            foto_contextual_por_sku[k] = foto_contextual
            meta_atual = sku_para_meta.setdefault(k, {})
            if foto_contextual:
                meta_atual["Foto"] = foto_contextual
            else:
                meta_atual.pop("Foto", None)

    for k, payload in sku_para_meta_fallback.items():
        atual = sku_para_meta.setdefault(k, {})
        if not str(atual.get(TITULO_PRODUTO_INGLES_KEY, "") or "").strip():
            titulo_fb = str((payload or {}).get(TITULO_PRODUTO_INGLES_KEY, "") or "").strip()
            if titulo_fb:
                atual[TITULO_PRODUTO_INGLES_KEY] = titulo_fb

    idx_aliquotas = _indice_ncm_referencia_aliquotas(client_id) if incluir_impostos else {}
    idx_ncm_ref = _indice_ncm_referencia_por_ncm(client_id) if incluir_impostos else {}
    saida = []
    for item in lista_itens:
        if not isinstance(item, dict):
            continue
        novo = dict(item)
        sku_item = str(novo.get("SKU", "") or "").strip()

        k1, k2, k3 = _sku_lookup_keys_sync_ncm(sku_item)
        fiscal_cad = sku_para_fiscal.get(k1) or sku_para_fiscal.get(k2) or sku_para_fiscal.get(k3) or {}
        meta_cad = sku_para_meta.get(k1) or sku_para_meta.get(k2) or sku_para_meta.get(k3) or {}

        if contexto_fotos_indisponivel or _sku_controlado(k1, k2, k3):
            foto_contextual = ""
            for chave_sku in (k1, k2, k3):
                if chave_sku in foto_contextual_por_sku:
                    foto_contextual = str(
                        foto_contextual_por_sku[chave_sku] or ""
                    ).strip()
                    break
            novo["Foto"] = foto_contextual
        else:
            novo["Foto"] = _resolver_foto_cadastro_sku(
                client_id,
                sku_item,
                str(novo.get("Foto", "") or ""),
                store_id_cadastro or None,
            )
            if not str(novo.get("Foto", "") or "").strip() and str(meta_cad.get("Foto", "") or "").strip():
                novo["Foto"] = str(meta_cad.get("Foto", "") or "").strip()
        titulo_atual = ""
        for chave_titulo in TITULO_PRODUTO_INGLES_KEYS_LEGADO:
            titulo_atual = str(novo.get(chave_titulo, "") or "").strip()
            if titulo_atual:
                break
        if not titulo_atual and str(meta_cad.get(TITULO_PRODUTO_INGLES_KEY, "") or "").strip():
            novo[TITULO_PRODUTO_INGLES_KEY] = str(meta_cad.get(TITULO_PRODUTO_INGLES_KEY, "") or "").strip()
        if not str(novo.get("OEM", "") or "").strip() and str(meta_cad.get("OEM", "") or "").strip():
            novo["OEM"] = str(meta_cad.get("OEM", "") or "").strip()
        if not str(novo.get(COR_LADO_LISTA_PEDIDO_KEY, "") or "").strip() and str(meta_cad.get(COR_LADO_LISTA_PEDIDO_KEY, "") or "").strip():
            novo[COR_LADO_LISTA_PEDIDO_KEY] = str(meta_cad.get(COR_LADO_LISTA_PEDIDO_KEY, "") or "").strip()
        if not str(novo.get("Link", "") or "").strip() and str(meta_cad.get("Link", "") or "").strip():
            novo["Link"] = str(meta_cad.get("Link", "") or "").strip()

        ncm_item = _normalizar_codigo_fiscal(novo.get("NCM", "")) or _normalizar_codigo_fiscal(fiscal_cad.get("ncm", ""))
        cest_item = _normalizar_codigo_fiscal(novo.get("CEST", "")) or _normalizar_codigo_fiscal(fiscal_cad.get("cest", ""))
        aliq = idx_aliquotas.get(ncm_item, {}) if ncm_item else {}
        ncm_ref = idx_ncm_ref.get(ncm_item, {}) if ncm_item else {}

        if ncm_item:
            novo["NCM"] = ncm_item
        if cest_item:
            novo["CEST"] = cest_item

        descricao_ncm = str(ncm_ref.get("descricao_completa") or ncm_ref.get("descricao") or "").strip()
        if descricao_ncm:
            novo["DescriÃƒÂ§ÃƒÂ£o do NCM"] = descricao_ncm
            novo["Descricao do NCM"] = descricao_ncm
            if not str(novo.get("DescriÃƒÂ§ÃƒÂ£o", "") or "").strip():
                novo["DescriÃƒÂ§ÃƒÂ£o"] = descricao_ncm
            if not str(novo.get("Descricao", "") or "").strip():
                novo["Descricao"] = descricao_ncm

        ii = float(aliq.get("ii", 0) or 0)
        ipi = float(aliq.get("ipi", 0) or 0)
        pis = float(aliq.get("pis", 0) or 0)
        cofins = float(aliq.get("cofins", 0) or 0)

        if incluir_impostos and ncm_item:
            novo["II"] = ii
            novo["IPI"] = ipi
            novo["PIS"] = pis
            novo["COFINS"] = cofins

        qtd_item = max(0.0, _to_float(novo.get("Quantidade", 0), 0.0))
        m3_individual = max(0.0, _to_float(fiscal_cad.get("m3_individual", 0), 0.0))
        m3_total = round(m3_individual * qtd_item, 6)
        if m3_total > 0:
            novo["MÃ‚Â³"] = m3_total
            novo["M3"] = m3_total
            novo["m3"] = m3_total
            novo[CBM_LISTA_PEDIDO_KEY] = m3_total
        if m3_individual > 0:
            novo["MÃ‚Â³ individual"] = m3_individual
            novo["M3 individual"] = m3_individual

        if incluir_impostos and ncm_item:
            novo["Imposto"] = f"II {ii:.2f}% | IPI {ipi:.2f}% | PIS {pis:.2f}% | COFINS {cofins:.2f}%"

        saida.append(novo)

    return saida


def _construir_mapa_m3_sku(client_id: str) -> dict[str, float]:
    """LÃƒÂª o catÃƒÂ¡logo de produtos uma vez e retorna {chave_sku: m3_individual}."""
    caminhos = _caminhos_cadastros_meta(client_id)
    if not caminhos:
        return {}

    def _nc(v: str) -> str:
        base = unicodedata.normalize("NFKD", str(v or ""))
        base = "".join(ch for ch in base if not unicodedata.combining(ch))
        return re.sub(r"[^a-z0-9]+", "", base.lower())

    mapa: dict[str, float] = {}
    for arquivo in caminhos:
        try:
            df = pd.read_csv(arquivo, dtype=str).fillna("")
            df.columns = [str(c or "").strip().lower() for c in df.columns]
            if "sku" not in df.columns:
                continue
        except Exception:
            continue

        for _, row in df.iterrows():
            sku = str(row.get("sku", "") or "").strip()
            if not sku:
                continue
            row_dict = row.to_dict() if hasattr(row, "to_dict") else {}
            row_norm_map = {_nc(k): k for k in row_dict.keys()}
            m3_individual = 0.0
            for chave_norm in ["cgm3individual", "m3individual", "m3", "cbm"]:
                chave_real = row_norm_map.get(chave_norm)
                if not chave_real:
                    continue
                m3_individual = max(0.0, _to_float_cadastro_m3(row_dict.get(chave_real, 0), 0.0))
                if m3_individual > 0:
                    break
            if m3_individual > 0:
                k1, k2, k3 = _sku_lookup_keys_sync_ncm(sku)
                for k in (k1, k2, k3):
                    if k and k not in mapa:
                        mapa[k] = m3_individual
    return mapa



def configure_medias_compras_fiscal_runtime(runtime_module=None):
    return _configure_runtime_globals(runtime_module)


configure_medias_compras_fiscal_runtime()

__all__ = [
    "configure_medias_compras_fiscal_runtime",
    "_recalcular_frete_internacional_itens_lista",
    "_indice_ncm_referencia_aliquotas",
    "_enriquecer_itens_lista_pedido_com_impostos",
    "_construir_mapa_m3_sku",
]
