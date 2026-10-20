"""Cheap filesystem revision fences for Medias. No credentials are read/logged."""

from datetime import datetime
from collections import Counter
from pathlib import Path
import os
import stat

from backend.services.medias_compras_cache import Fontes, digest_json


def _categoria_diagnostico(path):
    """Return only a fixed technical category, never any part of the path."""
    parts = str(path).replace("\\", "/").casefold().split("/")
    name = parts[-1]
    if "cadastro_fotos" in parts:
        return "fotos"
    if name.startswith("vendas_historico"):
        return "vendas_wal" if name.endswith("-wal") else "vendas_journal" if name.endswith("-journal") else "vendas_db"
    if name.startswith("cadastro_produtos"):
        return "cadastro"
    if name.startswith("cadastro_custos"):
        return "custos"
    return {
        "produtos_compilado.csv": "estoque", "listas_pedidos.json": "listas",
        "lojas_config.json": "lojas_config", "lojas_config.json.bak": "lojas_config",
        "lojas_public_snapshot.json": "lojas_snapshot", "lojas_sync_tombstones.json": "lojas_tombstones",
        "cadastro_fotos_config.json": "fotos_politica", ".cadastro-fotos-migracao-v1.json": "fotos_migracao",
        ".cadastro_tenant_trust.json": "tenant_autoridade",
        "transaction.json": "lojas_transacao", "pending_lojas_transaction.json": "lojas_transacao",
        "impostos_ncm.db": "fiscal", "impostos_ncm.db-wal": "fiscal",
        "impostos_ncm.db-journal": "fiscal", "ncm.xlsx": "fiscal", "ncm1.xlsx": "fiscal",
    }.get(name, "outros")


def categorias_alteradas(antes, depois):
    """Count changed fences without reading sources or exposing their identity."""
    if antes.assinatura == depois.assinatura:
        return {}
    def inventory(signature):
        if len(signature) != 2 or not isinstance(signature[1], (tuple, list)):
            return None
        rows = signature[1]
        if any(not isinstance(row, (tuple, list)) or not row or not isinstance(row[0], str) for row in rows):
            return None
        return {row[0]: row for row in rows}
    old, new = inventory(antes.assinatura), inventory(depois.assinatura)
    if old is None or new is None:
        return {"outros": 1}
    changes = Counter(
        _categoria_diagnostico(path) for path in old.keys() | new.keys()
        if old.get(path) != new.get(path)
    )
    if antes.assinatura[0] != depois.assinatura[0]:
        changes["calendario"] += 1
    return dict(sorted(changes.items()))


def assinatura_arquivo(path, entry=None):
    path = Path(path)
    try:
        # Windows directory enumeration can retain an old mtime (and ino=0)
        # until that directory is opened. Read its own metadata so a read-only
        # scan does not manufacture a revision change on the next fence.
        entry = path.lstat() if entry is None or stat.S_ISDIR(entry.st_mode) else entry
        linked = stat.S_ISLNK(entry.st_mode) or bool(getattr(entry, "st_file_attributes", 0) & 0x400)
        target = path.stat() if linked else entry
        # A read-only WAL connection can create/remove an empty sidecar. It
        # contains no frames and changes no rows; do not invalidate a result
        # just because another reader opens or closes the database. Non-empty
        # WALs retain the full identity fence, including same-size rewrites.
        if path.name.casefold().endswith(".db-wal") and target.st_size == 0:
            return (os.path.normcase(os.path.abspath(path)), None)
        resolved = os.path.normcase(os.path.realpath(path) if linked else os.path.abspath(path))
        return (os.path.normcase(os.path.abspath(path)), entry.st_ctime_ns, entry.st_mtime_ns, entry.st_size,
                target.st_ctime_ns, target.st_mtime_ns, target.st_size, entry.st_ino, resolved)
    except FileNotFoundError:
        return (os.path.normcase(os.path.abspath(path)), None)


def capturar_fontes(tenant, *, detalhe=False, extras=(), alvo="", snapshot=None, escopo=()):
    root = Path(tenant)
    paths = set()
    authority = set()
    cached_stats = {}
    fixed = {
        "cadastro_produtos.csv", "cadastro_produtos_lojas.csv", "cadastro_custos_lojas.csv",
        "produtos_compilado.csv", "impostos_ncm.db", "lojas_config.json", "lojas_config.json.bak",
        "lojas_public_snapshot.json", "lojas_sync_tombstones.json", "cadastro_fotos_config.json",
        ".cadastro-fotos-migracao-v1.json",
    }
    if not detalhe:
        fixed.add("listas_pedidos.json")
    paths.update(root / name for name in fixed)
    # Inventory only files actually consulted. Sync progress and unrelated
    # settings must not invalidate a result or exhaust its consistency retry.
    with os.scandir(root) as entries:
        for entry in entries:
            name = entry.name.casefold()
            if ((name.startswith("cadastro_produtos") and name.endswith(".csv")) or
                    (not detalhe and name.startswith("vendas_historico") and ".backup_" not in name and name.endswith(".db"))):
                path = Path(entry.path)
                paths.add(path)
                cached_stats[path] = entry.stat(follow_symlinks=False)
    for name in ("lojas_public_snapshot.json", "lojas_sync_tombstones.json", "cadastro_fotos_config.json", ".cadastro-fotos-migracao-v1.json"):
        authority.add(root / name)
    if not (root / "lojas_public_snapshot.json").exists():
        # Old runtimes without a public projection retain the strict fallback.
        authority.add(root / "lojas_config.json")
    authority.add(root.parent / ".cadastro_tenant_trust.json")
    authority.update(Path(path) for path in extras if path and Path(path).name == ".cadastro_tenant_trust.json")
    photo_dirs = [root / "cadastro_fotos"]
    photo_dirs.extend(Path(path) for path in extras if path and Path(path).name == "cadastro_fotos")
    for directory in photo_dirs:
        paths.add(directory)
        stack = [directory] if directory.is_dir() and not directory.is_symlink() else []
        while stack:
            parent = stack.pop()
            try:
                with os.scandir(parent) as entries:
                    for entry in entries:
                        path = Path(entry.path)
                        info = entry.stat(follow_symlinks=False)
                        paths.add(path)
                        cached_stats[path] = info
                        linked = stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)
                        if stat.S_ISDIR(info.st_mode) and not linked:
                            stack.append(path)
            except FileNotFoundError:
                continue  # the directory signature records the concurrent removal
    journals = (
        root / "_stores_publication" / "transaction.json",
        root / "_shared_sync_backups" / "pending_lojas_transaction.json",
        root / ".cadastro-fotos-migracao-v1.json",
    )
    authority.update(journals)
    paths.update(authority)
    for extra in extras:
        if extra:
            paths.add(Path(extra))
    # Include non-existent sidecars, so creation/deletion changes the revision.
    for path in tuple(paths):
        if path.suffix.casefold() == ".db":
            paths.add(Path(str(path) + "-wal"))
            paths.add(Path(str(path) + "-journal"))
    by_path = {path: assinatura_arquivo(path, cached_stats.get(path)) for path in paths}
    signatures = tuple(sorted(by_path.values()))
    hard = tuple(sorted(by_path[path] for path in authority))
    pending = any(path.exists() for path in journals) or any(path.name.endswith("-journal") and path.exists() for path in paths)
    physical_root = os.path.normcase(os.path.realpath(root))
    return Fontes(
        (datetime.now().strftime("%Y-%m-%d"), signatures),
        (physical_root, tuple(escopo), hard),
        alvo, snapshot, pending, (physical_root, hard),
    )


def extras_runtime(module):
    from backend.services import impostos_ncm
    extras = [getattr(module, name, "") for name in (
        "ARQUIVO_DB_CADASTRO_PRODUTOS", "ARQUIVO_DB_PRODUTOS", "ARQUIVO_NCM_XLSX", "ARQUIVO_NCM1_XLSX",
    )]
    info = getattr(module, "PASTA_INFO", "")
    if info:
        extras.append(os.path.join(info, "default", "cadastro_produtos.csv"))
        extras.append(os.path.join(info, "default", "cadastro_fotos"))
        extras.append(os.path.join(info, ".cadastro_tenant_trust.json"))
    extras.extend(getattr(impostos_ncm, name, "") for name in ("ARQUIVO_NCM_XLSX", "ARQUIVO_NCM1_XLSX"))
    return extras


def digest_lista(lista):
    return digest_json(lista)
