"""Confirmed monthly sales projections. Only the close worker writes to a sales DB.

Month receipts and their invalidation triggers live together so rollback, restore,
concurrent sales edits and process restarts cannot separate data from its receipt.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
import zlib

from backend.services.sqlite_coordination import sqlite_lock_for_path


_FORMATO = 2
_TABELA = "medias_fechamento_mensal_v2"
_ALIASES = "medias_fechamento_alias_v2"
_MES = "strftime('%Y-%m',date(data))"
_MAX_BYTES = 32 * 1024 * 1024


def raiz_tenant(client_id):
    from backend.services.medias_compras_common import get_tenant_path
    return Path(get_tenant_path(client_id)).resolve()


def _fontes(client_id, loja, store_id, db_paths):
    from backend.services.bling_vendas import _sql_filtro_loja_vendas
    if not client_id or not store_id or not loja or loja == "__todas" or not db_paths:
        return None
    root = Path(raiz_tenant(client_id)).resolve()
    paths = tuple(Path(p).absolute() for p in db_paths)
    if any(p.resolve().parent != root or p.parent != root or p.is_symlink() or p.resolve() != p
           or p.suffix.lower() != ".db" or not p.is_file() for p in paths):
        return None
    filtro, params = _sql_filtro_loja_vendas(loja)
    if not filtro:
        return None
    key = hashlib.sha256(json.dumps([_FORMATO, os.path.normcase(str(root)), client_id,
        store_id, loja, filtro, params], ensure_ascii=True).encode()).hexdigest()
    return root, paths, key, filtro, tuple(params)


def _mes_atual():
    from backend.modules.vendas.sync_queue import SYNC_TIMEZONE
    return datetime.now(SYNC_TIMEZONE).strftime("%Y-%m")


def _autorizados(root, client_id, store_id, meses):
    from backend.modules.vendas.monthly_coverage import meses_confirmados
    provas = meses_confirmados(root, client_id, store_id)
    atual = _mes_atual()
    return {mes: verso for mes, verso in meses.items()
            if isinstance(mes, str) and re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", mes)
            and mes < atual and verso and provas.get((store_id, mes)) == verso}


def provas_para_leitura(client_id, store_id, primeiro_mes_aberto):
    from backend.modules.vendas.monthly_coverage import meses_confirmados
    if not store_id:
        return {}
    try:
        provas = meses_confirmados(raiz_tenant(client_id), client_id, store_id)
        atual = _mes_atual()
        return {m: v for (store, m), v in provas.items() if store == store_id and m < min(atual, primeiro_mes_aberto)}
    except (OSError, RuntimeError, sqlite3.Error):
        return {}


def _invalidar(ref):
    # The aliases reproduce both branches of the existing store SQL filter.
    valor = f"trim(coalesce({ref}.loja_conta,''))"
    return (f"DELETE FROM {_TABELA} WHERE mes=strftime('%Y-%m',date({ref}.data)) "
            f"AND escopo IN (SELECT escopo FROM {_ALIASES} WHERE "
            f"(modo=0 AND alias=lower({valor})) OR (modo=1 AND alias={valor} COLLATE NOCASE));")


def _criar_estruturas(conn):
    table = conn.execute("SELECT type,wr FROM pragma_table_list WHERE schema='main' AND name='vendas'").fetchone()
    if table != ("table", 0):
        return False
    columns = {c[1].lower() for c in conn.execute("PRAGMA table_info(vendas)")}
    if "rowid" in columns or not {"id_unico","sku","data","produto","quantidade","loja_conta"} <= columns:
        return False
    # Unknown expression indexes, alternative conflict targets and collations
    # must never leave partial triggers installed on a legacy sales database.
    unique_id = False
    for idx in conn.execute("PRAGMA index_list(vendas)"):
        if idx[2]:
            fields = tuple((c[2], c[4]) for c in conn.execute("SELECT * FROM pragma_index_xinfo(?) ORDER BY seqno", (idx[1],)) if c[5])
            if fields != (("id_unico", "BINARY"),):
                return False
            unique_id = True
    conn.execute(f"CREATE TABLE IF NOT EXISTS {_TABELA} (escopo TEXT NOT NULL, mes TEXT NOT NULL, "
                 "prova TEXT NOT NULL, token TEXT NOT NULL, esquema INTEGER NOT NULL, dados BLOB NOT NULL, "
                 "metadados BLOB NOT NULL, PRIMARY KEY(escopo,mes))")
    conn.execute(f"CREATE TABLE IF NOT EXISTS {_ALIASES} (escopo TEXT NOT NULL, modo INTEGER NOT NULL, "
                 "alias TEXT NOT NULL, PRIMARY KEY(escopo,modo,alias))")
    for event, body in (("INSERT", _invalidar("NEW")), ("UPDATE", _invalidar("OLD") + _invalidar("NEW")),
                        ("DELETE", _invalidar("OLD"))):
        when = ""
        if event == "UPDATE":
            cols = ("id_unico", "sku", "data", "produto", "quantidade", "loja_conta", "rowid")
            when = "WHEN " + " OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c in cols)
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS medias_fechamento_{event}_v2 AFTER {event} ON vendas "
                     f"{when} BEGIN {body} END")
    # INSERT/UPDATE OR REPLACE may discard a *different* row without firing
    # its DELETE trigger, including a row in another month or store.
    conflito = "rowid=NEW.rowid" + (" OR id_unico COLLATE BINARY=NEW.id_unico" if unique_id else "")
    for event in ("INSERT", "UPDATE"):
        check = "EXISTS(SELECT 1 FROM " + _TABELA + ")"
        if event == "UPDATE":
            check += " AND (NEW.rowid IS NOT OLD.rowid OR NEW.id_unico IS NOT OLD.id_unico)"
        nome = f"medias_fechamento_replace_{event}_v2"
        definicao = f"""CREATE TRIGGER {nome} BEFORE {event} ON vendas
            WHEN {check}
            BEGIN DELETE FROM {_TABELA} WHERE (escopo,mes) IN (
                SELECT a.escopo,strftime('%Y-%m',date(v.data)) FROM
                (SELECT data,loja_conta FROM vendas WHERE {conflito}) v JOIN {_ALIASES} a
                ON (a.modo=0 AND a.alias=lower(trim(coalesce(v.loja_conta,''))))
                OR (a.modo=1 AND a.alias=trim(coalesce(v.loja_conta,'')) COLLATE NOCASE)); END"""
        existente = conn.execute("SELECT sql FROM sqlite_schema WHERE type='trigger' AND name=?", (nome,)).fetchone()
        if existente is None or existente[0] != definicao:
            conn.execute(f"DROP TRIGGER IF EXISTS {nome}")
            conn.execute(definicao)
    return True


def _novo_resumo():
    return {"totais": {}, "datas": {}, "titulos": {}, "ids": set(), "seguro": True, "meta_segura": True, "magnitudes": {}, "fracionados": set()}


def _acumular(out, row, ordem, basename):
    identificador, sku, raw_date, produto, dia, mes, quantidade = row
    original = str(sku or "").strip()
    if not original:
        return
    if not isinstance(raw_date, str) or not isinstance(produto, str):
        out["seguro"] = False
        out["meta_segura"] = False
        return
    try:
        qty = float(quantidade or 0)
    except (ValueError, TypeError, OverflowError):
        qty = float("nan")
    if not math.isfinite(qty):
        out["seguro"] = False
    chave = str(identificador or "").strip() or f"{basename}::{original}::{mes}::{qty}"
    if math.isfinite(qty) and chave not in out["ids"]:
        out["ids"].add(chave)
        total = out["totais"].get(original, 0.0) + qty
        magnitude = out["magnitudes"].get(original, 0.0) + abs(qty)
        if not math.isfinite(total) or not math.isfinite(magnitude):
            out["seguro"] = False
        else:
            out["totais"][original] = total
            out["magnitudes"][original] = magnitude
        if not qty.is_integer():
            out["fracionados"].add(original)
    datas = out["datas"].setdefault(original, ["", ""])
    if math.isfinite(qty) and qty > 0 and (not datas[0] or dia < datas[0]):
        datas[0] = dia
    if dia > datas[1]:
        datas[1] = dia
    key = (original, produto)
    ant = out["titulos"].get(key)
    texto = raw_date.strip()
    if ant is None or texto > ant[0] or (texto == ant[0] and (dia, raw_date) <= (ant[1], ant[2])):
        out["titulos"][key] = (texto, dia, raw_date, ordem)


def _reservar(conn, key, loja, meses):
    from backend.services.bling_vendas import _variacoes_nome_loja, _variacoes_nome_loja_nocase
    if not _criar_estruturas(conn):
        return None
    schema = conn.execute("PRAGMA schema_version").fetchone()[0]
    registros = {m: (v, token, pronto) for m, v, token, pronto in conn.execute(
        f"SELECT mes,prova,token,length(dados)>0 FROM {_TABELA} WHERE escopo=? AND esquema=?", (key, schema))}
    aliases = [(key, modo, alias) for modo, valores in enumerate(
        (_variacoes_nome_loja(loja), _variacoes_nome_loja_nocase(loja))) for alias in valores]
    conn.executemany(f"INSERT OR IGNORE INTO {_ALIASES} VALUES(?,?,?)", aliases)
    faltam = {}
    for mes, prova in meses.items():
        antigo = registros.get(mes)
        if antigo and antigo[2] and antigo[0] != prova:
            # Coverage can advance after a new daily check with identical sales.
            # The data and mutation triggers certify exactly this month and store.
            conn.execute(f"UPDATE {_TABELA} SET prova=? WHERE escopo=? AND mes=?", (prova, key, mes))
            continue
        if antigo and antigo[0] == prova:
            if not antigo[2]:
                faltam[mes] = antigo[1]
            continue
        token = uuid.uuid4().hex
        faltam[mes] = token
        conn.execute(f"INSERT OR REPLACE INTO {_TABELA} VALUES(?,?,?,?,?,X'',X'')", (key, mes, prova, token, schema))
    return faltam


def _resumir(conn, path, filtro, params, meses):
    from backend.services.medias_compras_historico import _QUERY_PROJECAO
    resumos = {m: _novo_resumo() for m in meses}
    for ordem, row in enumerate(conn.execute(_QUERY_PROJECAO + filtro, params)):
        if row[5] in resumos:
            _acumular(resumos[row[5]], row, ordem, path.name)
    blobs = {}
    for mes, resumo in resumos.items():
        resumo["fracionados"] = sorted(resumo["fracionados"])
        resumo["ids"] = sorted(resumo["ids"])
        resumo["titulos"] = [(*k, *v) for k, v in resumo["titulos"].items()]
        meta = {k: resumo.pop(k) for k in ("datas", "titulos", "meta_segura")}
        blobs[mes] = (_empacotar(resumo, "seguro"), _empacotar(meta, "meta_segura"))
    return blobs


def _empacotar(valores, flag):
    bruto = json.dumps(valores if valores[flag] else {flag: False}, ensure_ascii=True,
                      allow_nan=False, separators=(",", ":")).encode()
    if len(bruto) > _MAX_BYTES:
        bruto = json.dumps({flag: False}).encode()
    comprimido = zlib.compress(bruto, 1)
    return hashlib.sha256(comprimido).digest() + comprimido


def _desempacotar(blob):
    if len(blob) < 32 or hashlib.sha256(blob[32:]).digest() != blob[:32]:
        raise ValueError("Fechamento mensal inconsistente")
    decoder = zlib.decompressobj()
    bruto = decoder.decompress(blob[32:], _MAX_BYTES + 1)
    if len(bruto) > _MAX_BYTES or not decoder.eof:
        raise ValueError("Fechamento mensal incompleto")
    return json.loads(bruto)


def _preparar_banco(path, key, loja, filtro, params, meses):
    lock = sqlite_lock_for_path(str(path))
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=1)) as conn:
        with lock, conn:
            conn.execute("BEGIN IMMEDIATE")
            faltam = _reservar(conn, key, loja, meses)
            if faltam is None:
                conn.rollback()
                return False
        if not faltam:
            return True

        # A stable SQLite read snapshot is independent of the writer lock.
        # In rollback-journal mode materialize a temporary online backup in
        # small SQLite steps, so Python aggregation cannot hold a reader lock
        # that prevents sales writers from committing.
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal":
            blobs = _resumir(conn, path, filtro, params, faltam)
        else:
            with closing(sqlite3.connect("")) as copia:
                limite = time.monotonic() + 20
                def verificar_prazo(status, restantes, total):
                    if restantes and time.monotonic() > limite:
                        raise TimeoutError("Cópia de vendas ocupada")
                conn.backup(copia, pages=128, sleep=0.02, progress=verificar_prazo)
                blobs = _resumir(copia, path, filtro, params, faltam)

        # Reservation tokens live in the sales DB. Mutations that affect a
        # reserved month invalidate its token in their own transaction. A
        # second process can finish the same token safely and idempotently.
        completos = True
        with lock, conn:
            conn.execute("BEGIN IMMEDIATE")
            schema = conn.execute("PRAGMA schema_version").fetchone()[0]
            for mes, (dados, meta) in blobs.items():
                mudou = conn.execute(f"UPDATE {_TABELA} SET dados=?,metadados=? WHERE escopo=? AND mes=? AND token=? "
                                     "AND esquema=? AND length(dados)=0", (dados, meta, key, mes, faltam[mes], schema))
                if not mudou.rowcount:
                    pronto = conn.execute(f"SELECT 1 FROM {_TABELA} WHERE escopo=? AND mes=? AND token=? "
                                          "AND esquema=? AND length(dados)>0", (key, mes, faltam[mes], schema))
                    if pronto.fetchone() is None:
                        completos = False
        return completos


def preparar_fechamentos(*, client_id, loja, store_id, meses, db_paths):
    try:
        fonte = _fontes(client_id, loja, store_id, db_paths)
        if not fonte:
            return False
        root, paths, key, filtro, params = fonte
        provas = _autorizados(root, client_id, store_id, meses)
        if not provas:
            return False
        for path in paths:
            if not _preparar_banco(path, key, loja, filtro, params, provas):
                return False
        return provas == meses and _autorizados(root, client_id, store_id, provas) == provas
    except (OSError, sqlite3.Error, ValueError, RuntimeError, OverflowError):
        return False


def _recibos(conn, key):
    schema = conn.execute("PRAGMA schema_version").fetchone()[0]
    return {m: (v, t) for m, v, t in conn.execute(
        f"SELECT mes,prova,token FROM {_TABELA} WHERE escopo=? AND esquema=? AND length(dados)>0", (key, schema))}


def inventario(client_id, loja, store_id, db_paths, meses, *, filtro=None, params=None, provas_checadas=False):
    try:
        fonte = _fontes(client_id, loja, store_id, db_paths)
        if not fonte:
            return "", {}
        root, paths, key, sql, args = fonte
        if filtro is not None and (sql != filtro or args != tuple(params)):
            return "", {}
        validos = meses if provas_checadas else _autorizados(root, client_id, store_id, meses)
        arquivos = {}
        for path in paths:
            if not validos:
                return "", {}
            with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True)) as conn:
                recibos = _recibos(conn, key)
            validos = {m: v for m, v in validos.items() if m in recibos and recibos[m][0] == v}
            arquivos[str(path)] = recibos
        return key, {p: {m: recibos[m][1] for m in validos} for p, recibos in arquivos.items()} if validos else {}
    except (OSError, sqlite3.Error, RuntimeError, ValueError):
        return "", {}


def fechamentos_disponiveis(*, client_id, loja, store_id, meses, db_paths):
    _, arquivos = inventario(client_id, loja, store_id, db_paths, meses)
    return set(next(iter(arquivos.values()), ()))


def tokens_validos(conn, key, tokens):
    recibos = _recibos(conn, key)
    return all(m in recibos and recibos[m][1] == t for m, t in tokens.items())


def resumos_confirmados(conn, key, tokens, inicio):
    if not tokens_validos(conn, key, tokens):
        return None
    # Older months supply first/last sale and title only. Do not transfer,
    # decompress or retain their transaction identifiers or quantities.
    placeholders = ",".join("?" for _ in tokens)
    cursor = conn.execute(f"SELECT mes,metadados,CASE WHEN mes>=? THEN dados END FROM {_TABELA} "
                          f"WHERE escopo=? AND mes IN ({placeholders})", (inicio, key, *tokens))
    return {mes: {**_desempacotar(meta), **(_desempacotar(vendas) if vendas else {})}
            for mes, meta, vendas in cursor}


def consulta_abertos(sql, tokens):
    return sql + f" AND coalesce({_MES},'') NOT IN ({','.join('?' for _ in tokens)})"
