"""Padroniza uma raiz de tenant legada (junction/symlink) sem apagar a origem.

O modo padrao e somente leitura. A aplicacao exige tenant explicito, token
gerado pelo preflight e ausencia dos servidores locais do JK Sistema.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import sys
import tempfile
import time
from typing import Any, Iterable
import uuid


JOURNAL_NAME = "tenant-root-migration-journal.json"
STATUS_NAME = "tenant-root-migration-status.json"
STAGE_PREFIX = ".tenant-root-migration-stage-"
BACKUP_PREFIX = ".tenant-root-migration-link-"
SAFE_TENANT_LENGTH = 6
BLOCKING_PORTS = (8001, 8011, 8012)


class TenantRootMigrationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code or "tenant_root_migration_failed")


def _io_path(path: Path) -> Path:
    """Use Win32 extended paths so legacy names ending in dots are preserved."""
    absolute = os.path.abspath(path)
    if os.name != "nt" or absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute.lstrip("\\"))
    return Path("\\\\?\\" + absolute)


def _is_safe_tenant_id(value: str) -> bool:
    return len(str(value or "")) == SAFE_TENANT_LENGTH and str(value).isdigit()


def _is_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400) or 0x400)
    is_junction = getattr(os.path, "isjunction", None)
    return bool(
        path.is_symlink()
        or attributes & reparse_flag
        or (callable(is_junction) and is_junction(path))
    )


def _assert_physical_directory(path: Path, label: str) -> None:
    if not path.exists() or _is_reparse(path) or not path.is_dir():
        raise TenantRootMigrationError(
            "tenant_root_invalid",
            f"{label} deve ser um diretorio fisico.",
        )


def _safe_child(root: Path, name: str, prefix: str = "") -> Path:
    raw = str(name or "")
    if (
        not raw
        or raw in {".", ".."}
        or "/" in raw
        or "\\" in raw
        or (prefix and not raw.startswith(prefix))
    ):
        raise TenantRootMigrationError(
            "migration_journal_invalid",
            "Artefato de migracao invalido.",
        )
    root_abs = Path(os.path.abspath(root))
    candidate = Path(os.path.abspath(root_abs / raw))
    try:
        candidate.relative_to(root_abs)
    except ValueError as exc:
        raise TenantRootMigrationError(
            "migration_path_escape",
            "Artefato fora da raiz de dados.",
        ) from exc
    if candidate == root_abs:
        raise TenantRootMigrationError(
            "migration_path_escape",
            "Artefato fora da raiz de dados.",
        )
    return candidate


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(root: Path) -> list[dict[str, Any]]:
    _assert_physical_directory(root, "Destino legado do tenant")
    root = _io_path(root)
    records: list[dict[str, Any]] = []
    stack: list[tuple[Path, str]] = [(root, "")]
    while stack:
        current, relative_root = stack.pop()
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name.casefold(), reverse=True)
        except OSError as exc:
            raise TenantRootMigrationError(
                "tenant_source_unreadable",
                "Nao foi possivel inventariar a raiz legada.",
            ) from exc
        for child in children:
            relative = f"{relative_root}/{child.name}".lstrip("/").replace("\\", "/")
            try:
                metadata = child.lstat()
            except OSError as exc:
                raise TenantRootMigrationError(
                    "tenant_source_unreadable",
                    f"Nao foi possivel ler {relative}.",
                ) from exc
            if _is_reparse(child):
                raise TenantRootMigrationError(
                    "tenant_source_reparse",
                    f"A raiz legada contem link ou junction em {relative}.",
                )
            if stat.S_ISDIR(metadata.st_mode):
                records.append({"type": "directory", "path": relative})
                stack.append((child, relative))
            elif stat.S_ISREG(metadata.st_mode):
                records.append({
                    "type": "file",
                    "path": relative,
                    "size": int(metadata.st_size),
                    "mtime_ns": int(metadata.st_mtime_ns),
                })
            else:
                raise TenantRootMigrationError(
                    "tenant_source_special_file",
                    f"A raiz legada contem tipo de arquivo nao suportado em {relative}.",
                )
    records.sort(key=lambda item: str(item["path"]).casefold())
    return records


def _inventory_signature(records: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(
            (
                f"{record.get('type', '')}\0{record.get('path', '')}\0"
                f"{record.get('size', 0)}\0{record.get('mtime_ns', 0)}\n"
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _content_fingerprint(records: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda item: str(item["path"]).casefold()):
        digest.update(
            (
                f"{record.get('type', '')}\0{record.get('path', '')}\0"
                f"{record.get('size', 0)}\0{record.get('sha256', '')}\n"
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _confirmation_token(tenant_id: str, inventory_sha256: str) -> str:
    return hashlib.sha256(
        f"jk-tenant-root-v1|{tenant_id}|{inventory_sha256}".encode("utf-8")
    ).hexdigest()[:32]


def _tenant_plan(info_root: Path, tenant_id: str) -> dict[str, Any]:
    if not _is_safe_tenant_id(tenant_id):
        raise TenantRootMigrationError(
            "tenant_id_invalid",
            "O tenant deve ter exatamente seis digitos.",
        )
    tenant_root = _safe_child(info_root, tenant_id)
    if not _is_reparse(tenant_root):
        return {
            "tenant_id": tenant_id,
            "requires_migration": False,
            "reason": "physical_or_missing",
        }
    try:
        source_root = Path(os.path.realpath(tenant_root))
    except OSError as exc:
        raise TenantRootMigrationError(
            "tenant_link_target_invalid",
            "O destino da junction legada nao esta disponivel.",
        ) from exc
    _assert_physical_directory(source_root, "Destino legado do tenant")
    info_real = Path(os.path.realpath(info_root))
    try:
        source_root.relative_to(info_real)
    except ValueError:
        pass
    else:
        raise TenantRootMigrationError(
            "tenant_link_target_invalid",
            "A junction legada aponta para dentro da propria raiz de dados.",
        )
    inventory = _inventory(source_root)
    inventory_sha256 = _inventory_signature(inventory)
    files = [item for item in inventory if item["type"] == "file"]
    return {
        "tenant_id": tenant_id,
        "requires_migration": True,
        "file_count": len(files),
        "directory_count": len(inventory) - len(files),
        "total_bytes": sum(int(item.get("size") or 0) for item in files),
        "inventory_sha256": inventory_sha256,
        "confirmation_token": _confirmation_token(tenant_id, inventory_sha256),
        "source_preserved": True,
        "_source_root": source_root,
        "_inventory": inventory,
    }


def inspect_legacy_tenant_roots(target_root: Path, tenant_id: str = "") -> dict[str, Any]:
    target = Path(os.path.abspath(target_root))
    _assert_physical_directory(target, "Runtime local")
    info_root = target / "info"
    _assert_physical_directory(info_root, "Raiz info")
    journal = _read_json(info_root / JOURNAL_NAME) if (info_root / JOURNAL_NAME).exists() else None
    if tenant_id:
        ids = [tenant_id]
    else:
        ids = sorted(
            item.name
            for item in info_root.iterdir()
            if _is_safe_tenant_id(item.name) and _is_reparse(item)
        )
    plans = []
    for candidate in ids:
        plan = _tenant_plan(info_root, candidate)
        plans.append({key: value for key, value in plan.items() if not key.startswith("_")})
    return {
        "success": True,
        "mode": "plan",
        "recovery_required": bool(journal),
        "tenants": plans,
    }


def _assert_runtime_stopped(ports: Iterable[int] = BLOCKING_PORTS) -> None:
    active = []
    for port in ports:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
                active.append(int(port))
        except OSError:
            continue
    if active:
        raise TenantRootMigrationError(
            "runtime_active",
            "Feche completamente o JK Sistema antes de aplicar o reparo offline.",
        )


def _assert_free_space(info_root: Path, required_bytes: int) -> None:
    usage = shutil.disk_usage(info_root)
    reserve = max(64 * 1024 * 1024, int(required_bytes * 0.05))
    if int(usage.free) < int(required_bytes) + reserve:
        raise TenantRootMigrationError(
            "disk_space_insufficient",
            "Espaco livre insuficiente para criar e verificar a raiz fisica.",
        )


def _copy_verified(
    source_root: Path,
    stage_root: Path,
    inventory: list[dict[str, Any]],
    status_path: Path,
    tenant_id: str,
) -> dict[str, Any]:
    stage_root.mkdir(parents=False, exist_ok=False)
    directories = sorted(
        (item for item in inventory if item["type"] == "directory"),
        key=lambda item: (str(item["path"]).count("/"), str(item["path"]).casefold()),
    )
    for entry in directories:
        _io_path(stage_root / Path(str(entry["path"]))).mkdir(parents=False, exist_ok=False)
    files = [item for item in inventory if item["type"] == "file"]
    records = [{"type": "directory", "path": item["path"]} for item in directories]
    copied_bytes = 0
    for index, entry in enumerate(files, start=1):
        source = _io_path(source_root / Path(str(entry["path"])))
        target = _io_path(stage_root / Path(str(entry["path"])))
        before = source.lstat()
        if (
            _is_reparse(source)
            or not stat.S_ISREG(before.st_mode)
            or int(before.st_size) != int(entry["size"])
            or int(before.st_mtime_ns) != int(entry["mtime_ns"])
        ):
            raise TenantRootMigrationError(
                "tenant_source_changed",
                f"Arquivo legado mudou durante a copia: {entry['path']}.",
            )
        shutil.copy2(source, target, follow_symlinks=False)
        source_hash = _sha256_file(source)
        target_hash = _sha256_file(target)
        after = source.lstat()
        target_stat = target.lstat()
        if (
            _is_reparse(source)
            or _is_reparse(target)
            or not stat.S_ISREG(after.st_mode)
            or not stat.S_ISREG(target_stat.st_mode)
            or int(after.st_size) != int(entry["size"])
            or int(after.st_mtime_ns) != int(entry["mtime_ns"])
            or int(target_stat.st_size) != int(entry["size"])
            or target_hash != source_hash
        ):
            raise TenantRootMigrationError(
                "tenant_copy_mismatch",
                f"Copia verificada divergiu em {entry['path']}.",
            )
        records.append({
            "type": "file",
            "path": entry["path"],
            "size": int(entry["size"]),
            "sha256": source_hash,
        })
        copied_bytes += int(entry["size"])
        if index == len(files) or index % 250 == 0:
            _atomic_write_json(status_path, {
                "schema_version": 1,
                "state": "copying",
                "tenant_id": tenant_id,
                "copied_files": index,
                "total_files": len(files),
                "copied_bytes": copied_bytes,
                "updated_at": _now_iso(),
            })
    return {
        "fingerprint_sha256": _content_fingerprint(records),
        "file_count": len(files),
        "directory_count": len(directories),
        "total_bytes": copied_bytes,
    }


def _fingerprint_tree(root: Path) -> dict[str, Any]:
    inventory = _inventory(root)
    records: list[dict[str, Any]] = []
    total_bytes = 0
    for entry in inventory:
        if entry["type"] == "directory":
            records.append({"type": "directory", "path": entry["path"]})
            continue
        candidate = _io_path(root / Path(str(entry["path"])))
        records.append({
            "type": "file",
            "path": entry["path"],
            "size": int(entry["size"]),
            "sha256": _sha256_file(candidate),
        })
        total_bytes += int(entry["size"])
    return {
        "fingerprint_sha256": _content_fingerprint(records),
        "file_count": len([item for item in records if item["type"] == "file"]),
        "directory_count": len([item for item in records if item["type"] == "directory"]),
        "total_bytes": total_bytes,
    }


def _same_fingerprint(left: dict[str, Any], right: dict[str, Any]) -> bool:
    keys = ("fingerprint_sha256", "file_count", "directory_count", "total_bytes")
    return all(left.get(key) == right.get(key) for key in keys)


def _remove_link(path: Path) -> None:
    if not _is_reparse(path):
        raise TenantRootMigrationError(
            "migration_link_expected",
            "O artefato esperado deixou de ser uma junction.",
        )
    if os.name == "nt" and path.is_dir():
        os.rmdir(path)
    else:
        path.unlink()


def _remove_stage(path: Path, info_root: Path) -> None:
    expected = _safe_child(info_root, path.name, STAGE_PREFIX)
    if expected != path or not os.path.lexists(path):
        return
    if _is_reparse(path):
        _remove_link(path)
        return
    _assert_physical_directory(path, "Staging de migracao")
    shutil.rmtree(_io_path(path))


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _inject_fault(configured: str, phase: str) -> None:
    if configured == phase:
        raise RuntimeError(f"fault_{phase}")
    if configured == f"crash_{phase}":
        raise SystemExit(f"crash_{phase}")


def recover_legacy_tenant_root_repair(target_root: Path) -> dict[str, Any]:
    """Conclui ou desfaz uma transacao interrompida, sempre de modo offline."""
    _assert_runtime_stopped()
    target = Path(os.path.abspath(target_root))
    _assert_physical_directory(target, "Runtime local")
    info_root = target / "info"
    _assert_physical_directory(info_root, "Raiz info")
    journal_path = info_root / JOURNAL_NAME
    status_path = info_root / STATUS_NAME
    journal = _read_json(journal_path)
    if not journal:
        return {"success": True, "mode": "recover", "changed": False}
    tenant_id = str(journal.get("tenant_id") or "")
    try:
        schema_version = int(journal.get("schema_version") or 0)
    except (TypeError, ValueError):
        schema_version = 0
    if schema_version != 1 or not _is_safe_tenant_id(tenant_id):
        raise TenantRootMigrationError(
            "migration_journal_invalid",
            "Journal de migracao da raiz de dados invalido.",
        )
    tenant_root = _safe_child(info_root, tenant_id)
    stage = _safe_child(info_root, str(journal.get("stage_name") or ""), STAGE_PREFIX)
    backup = _safe_child(info_root, str(journal.get("backup_name") or ""), BACKUP_PREFIX)
    expected = journal.get("expected") if isinstance(journal.get("expected"), dict) else None

    def finish(state: str, changed: bool) -> dict[str, Any]:
        journal_path.unlink(missing_ok=True)
        result = {
            "success": True,
            "mode": "recover",
            "changed": changed,
            "tenant_id": tenant_id,
            "state": state,
            "source_preserved": True,
            "completed_at": _now_iso(),
        }
        _atomic_write_json(status_path, {"schema_version": 1, **result})
        return result

    if os.path.lexists(tenant_root) and _is_reparse(tenant_root) and not os.path.lexists(backup):
        if os.path.lexists(stage):
            _remove_stage(stage, info_root)
        return finish("rolled_back", False)

    if not os.path.lexists(tenant_root) and _is_reparse(backup):
        if (
            expected
            and os.path.lexists(stage)
            and not _is_reparse(stage)
            and _same_fingerprint(_fingerprint_tree(stage), expected)
        ):
            stage.rename(tenant_root)
            if not _same_fingerprint(_fingerprint_tree(tenant_root), expected):
                tenant_root.rename(stage)
                backup.rename(tenant_root)
                _remove_stage(stage, info_root)
                return finish("rolled_back", False)
            _remove_link(backup)
            return finish("complete", True)
        backup.rename(tenant_root)
        if os.path.lexists(stage):
            _remove_stage(stage, info_root)
        return finish("rolled_back", False)

    if os.path.lexists(tenant_root) and not _is_reparse(tenant_root) and _is_reparse(backup):
        if expected and _same_fingerprint(_fingerprint_tree(tenant_root), expected):
            _remove_link(backup)
            if os.path.lexists(stage):
                _remove_stage(stage, info_root)
            return finish("complete", True)
        if os.path.lexists(stage):
            _remove_stage(stage, info_root)
        tenant_root.rename(stage)
        backup.rename(tenant_root)
        _remove_stage(stage, info_root)
        return finish("rolled_back", False)

    if os.path.lexists(tenant_root) and not _is_reparse(tenant_root) and not os.path.lexists(backup):
        if expected and _same_fingerprint(_fingerprint_tree(tenant_root), expected):
            if os.path.lexists(stage):
                _remove_stage(stage, info_root)
            return finish("complete", True)

    raise TenantRootMigrationError(
        "migration_recovery_ambiguous",
        "Nao foi possivel recuperar a migracao com seguranca; nenhum dado foi removido.",
    )


def apply_legacy_tenant_root_repair(
    target_root: Path,
    tenant_id: str,
    confirmation_token: str,
    *,
    fault_phase: str = "",
) -> dict[str, Any]:
    _assert_runtime_stopped()
    target = Path(os.path.abspath(target_root))
    _assert_physical_directory(target, "Runtime local")
    info_root = target / "info"
    _assert_physical_directory(info_root, "Raiz info")
    journal_path = info_root / JOURNAL_NAME
    status_path = info_root / STATUS_NAME
    if journal_path.exists():
        raise TenantRootMigrationError(
            "migration_recovery_required",
            "Existe uma migracao interrompida; nao inicie outra aplicacao.",
        )
    plan = _tenant_plan(info_root, tenant_id)
    if not plan.get("requires_migration"):
        return {"success": True, "mode": "apply", "changed": False, "tenant_id": tenant_id}
    if str(confirmation_token or "") != str(plan["confirmation_token"]):
        raise TenantRootMigrationError(
            "confirmation_token_mismatch",
            "O token nao corresponde ao inventario atual; execute o preflight novamente.",
        )
    _assert_free_space(info_root, int(plan["total_bytes"]))
    source_root = Path(plan["_source_root"])
    inventory = list(plan["_inventory"])
    tenant_root = _safe_child(info_root, tenant_id)
    transaction_id = f"{int(time.time())}-{os.getpid()}-{uuid.uuid4().hex[:10]}"
    stage = _safe_child(info_root, f"{STAGE_PREFIX}{tenant_id}-{transaction_id}", STAGE_PREFIX)
    backup = _safe_child(info_root, f"{BACKUP_PREFIX}{tenant_id}-{transaction_id}", BACKUP_PREFIX)
    journal = {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "tenant_id": tenant_id,
        "stage_name": stage.name,
        "backup_name": backup.name,
        "phase": "copying",
        "inventory_sha256": plan["inventory_sha256"],
        "started_at": _now_iso(),
    }
    _atomic_write_json(journal_path, journal)
    try:
        expected = _copy_verified(source_root, stage, inventory, status_path, tenant_id)
        if _inventory_signature(_inventory(source_root)) != plan["inventory_sha256"]:
            raise TenantRootMigrationError(
                "tenant_source_changed",
                "A raiz legada mudou durante a migracao.",
            )
        staged = _fingerprint_tree(stage)
        if not _same_fingerprint(staged, expected):
            raise TenantRootMigrationError(
                "tenant_copy_mismatch",
                "A verificacao final da copia fisica falhou.",
            )
        journal.update({"phase": "prepared", "expected": expected})
        _atomic_write_json(journal_path, journal)
        _inject_fault(fault_phase, "after_copy")

        tenant_root.rename(backup)
        journal["phase"] = "link_moved"
        _atomic_write_json(journal_path, journal)
        _inject_fault(fault_phase, "after_link_moved")
        stage.rename(tenant_root)
        journal["phase"] = "installed"
        _atomic_write_json(journal_path, journal)
        _inject_fault(fault_phase, "after_install")

        installed = _fingerprint_tree(tenant_root)
        if not _same_fingerprint(installed, expected):
            raise TenantRootMigrationError(
                "tenant_target_mismatch",
                "A raiz fisica instalada divergiu da copia verificada.",
            )
        _remove_link(backup)
        journal_path.unlink(missing_ok=True)
        result = {
            "success": True,
            "mode": "apply",
            "changed": True,
            "tenant_id": tenant_id,
            **expected,
            "source_preserved": True,
            "completed_at": _now_iso(),
        }
        _atomic_write_json(status_path, {"schema_version": 1, "state": "complete", **result})
        return result
    except Exception as exc:
        rollback_error = ""
        try:
            if os.path.lexists(tenant_root) and not _is_reparse(tenant_root) and _is_reparse(backup):
                if os.path.lexists(stage):
                    _remove_stage(stage, info_root)
                tenant_root.rename(stage)
                backup.rename(tenant_root)
            elif not os.path.lexists(tenant_root) and _is_reparse(backup):
                backup.rename(tenant_root)
            if os.path.lexists(stage):
                _remove_stage(stage, info_root)
            journal_path.unlink(missing_ok=True)
        except Exception as rollback_exc:  # pragma: no cover - exige falha dupla do SO
            rollback_error = type(rollback_exc).__name__
        error_code = getattr(exc, "code", "tenant_root_migration_failed")
        _atomic_write_json(status_path, {
            "schema_version": 1,
            "state": "recovery_required" if rollback_error else "failed",
            "tenant_id": tenant_id,
            "error_code": str(error_code),
            "rollback_error": rollback_error,
            "updated_at": _now_iso(),
        })
        if isinstance(exc, TenantRootMigrationError):
            raise
        raise TenantRootMigrationError(
            str(error_code),
            "Falha ao padronizar a raiz de dados; a junction original foi preservada.",
        ) from exc


def _public_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if not key.startswith("_")
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-root", required=True, help="Raiz local_app instalada.")
    parser.add_argument("--tenant-id", default="", help="Tenant de seis digitos.")
    parser.add_argument("--apply", action="store_true", help="Aplica a copia verificada e a troca atomica.")
    parser.add_argument("--recover", action="store_true", help="Recupera uma aplicacao interrompida.")
    parser.add_argument("--confirmation-token", default="", help="Token retornado pelo preflight.")
    args = parser.parse_args(argv)
    if args.apply and args.recover:
        parser.error("--apply e --recover sao mutuamente exclusivos")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.recover:
            result = recover_legacy_tenant_root_repair(Path(args.target_root))
        elif args.apply:
            if not args.tenant_id or not args.confirmation_token:
                raise TenantRootMigrationError(
                    "explicit_confirmation_required",
                    "--apply exige --tenant-id e --confirmation-token do preflight.",
                )
            result = apply_legacy_tenant_root_repair(
                Path(args.target_root),
                args.tenant_id,
                args.confirmation_token,
            )
        else:
            result = inspect_legacy_tenant_roots(Path(args.target_root), args.tenant_id)
        print(json.dumps(_public_payload(result), ensure_ascii=False, sort_keys=True))
        return 0
    except TenantRootMigrationError as exc:
        print(json.dumps({
            "success": False,
            "error_code": exc.code,
            "message": str(exc),
        }, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
