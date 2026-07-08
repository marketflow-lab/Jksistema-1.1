"""Import tax rates and monofasico helpers for Impostos."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import time
import unicodedata
import uuid
from datetime import datetime
from typing import Any, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup
from fastapi import Depends, Header, HTTPException, Request, UploadFile
from jose import JWTError

from backend.schemas import (
    ImpostoRegraRequest,
    ImpostosSimulacaoRequest,
    SimuladorCalculoRequest,
    SiscomexAliquotasRequest,
    SiscomexConfigRequest,
    SiscomexConsultaRequest,
    SiscomexFundamentoOpcionalRequest,
)
from backend.services.impostos_common import *
from backend.services.impostos_context import get_tenant_id, get_tenant_path

logger = None

def _configure_runtime_globals(target_globals, runtime_module=None, peers=None):
    if runtime_module is not None:
        runtime_logger = getattr(runtime_module, "logger", None)
        if runtime_logger is not None:
            target_globals["logger"] = runtime_logger
        for name in (
            "decodificar_access_token",
            "ler_csv_seguro",
            "salvar_csv_seguro",
            "_migrar_arquivo_legado_para_tenant",
            "ARQUIVO_DB_CADASTRO_PRODUTOS",
            "ARQUIVO_DB_PRODUTOS",
            "ARQUIVO_NCM_XLSX",
            "ARQUIVO_NCM1_XLSX",
            "pd",
        ):
            if hasattr(runtime_module, name):
                target_globals[name] = getattr(runtime_module, name)
    if peers:
        target_globals.update(peers)
    return runtime_module


def configure_impostos_aliquotas_runtime(runtime_module=None, peers=None):
    return _configure_runtime_globals(globals(), runtime_module, peers)


def _arquivo_db_aliquotas_importacao(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "impostos_aliquotas_importacao.db")


def _conexao_db_aliquotas_importacao(client_id: str) -> sqlite3.Connection:
    db_path = _arquivo_db_aliquotas_importacao(client_id)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS aliquotas_importacao (
            ncm TEXT PRIMARY KEY,
            ncm_formatado TEXT,
            descricao TEXT,
            ii REAL,
            ipi REAL,
            pis REAL,
            cofins REAL,
            fonte TEXT,
            fonte_url TEXT,
            observacoes TEXT,
            dados_extras_json TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_aliquotas_importacao_updated ON aliquotas_importacao(updated_at)")
    conn.commit()
    return conn


def _row_aliquota_importacao_para_dict(row: sqlite3.Row | dict | None) -> dict | None:
    if not row:
        return None
    get = row.get if isinstance(row, dict) else row.__getitem__
    try:
        extras = json.loads(get("dados_extras_json") or "{}")
    except Exception:
        extras = {}
    return {
        "ncm": str(get("ncm") or ""),
        "ncm_formatado": str(get("ncm_formatado") or ""),
        "descricao": str(get("descricao") or ""),
        "ii": _to_float(get("ii"), 0.0),
        "ipi": _to_float(get("ipi"), 0.0),
        "pis": _to_float(get("pis"), 0.0),
        "cofins": _to_float(get("cofins"), 0.0),
        "fonte": str(get("fonte") or ""),
        "fonte_url": str(get("fonte_url") or ""),
        "observacoes": str(get("observacoes") or ""),
        "dados_extras": extras if isinstance(extras, dict) else {},
        "updated_at": str(get("updated_at") or ""),
    }


def _buscar_aliquotas_importacao_local(client_id: str, ncm: str) -> dict | None:
    ncm_norm = _normalizar_codigo_fiscal(ncm)
    if not ncm_norm:
        return None
    conn = _conexao_db_aliquotas_importacao(client_id)
    try:
        row = conn.execute(
            "SELECT * FROM aliquotas_importacao WHERE ncm = ? LIMIT 1",
            (ncm_norm,),
        ).fetchone()
        return _row_aliquota_importacao_para_dict(row)
    finally:
        conn.close()


def _salvar_aliquotas_importacao_local(client_id: str, registro: dict) -> dict:
    ncm_norm = _normalizar_codigo_fiscal(registro.get("ncm", ""))
    if not ncm_norm or len(ncm_norm) != 8:
        raise ValueError("NCM invalido para salvar alÃƒÂ­quotas de importaÃƒÂ§ÃƒÂ£o.")
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "ncm": ncm_norm,
        "ncm_formatado": str(registro.get("ncm_formatado") or _formatar_ncm_pontos(ncm_norm)).strip(),
        "descricao": str(registro.get("descricao") or "").strip(),
        "ii": _normalizar_aliquota_percentual(registro.get("ii", 0)),
        "ipi": _normalizar_aliquota_percentual(registro.get("ipi", 0)),
        "pis": _normalizar_aliquota_percentual(registro.get("pis", 0)),
        "cofins": _normalizar_aliquota_percentual(registro.get("cofins", 0)),
        "fonte": str(registro.get("fonte") or "Base local de alÃƒÂ­quotas de importaÃƒÂ§ÃƒÂ£o").strip(),
        "fonte_url": str(registro.get("fonte_url") or "").strip(),
        "observacoes": str(registro.get("observacoes") or "").strip(),
        "dados_extras_json": json.dumps(registro.get("dados_extras") or {}, ensure_ascii=False),
        "updated_at": str(registro.get("updated_at") or agora).strip(),
    }
    conn = _conexao_db_aliquotas_importacao(client_id)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO aliquotas_importacao (
                ncm, ncm_formatado, descricao, ii, ipi, pis, cofins,
                fonte, fonte_url, observacoes, dados_extras_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["ncm"], payload["ncm_formatado"], payload["descricao"],
                payload["ii"], payload["ipi"], payload["pis"], payload["cofins"],
                payload["fonte"], payload["fonte_url"], payload["observacoes"],
                payload["dados_extras_json"], payload["updated_at"],
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return payload


def _percentual_de_texto(valor: str) -> float | None:
    return _normalizar_percentual_ttce(valor)


def _regex_percentual(texto: str, padrao: str) -> float | None:
    m = re.search(padrao, texto, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    return _percentual_de_texto(m.group(1))


def _buscar_aliquotas_buscador_ncm(ncm: str) -> dict:
    ncm_norm = _normalizar_codigo_fiscal(ncm)
    if not ncm_norm or len(ncm_norm) != 8:
        raise ValueError("NCM invalido para busca gratuita.")
    ncm_fmt = _formatar_ncm_pontos(ncm_norm)
    url = f"https://buscadorncm.com.br/ncm/{ncm_norm}"
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": "Mozilla/5.0 (compatible; JKSistema/1.0)",
    }
    resp = requests.get(url, headers=headers, timeout=20)
    if not resp.ok:
        raise RuntimeError(f"Buscador NCM retornou HTTP {resp.status_code}.")
    soup = BeautifulSoup(resp.text, "html.parser")
    texto = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

    descricao = ""
    desc_el = soup.select_one("p.description")
    if desc_el:
        descricao = desc_el.get_text(" ", strip=True).strip(" -Ã¢â‚¬â€.")
    if not descricao:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        meta_txt = str(meta_desc.get("content") or "") if meta_desc else ""
        m_meta = re.search(rf"{re.escape(ncm_fmt)}\s*:\s*([^.]*)", meta_txt, flags=re.IGNORECASE)
        if m_meta:
            descricao = m_meta.group(1).strip(" -Ã¢â‚¬â€.")
    if not descricao:
        m_desc = re.search(rf"{re.escape(ncm_fmt)}\s*-\s*([^.|Ã‚Â·]+)", texto, flags=re.IGNORECASE)
        if m_desc:
            descricao = m_desc.group(1).strip(" -Ã¢â‚¬â€.")

    ii = (
        _regex_percentual(texto, r"\bII\s+([0-9]+(?:[,.][0-9]+)?)\s*%")
        or _regex_percentual(texto, r"II\s*[Ã¢â‚¬â€-]\s*Imp\.?\s*Importa[ÃƒÂ§c][aÃƒÂ£]o\s+([0-9]+(?:[,.][0-9]+)?)\s*%")
        or _regex_percentual(texto, r"Imposto de Importa[ÃƒÂ§c][aÃƒÂ£]o.*?al[iÃƒÂ­]quota\s+(?:ÃƒÂ©\s+)?de\s+([0-9]+(?:[,.][0-9]+)?)\s*%")
    )
    ipi = (
        _regex_percentual(texto, r"\bIPI\s+([0-9]+(?:[,.][0-9]+)?)\s*%")
        or _regex_percentual(texto, r"Al[iÃƒÂ­]quota\s+IPI\s+([0-9]+(?:[,.][0-9]+)?)\s*%")
        or _regex_percentual(texto, r"([0-9]+(?:[,.][0-9]+)?)\s*%\s+de\s+IPI")
    )
    pis_cofins = _pis_cofins_importacao_por_ncm(ncm_norm, descricao)

    if ii is None and ipi is None:
        raise RuntimeError("Fonte gratuita encontrada, mas sem II/IPI legÃƒÂ­veis para este NCM.")

    observacoes = (
        "Fonte gratuita pÃƒÂºblica usada para alimentar a nova base local. "
        "PIS/COFINS usam regra geral quando a pÃƒÂ¡gina nÃ£o trouxer exceÃƒÂ§ÃƒÂ£o especÃƒÂ­fica; confirme exceÃƒÂ§ÃƒÂµes com contador."
    )
    return {
        "ncm": ncm_norm,
        "ncm_formatado": ncm_fmt,
        "descricao": descricao,
        "ii": ii if ii is not None else 0.0,
        "ipi": ipi if ipi is not None else 0.0,
        "pis": pis_cofins["pis"],
        "cofins": pis_cofins["cofins"],
        "fonte": "Base local atualizÃƒÂ¡vel",
        "fonte_url": url,
        "observacoes": f"{observacoes} {pis_cofins['observacao']}",
        "dados_extras": {
            "provedor": "buscadorncm.com.br",
            "fonte_ii": "Buscador NCM (pÃƒÂ¡gina pÃƒÂºblica; conferir exceÃƒÂ§ÃƒÂµes TEC/LETEC/Ex-tarifÃƒÂ¡rio)",
            "fonte_ii_url": url,
            "fonte_ipi": "Buscador NCM (substituÃƒÂ­da por TIPI oficial quando disponÃƒÂ­vel)",
            "fonte_ipi_url": url,
            "fonte_pis": pis_cofins["fonte_pis"],
            "fonte_cofins": pis_cofins["fonte_cofins"],
            "pis_cofins": pis_cofins["observacao"],
            "pis_cofins_regra": pis_cofins["regra"],
            "pis_cofins_rule_version": PIS_COFINS_IMPORTACAO_RULE_VERSION,
        },
    }


def _buscar_ipi_tipi_oficial_receita(ncm: str) -> dict | None:
    ncm_norm = _normalizar_codigo_fiscal(ncm)
    if not ncm_norm or len(ncm_norm) != 8:
        return None
    url = "https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/legislacao/documentos-e-arquivos/tipi.xlsx/@@download/file"
    try:
        resp = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0 (compatible; JKSistema/1.0)"})
        if not resp.ok or not resp.content:
            return None
        df = pd.read_excel(io.BytesIO(resp.content), sheet_name="Tabela Completa", dtype=str, header=7)
        df.columns = [str(c or "").strip() for c in df.columns]
        if "NCM" not in df.columns or "ALÃƒÂQUOTA (%)" not in df.columns:
            return None
        serie_ncm = df["NCM"].astype(str).str.replace(r"\D", "", regex=True)
        rows = df[serie_ncm.eq(ncm_norm)]
        if rows.empty:
            return None
        row = rows.iloc[0]
        aliq_raw = str(row.get("ALÃƒÂQUOTA (%)") or "").strip()
        aliq = 0.0 if aliq_raw.upper() in {"NT", "N/T"} else _normalizar_aliquota_percentual(aliq_raw)
        return {
            "ipi": aliq,
            "descricao": str(row.get("DESCRIÃƒâ€¡ÃƒÆ’O") or row.get("DESCRIÃƒâ€¡ÃƒÆ’O ") or "").strip(" -Ã¢â‚¬â€."),
            "fonte": "TIPI oficial Receita Federal Ã¢â‚¬â€ atualizada ADE RFB nÃ‚Âº 1/2026",
            "fonte_url": url,
            "raw": aliq_raw,
        }
    except Exception as e:
        logger.warning("[IMPOSTOS][TIPI OFICIAL] Falha ao consultar TIPI oficial para %s: %s", ncm_norm, e)
        return None


def _pis_cofins_importacao_por_ncm(ncm: str, descricao: str = "") -> dict:
    ncm_norm = _normalizar_codigo_fiscal(ncm)
    override = ALIQUOTAS_IMPORTACAO_OVERRIDES.get(ncm_norm)
    if isinstance(override, dict):
        return {
            "pis": _to_float(override.get("pis"), 0.0),
            "cofins": _to_float(override.get("cofins"), 0.0),
            "fonte_pis": str(override.get("fonte_pis") or "CorreÃƒÂ§ÃƒÂ£o validada"),
            "fonte_cofins": str(override.get("fonte_cofins") or "CorreÃƒÂ§ÃƒÂ£o validada"),
            "observacao": str(override.get("observacao") or "CorreÃƒÂ§ÃƒÂ£o exata validada."),
            "regra": "override_exato",
        }
    autopeca_por_ncm = (
        ncm_norm in _PIS_COFINS_AUTOPECAS_NCM_EXATOS
        or any(ncm_norm.startswith(prefixo) for prefixo in _PIS_COFINS_AUTOPECAS_PREFIXOS_SEGUROS)
    )

    if autopeca_por_ncm:
        return {
            "pis": 3.12,
            "cofins": 14.37,
            "fonte_pis": "Lei 10.865/2004 art. 8Ã‚Âº, Ã‚Â§ 9Ã‚Âº-A Ã¢â‚¬â€ autopeÃƒÂ§as",
            "fonte_cofins": "Lei 10.865/2004 art. 8Ã‚Âº, Ã‚Â§ 9Ã‚Âº-A Ã¢â‚¬â€ autopeÃƒÂ§as",
            "observacao": (
                "Regra majorada de autopeÃƒÂ§as aplicada por NCM explÃƒÂ­cito/conservador para importador nÃ£o fabricante. "
                "Confirmar descriÃƒÂ§ÃƒÂ£o/destinaÃƒÂ§ÃƒÂ£o nos Anexos I/II da Lei 10.485/2002 e eventual adicional de COFINS."
            ),
            "regra": "autopecas_3_12_14_37",
        }

    return {
        "pis": 2.1,
        "cofins": 10.25,
        "fonte_pis": "Regra geral PIS-ImportaÃƒÂ§ÃƒÂ£o",
        "fonte_cofins": "Regra geral COFINS-ImportaÃƒÂ§ÃƒÂ£o 2026 Ã¢â‚¬â€ Siscomex ImportaÃƒÂ§ÃƒÂ£o nÃ‚Âº 025/2026",
        "observacao": "Regra geral 2026; confirmar exceÃƒÂ§ÃƒÂµes legais por NCM, destinaÃƒÂ§ÃƒÂ£o e perfil do importador.",
        "regra": "geral_2_10_10_25",
    }


def _aplicar_overrides_aliquotas_importacao(registro: dict) -> dict:
    ncm_norm = _normalizar_codigo_fiscal((registro or {}).get("ncm", ""))
    override = ALIQUOTAS_IMPORTACAO_OVERRIDES.get(ncm_norm)
    if not isinstance(override, dict):
        return registro

    saida = dict(registro or {})
    for chave in ("ii", "ipi", "pis", "cofins"):
        if chave in override:
            saida[chave] = _normalizar_aliquota_percentual(override.get(chave))
    if override.get("descricao"):
        saida["descricao"] = str(override.get("descricao") or "").strip()
    obs = str(saida.get("observacoes") or "").strip()
    obs_override = str(override.get("observacao") or "").strip()
    saida["observacoes"] = f"{obs} {obs_override}".strip()
    extras = dict(saida.get("dados_extras") or {})
    for chave in ("fonte_ii", "fonte_ipi", "fonte_pis", "fonte_cofins"):
        if override.get(chave):
            extras[chave] = str(override.get(chave) or "").strip()
    extras["override_exato"] = True
    extras["override_version"] = PIS_COFINS_IMPORTACAO_RULE_VERSION
    extras["pis_cofins_rule_version"] = PIS_COFINS_IMPORTACAO_RULE_VERSION
    saida["dados_extras"] = extras
    return saida


def _obter_aliquotas_importacao_atualizavel(client_id: str, ncm: str, *, forcar_atualizacao: bool = False) -> dict:
    ncm_norm = _normalizar_codigo_fiscal(ncm)
    if not ncm_norm or len(ncm_norm) != 8:
        raise HTTPException(status_code=400, detail="Informe um NCM com 8 dÃƒÂ­gitos.")
    if not forcar_atualizacao:
        local = _buscar_aliquotas_importacao_local(client_id, ncm_norm)
        if local:
            hoje = datetime.now().strftime("%Y-%m-%d")
            atualizado_hoje = str(local.get("updated_at") or "").startswith(hoje)
            extras = local.get("dados_extras") if isinstance(local.get("dados_extras"), dict) else {}
            ipi_oficial = "TIPI oficial" in str(extras.get("fonte_ipi") or "")
            pis_cofins_atual = str(extras.get("pis_cofins_rule_version") or "") == PIS_COFINS_IMPORTACAO_RULE_VERSION
            override = ALIQUOTAS_IMPORTACAO_OVERRIDES.get(ncm_norm)
            override_atual = not override or str(extras.get("override_version") or "") == PIS_COFINS_IMPORTACAO_RULE_VERSION
            if atualizado_hoje and ipi_oficial and pis_cofins_atual and override_atual:
                local["origem"] = "base_local"
                return local
    try:
        novo = _buscar_aliquotas_buscador_ncm(ncm_norm)
        ipi_oficial = _buscar_ipi_tipi_oficial_receita(ncm_norm)
        if ipi_oficial:
            novo["ipi"] = ipi_oficial["ipi"]
            if ipi_oficial.get("descricao"):
                novo["descricao"] = novo.get("descricao") or ipi_oficial["descricao"]
            extras = dict(novo.get("dados_extras") or {})
            extras["fonte_ipi"] = ipi_oficial["fonte"]
            extras["fonte_ipi_url"] = ipi_oficial["fonte_url"]
            extras["tipi_raw"] = ipi_oficial.get("raw", "")
            novo["dados_extras"] = extras
        novo = _aplicar_overrides_aliquotas_importacao(novo)
        salvo = _salvar_aliquotas_importacao_local(client_id, novo)
        registro = _row_aliquota_importacao_para_dict(salvo) or novo
        registro["origem"] = "fonte_gratuita_atualizada"
        return registro
    except Exception as e:
        logger.warning("[IMPOSTOS][ALIQUOTAS IMPORTAÃƒâ€¡ÃƒÆ’O] Falha ao atualizar NCM %s: %s", ncm_norm, e)
        local = _buscar_aliquotas_importacao_local(client_id, ncm_norm)
        if local:
            local["origem"] = "base_local_fallback"
            local["aviso_atualizacao"] = str(e)
            return local
        raise HTTPException(status_code=502, detail=f"NÃƒÂ£o foi possÃƒÂ­vel obter alÃƒÂ­quotas gratuitas para o NCM {ncm_norm}: {e}")


def _aliquotas_importacao_para_cards(registro: dict) -> dict:
    fonte = str((registro or {}).get("fonte") or "Base local de alÃƒÂ­quotas de importaÃƒÂ§ÃƒÂ£o")
    fonte_url = str((registro or {}).get("fonte_url") or "")
    extras = (registro or {}).get("dados_extras") if isinstance((registro or {}).get("dados_extras"), dict) else {}
    def _item(chave: str) -> dict:
        fonte_chave = str(extras.get(f"fonte_{chave}") or fonte)
        fonte_url_chave = str(extras.get(f"fonte_{chave}_url") or fonte_url)
        return {
            "percentual": _to_float((registro or {}).get(chave), 0.0),
            "fonte": fonte_chave,
            "fonte_url": fonte_url_chave,
            "origem": str((registro or {}).get("origem") or "base_local"),
            "erro": None,
        }
    return {
        "ii": _item("ii"),
        "ipi": _item("ipi"),
        "pis": _item("pis"),
        "cofins": _item("cofins"),
    }


def _arquivo_impostos_monofasico(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "impostos_monofasico.json")


def _carregar_regras_monofasico(client_id: str) -> dict:
    """Carrega regras de monofÃƒÂ¡sico por NCM (exato/prefixo) para o tenant."""
    arquivo = _arquivo_impostos_monofasico(client_id)
    padrao = {
        "ncm_exatos": [],
        "ncm_prefixos": [],
        "ncm_excluir_exatos": [],
        "ncm_excluir_prefixos": [],
    }
    if not os.path.exists(arquivo):
        return padrao
    try:
        with open(arquivo, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return padrao
    if not isinstance(data, dict):
        return padrao

    def _norm_lista(chave: str) -> list[str]:
        lista = data.get(chave, [])
        if not isinstance(lista, list):
            return []
        out: list[str] = []
        for item in lista:
            n = _normalizar_codigo_fiscal(item)
            if n:
                out.append(n)
        return out

    return {
        "ncm_exatos": _norm_lista("ncm_exatos"),
        "ncm_prefixos": _norm_lista("ncm_prefixos"),
        "ncm_excluir_exatos": _norm_lista("ncm_excluir_exatos"),
        "ncm_excluir_prefixos": _norm_lista("ncm_excluir_prefixos"),
    }


def _avaliar_monofasico_ncm(client_id: str, ncm: str, ttce_result: dict | None = None) -> dict:
    """Classifica monofÃƒÂ¡sico em 3 camadas de prioridade:
      1. TTCE Ã¢â‚¬â€ regime real retornado pela API (mais preciso)
      2. Regras locais do tenant (ncm_exatos / ncm_prefixos / ncm_excluir_*)
      3. Base legislativa embutida (_MONOFASICO_PREFIXOS_LEGIS)
    Retorna is_monofasico=True/False/None e detalhes de fonte/motivo."""
    n = _normalizar_codigo_fiscal(ncm)
    if not n:
        return {
            "is_monofasico": None,
            "status": "indeterminado",
            "fonte": "Ã¢â‚¬â€",
            "motivo": "NCM invalido para classificaÃƒÂ§ÃƒÂ£o.",
        }

    # -----------------------------------------------------------------------
    # CAMADA 1 Ã¢â‚¬â€ TTCE: regime real da API Siscomex
    # tratamentosTributarios[].regime.nome pode conter "MONOFASICO"
    # -----------------------------------------------------------------------
    if isinstance(ttce_result, dict) and not ttce_result.get("sem_credenciais") and not ttce_result.get("sem_pais"):
        tratamentos = ttce_result.get("tratamentosTributarios") or []
        if isinstance(tratamentos, list) and tratamentos:
            nomes_regime = [
                str((t.get("regime") or {}).get("nome") or "").upper()
                for t in tratamentos if isinstance(t, dict)
            ]
            tem_monofasico = any("MONOFASICO" in r or "MONOFÃƒÂSICO" in r for r in nomes_regime)
            nao_monofasico = all(
                ("MONOFASICO" not in r and "MONOFÃƒÂSICO" not in r)
                for r in nomes_regime
            ) if nomes_regime else False
            if tem_monofasico:
                return {
                    "is_monofasico": True,
                    "status": "sim",
                    "fonte": "TTCE Ã¢â‚¬â€ Portal ÃƒÅ¡nico Siscomex",
                    "motivo": "Regime monofÃƒÂ¡sico identificado na resposta da API TTCE.",
                }
            if nao_monofasico:
                return {
                    "is_monofasico": False,
                    "status": "nao",
                    "fonte": "TTCE Ã¢â‚¬â€ Portal ÃƒÅ¡nico Siscomex",
                    "motivo": "Nenhum regime monofÃƒÂ¡sico na resposta da API TTCE.",
                }

    # -----------------------------------------------------------------------
    # CAMADA 2 Ã¢â‚¬â€ Regras locais do tenant (maior prioridade que legislaÃƒÂ§ÃƒÂ£o
    # embutida, pois o usuÃƒÂ¡rio pode customizar exceÃƒÂ§ÃƒÂµes)
    # -----------------------------------------------------------------------
    regras = _carregar_regras_monofasico(client_id)
    exatos = set(regras.get("ncm_exatos") or [])
    prefixos = list(regras.get("ncm_prefixos") or [])
    excluir_exatos = set(regras.get("ncm_excluir_exatos") or [])
    excluir_prefixos = list(regras.get("ncm_excluir_prefixos") or [])

    if n in excluir_exatos or any(n.startswith(p) for p in excluir_prefixos):
        return {
            "is_monofasico": False,
            "status": "nao",
            "fonte": "ConfiguraÃƒÂ§ÃƒÂ£o local",
            "motivo": "NCM excluÃƒÂ­do da lista monofÃƒÂ¡sico na configuraÃƒÂ§ÃƒÂ£o do sistema.",
        }

    if n in exatos:
        return {
            "is_monofasico": True,
            "status": "sim",
            "fonte": "ConfiguraÃƒÂ§ÃƒÂ£o local",
            "motivo": "NCM classificado como monofÃƒÂ¡sico na configuraÃƒÂ§ÃƒÂ£o do sistema.",
        }

    for pref in prefixos:
        if n.startswith(pref):
            return {
                "is_monofasico": True,
                "status": "sim",
                "fonte": "ConfiguraÃƒÂ§ÃƒÂ£o local",
                "motivo": f"Prefixo NCM {pref} classificado como monofÃƒÂ¡sico na configuraÃƒÂ§ÃƒÂ£o.",
            }

    # -----------------------------------------------------------------------
    # CAMADA 3 Ã¢â‚¬â€ Base legislativa embutida (prefixo de 4 dÃƒÂ­gitos)
    # -----------------------------------------------------------------------
    pref4 = n[:4]
    if pref4 in _MONOFASICO_PREFIXOS_LEGIS:
        leg, categoria = _MONOFASICO_PREFIXOS_LEGIS[pref4]
        return {
            "is_monofasico": True,
            "status": "sim",
            "fonte": f"LegislaÃƒÂ§ÃƒÂ£o ({leg})",
            "motivo": f"{categoria} Ã¢â‚¬â€ NCM {_formatar_ncm_pontos(n)} coberto por {leg}.",
        }

    # CapÃƒÂ­tulo 87 inteiro NÃƒO ÃƒÂ© monofÃƒÂ¡sico alÃƒÂ©m dos prefixos mapeados acima
    # (ex: 8716 Ã¢â‚¬â€ reboques, 8709 Ã¢â‚¬â€ empilhadeiras)
    if n[:2] == "87":
        return {
            "is_monofasico": False,
            "status": "nao",
            "fonte": "LegislaÃƒÂ§ÃƒÂ£o (Lei 10.485/2002)",
            "motivo": "NCM do capÃƒÂ­tulo 87 nÃ£o listado no Anexo da Lei 10.485/2002.",
        }

    return {
        "is_monofasico": None,
        "status": "indeterminado",
        "fonte": "Ã¢â‚¬â€",
        "motivo": "NCM nÃ£o coberto por legislaÃƒÂ§ÃƒÂ£o monofÃƒÂ¡sica mapeada. Verifique com o contador.",
    }


async def consultar_aliquotas_completas(req: SiscomexAliquotasRequest, request: Request, client_id: str = Depends(get_tenant_id)):
    """Consulta alÃƒÂ­quotas em base local atualizÃƒÂ¡vel e usa TTCE como validaÃƒÂ§ÃƒÂ£o oficial."""
    loja_vinculada = str(req.loja_vinculada or "").strip()
    perfil_usuario = _normalizar_perfil_siscomex(req.perfil_usuario or "") or _extrair_username_do_request(request)

    ncm = _normalizar_codigo_fiscal(req.ncm or "")
    if not ncm or len(ncm) != 8:
        raise HTTPException(status_code=400, detail="Informe um NCM com 8 dÃƒÂ­gitos.")

    cfg = _carregar_config_siscomex(
        client_id,
        loja_vinculada=loja_vinculada,
        perfil_usuario=perfil_usuario,
        incluir_secret=True,
    )
    regime = str(cfg.get("regime_tributario") or "simples").strip().lower()

    base_local = _obter_aliquotas_importacao_atualizavel(client_id, ncm)
    extras_base = dict(base_local.get("dados_extras") or {})
    if str(cfg.get("client_id") or "").strip() and str(cfg.get("client_secret") or "").strip():
        try:
            auth_pre = _siscomex_autenticar(cfg)
            host = _siscomex_host(cfg.get("ambiente"))
            tec_oficial = _siscomex_get_tec(
                host,
                ncm,
                _siscomex_build_get_header_variants(cfg, auth_pre["set_token"], auth_pre["csrf_token"]),
            )
            if isinstance(tec_oficial, dict) and not tec_oficial.get("erro") and tec_oficial.get("aliquota_ii") is not None:
                base_local["ii"] = _normalizar_aliquota_percentual(tec_oficial.get("aliquota_ii"))
                extras_base["fonte_ii"] = "TEC/Siscomex oficial autenticado"
                extras_base["fonte_ii_url"] = f"https://{host}/tec/api/ext/nomenclatura/ncm/{_formatar_ncm_pontos(ncm)}"
                base_local["dados_extras"] = extras_base
                try:
                    _salvar_aliquotas_importacao_local(client_id, base_local)
                except Exception:
                    logger.warning("[IMPOSTOS][TEC OFICIAL] NÃƒÂ£o foi possÃƒÂ­vel persistir II oficial para %s", ncm)
        except Exception as e:
            logger.warning("[IMPOSTOS][TEC OFICIAL] Falha ao consultar TEC oficial para %s: %s", ncm, e)
    payload_ttce = _payload_ttce_importacao_china(ncm)
    ttce_result: dict = {}
    ttce_erro = ""
    if str(cfg.get("client_id") or "").strip() and str(cfg.get("client_secret") or "").strip():
        try:
            resultado_ttce = _siscomex_post_ttce(cfg, payload_ttce)
            ttce_result = resultado_ttce.get("payload") or {}
        except HTTPException as e:
            ttce_erro = str(e.detail or e)
        except Exception as e:
            ttce_erro = str(e)
    else:
        ttce_erro = "Credenciais Siscomex nÃ£o configuradas; alÃƒÂ­quotas retornadas pela base local, sem validaÃƒÂ§ÃƒÂ£o TTCE."
    aliquotas = _aliquotas_importacao_para_cards(base_local)

    monofasico_info = _avaliar_monofasico_ncm(client_id, ncm, ttce_result=ttce_result)

    # PIS/COFINS na importaÃƒÂ§ÃƒÂ£o sÃƒÂ£o sempre devidos (2,1% / 9,65%), independente do regime monofÃƒÂ¡sico.
    # O regime monofÃƒÂ¡sico zera PIS/COFINS apenas na revenda no mercado interno, nÃ£o na importaÃƒÂ§ÃƒÂ£o.
    monofasico_info = {
        **monofasico_info,
        "posicao_cadeia": "revendedor",
        "pis_cofins_importacao_tributada": True,
        "pis_cofins_revenda_zero": False,
    }

    return {
        "success": True,
        "ncm": _formatar_ncm_pontos(ncm),
        "escopo": {
            "loja_vinculada": _normalizar_loja_siscomex(loja_vinculada),
            "perfil_usuario": _normalizar_perfil_siscomex(perfil_usuario),
            "regime_tributario": regime,
            "posicao_cadeia": "revendedor",
            "modo_fonte": "base_local_atualizavel_com_ttce",
            "codigo_pais": SISCOMEX_CODIGO_PAIS_CHINA,
            "pais": "China",
            "tipo_operacao": SISCOMEX_TIPO_OPERACAO_IMPORTACAO,
            "data_fato_gerador": payload_ttce["dataFatoGerador"],
        },
        "aliquotas": aliquotas,
        "base_local": base_local,
        "consulta": payload_ttce,
        "tec": {"fonte": "Base local atualizÃƒÂ¡vel", "percentual": aliquotas["ii"]["percentual"]},
        "tipi": {"fonte": "Base local atualizÃƒÂ¡vel", "percentual": aliquotas["ipi"]["percentual"]},
        "ttce": {
            "retorno": ttce_result,
            "resumo": _resumir_resposta_ttce(ttce_result) if isinstance(ttce_result, dict) else {},
            "erro": ttce_erro,
            "validado": bool(ttce_result) and not ttce_erro,
            "sem_credenciais": "Credenciais Siscomex nÃ£o configuradas" in ttce_erro,
            "sem_pais": False,
        },
        "monofasico": monofasico_info,
    }


async def atualizar_aliquotas_importacao_ncm(ncm: str, client_id: str = Depends(get_tenant_id)):
    registro = _obter_aliquotas_importacao_atualizavel(client_id, ncm, forcar_atualizacao=True)
    return {"success": True, "registro": registro, "aliquotas": _aliquotas_importacao_para_cards(registro)}


async def obter_aliquotas_importacao_ncm(ncm: str, client_id: str = Depends(get_tenant_id)):
    registro = _buscar_aliquotas_importacao_local(client_id, ncm)
    if not registro:
        raise HTTPException(status_code=404, detail="NCM ainda nÃ£o existe na nova base local de alÃƒÂ­quotas.")
    registro["origem"] = "base_local"
    return {"success": True, "registro": registro, "aliquotas": _aliquotas_importacao_para_cards(registro)}


configure_impostos_aliquotas_runtime()

__all__ = [
    name
    for name in globals()
    if (
        (name.startswith("_") and not name.startswith("__"))
        or name.endswith("_impostos")
        or name.startswith("consultar_")
        or name.startswith("obter_")
        or name.startswith("salvar_")
        or name.startswith("testar_")
        or name.startswith("atualizar_")
        or name.startswith("importar_")
        or name.startswith("listar_")
        or name.startswith("simular_")
        or name.startswith("aplicar_")
        or name.startswith("calcular_")
        or name.startswith("configure_")
    )
]
