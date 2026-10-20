"""Read-only, privacy-limited inventory of exact JK store references.

Only the standard library is used. SQLite is opened exclusively on external
copies; a report is an observation, never authorization to remove a store.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
import time
import unicodedata

SCHEMA = "jk.shared_sync.store_report.v1"
IDENTITY_FIELDS = ("store_id", "store_ref")
NAME_FIELDS = ("loja", "loja_sync", "loja_nome", "nome_loja", "store_name")
FORMATS = ("csv", "json", "sqlite")
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
MAX_TEXT_BYTES = 64 * 1024 * 1024
MAX_SQLITE_BYTES = 1024 * 1024 * 1024
MAX_FILES = 100_000
MAX_RECORDS = 1_000_000
PRIVATE_KEYS = {
    "integracoes", "credentials", "tokens", "access_token", "refresh_token",
    "client_secret", "app_id", "client_id", "seller_id", "account_id",
    "email", "cpf", "cnpj", "prompt", "messages", "history", "response",
}
MESSAGES = {
    "observation_only": "Contagens sao ocorrencias de referencias, nao produtos ou prova de uso. Zero nao autoriza remover lojas; produtores podem continuar gravando.",
    "name_is_not_account_identity": "Nomes iguais indicam apenas candidatos. Nome nao prova a mesma conta; referencias somente por nome nao pertencem a nenhuma copia neste relatorio.",
    "registry_invalid": "O cadastro de lojas esta ausente, invalido ou possui identidades repetidas/inseguras.",
    "name_redacted": "Um nome de loja foi omitido por conter formato sensivel ou inseguro.",
    "linked_entry_skipped": "Link, junction, reparse point ou arquivo com multiplos links foi ignorado.",
    "read_failed": "Um arquivo ou diretorio nao pode ser examinado integralmente.",
    "invalid_format": "Um arquivo possui formato invalido ou estrutura nao suportada.",
    "unsupported_format": "Existem arquivos ou estruturas cujo conteudo nao foi examinado.",
    "sensitive_excluded": "Arquivos de credenciais/integracoes ou historico privado foram excluidos da leitura.",
    "capture_changed": "Arquivos mudaram durante a observacao; as contagens podem representar estados diferentes.",
    "sqlite_copy_unstable": "DB, WAL ou SHM mudaram durante a copia; nao ha garantia de snapshot consistente.",
    "sqlite_not_examined": "Uma copia SQLite nao pode ser validada/examinada integralmente.",
    "unknown_reference": "Existem referencias sem correspondencia exata com um ID JK listado.",
    "identity_value_invalid": "Existem campos de identidade vazios ou com tipo nao suportado.",
    "conflicting_identity": "Um registro referencia mais de um ID JK conhecido.",
    "name_only_reference": "Existem referencias somente por nome; nenhuma foi atribuida a uma copia.",
    "photo_unattributed": "Existem fotos sem caminho de loja reconhecido; nenhuma foi atribuida por nome.",
    "limit_reached": "Um limite de tamanho, quantidade ou tempo impediu o exame completo.",
}


class UnsafeInput(Exception):
    pass


def _signature(value):
    return (value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _is_alias(value):
    return (stat.S_ISLNK(value.st_mode)
            or bool(getattr(value, "st_file_attributes", 0) & 0x400)
            or (stat.S_ISREG(value.st_mode) and value.st_nlink > 1))


def _without_aliases(path: Path):
    current = path
    while True:
        value = current.lstat()
        if _is_alias(value):
            raise UnsafeInput("linked_entry_skipped")
        if current == current.parent:
            return
        current = current.parent


def _inside(path: Path, root: Path):
    try:
        shared = os.path.commonpath((str(path), str(root)))
        return os.path.normcase(shared) == os.path.normcase(str(root))
    except ValueError:
        return False


def _normal_name(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _safe_store_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value))


def _safe_name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        return ""
    if any(unicodedata.category(char).startswith("C") for char in value):
        return ""
    if ("@" in value or "/" in value or "\\" in value or ":" in value
            or re.search(r"(?:\d[. /-]*){11,14}", value)
            or re.search(r"(?i)(access_token|refresh_token|client_secret|password|bearer\s)", value)):
        return ""
    return value.strip()


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _empty_refs():
    return {kind: {field: 0 for field in IDENTITY_FIELDS} for kind in FORMATS}


def _is_backup(path: Path):
    parts = [part.casefold() for part in path.parts]
    if any(part in {"backup", "backups", "journal", "journals", "_shared_sync_backups", "_stores_publication"}
           or part.startswith(("backup_", "backup-", ".backup")) for part in parts[:-1]):
        return True
    name = parts[-1]
    return (name.endswith((".bak", ".backup", ".old", "~"))
            or (name.startswith("lojas") and any(mark in name for mark in ("backup", "journal", ".bak", ".old"))))


def _is_sensitive(path: Path):
    lowered = [part.casefold() for part in path.parts]
    if any(part in {"prompts", "conversations", "chat_history", "credentials"} for part in lowered):
        return True
    return any(mark in lowered[-1] for mark in (
        "integrac", "credential", "credencial", "token", "secret", "service-account",
        "service_account", "oauth", "api_key", "apikey", "auth_users", "firebase",
    )) or lowered[-1].startswith(".env")


class StoreReport:
    def __init__(self, root):
        self.root = Path(os.path.abspath(root))
        self.complete = True
        self.warnings = {"observation_only", "name_is_not_account_identity"}
        self.coverage = {key: 0 for key in (
            "files_seen", "csv_files", "json_files", "sqlite_files", "sqlite_tables",
            "photo_files", "ignored_backups", "sensitive_files_excluded", "unsupported_files",
            "linked_entries", "invalid_files", "unstable_captures", "tables_without_supported_columns",
        )}
        self.stores = {}
        self.names = {}
        self.name_counts = {}
        self.unknown = _empty_refs()
        self.unattributed_photos = 0
        self.observed = {}

    def warn(self, code):
        self.complete = False
        self.warnings.add(code)

    def inspect(self, path):
        _without_aliases(path)
        value = path.lstat()
        if _is_alias(value):
            raise UnsafeInput("linked_entry_skipped")
        self.observed.setdefault(path, _signature(value))
        return value

    def read(self, path, limit=MAX_TEXT_BYTES, destination=None):
        before = self.inspect(path)
        if not stat.S_ISREG(before.st_mode):
            raise UnsafeInput("read_failed")
        if before.st_size > limit:
            raise UnsafeInput("limit_reached")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        chunks = []
        digest = hashlib.sha256()
        total = 0
        with os.fdopen(descriptor, "rb") as stream:
            # Windows path-stat and handle-stat can expose different ctime
            # semantics. Compare inode/size/mtime across APIs; compare ctime
            # only between two path-stat observations below.
            if _signature(os.fstat(stream.fileno()))[:-1] != _signature(before)[:-1]:
                raise UnsafeInput("capture_changed")
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise UnsafeInput("limit_reached")
                digest.update(chunk)
                if destination is None:
                    chunks.append(chunk)
                else:
                    destination.write(chunk)
            after_open = os.fstat(stream.fileno())
        after_path = self.inspect(path)
        if _signature(before)[:-1] != _signature(after_open)[:-1] or _signature(before) != _signature(after_path):
            self.coverage["unstable_captures"] += 1
            self.warn("capture_changed")
        return b"".join(chunks) if destination is None else digest.hexdigest()

    def registry(self):
        try:
            payload = json.loads(self.read(self.root / "lojas_config.json").decode("utf-8-sig"), object_pairs_hook=_json_object)
            if not isinstance(payload, list):
                raise ValueError("registry")
            for entry in payload:
                if not isinstance(entry, dict) or not _safe_store_id(entry.get("store_id")):
                    self.warn("registry_invalid")
                    continue
                key = entry["store_id"]
                if key in self.stores:
                    self.warn("registry_invalid")
                    continue
                name = _safe_name(entry.get("nome"))
                if not name:
                    self.warn("name_redacted")
                self.stores[key] = {"store_id": key, "name": name,
                                    "exact_references": _empty_refs(),
                                    "photos": {"sid_sha256": 0, "legacy_id": 0}}
                if name:
                    normalized = _normal_name(name)
                    self.names.setdefault(normalized, []).append(key)
                    self.name_counts.setdefault(normalized, {kind: 0 for kind in FORMATS})
        except (OSError, UnicodeError, ValueError, RecursionError, UnsafeInput) as exc:
            self.warn(str(exc) if isinstance(exc, UnsafeInput) else "registry_invalid")
            self.warn("registry_invalid")

    def consume(self, record, kind, count=1):
        matched = set()
        recognized = False
        for field in IDENTITY_FIELDS:
            if field not in record:
                continue
            recognized = True
            value = record[field]
            if isinstance(value, str) and value in self.stores:
                self.stores[value]["exact_references"][kind][field] += count
                matched.add(value)
            else:
                self.unknown[kind][field] += count
                self.warn("identity_value_invalid" if not isinstance(value, str) or not value else "unknown_reference")
        if len(matched) > 1:
            self.warn("conflicting_identity")
        if not matched:
            for field in NAME_FIELDS:
                value = record.get(field)
                if isinstance(value, str) and _normal_name(value) in self.names:
                    normalized = _normal_name(value)
                    self.name_counts[normalized][kind] += count
                    recognized = True
                    self.warn("name_only_reference")
        return recognized

    def csv(self, path):
        data = self.read(path).decode("utf-8-sig")
        try:
            dialect = csv.Sniffer().sniff(data[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(data, newline=""), dialect=dialect, strict=True)
        header = next(reader, [])
        if not header or len(header) != len(set(header)):
            raise ValueError("header")
        relevant = any(field in header for field in (*IDENTITY_FIELDS, *NAME_FIELDS))
        if not relevant:
            self.warn("unsupported_format")
        for index, values in enumerate(reader):
            if index >= MAX_RECORDS:
                raise UnsafeInput("limit_reached")
            if not values:
                continue
            if len(values) != len(header):
                raise ValueError("row width")
            self.consume(dict(zip(header, values)), "csv")

    def json(self, path):
        payload = json.loads(self.read(path).decode("utf-8-sig"), object_pairs_hook=_json_object)
        pending = [payload]
        recognized = False
        visited = 0
        while pending:
            visited += 1
            if visited > MAX_RECORDS:
                raise UnsafeInput("limit_reached")
            value = pending.pop()
            if isinstance(value, dict):
                recognized = self.consume(value, "json") or recognized
                pending.extend(child for key, child in value.items()
                               if key.casefold() not in PRIVATE_KEYS and isinstance(child, (list, dict)))
            elif isinstance(value, list):
                pending.extend(child for child in value if isinstance(child, (list, dict)))
        if not recognized:
            self.warn("unsupported_format")

    def photo(self, relative):
        self.coverage["photo_files"] += 1
        parts = relative.parts
        if len(parts) >= 4 and parts[0].casefold() == "cadastro_fotos" and parts[1].casefold() == "lojas":
            segment = parts[2]
            for store_id, store in self.stores.items():
                if segment == "sid-" + hashlib.sha256(store_id.encode("utf-8")).hexdigest():
                    store["photos"]["sid_sha256"] += 1
                    return
                if segment == store_id:
                    store["photos"]["legacy_id"] += 1
                    return
        self.unattributed_photos += 1
        self.warn("photo_unattributed")

    def sqlite(self, path):
        # Never connect SQLite to an original: even mode=ro can touch WAL/SHM.
        temp_root = Path(os.path.abspath(tempfile.gettempdir()))
        _without_aliases(temp_root)
        if _inside(temp_root, self.root):
            raise UnsafeInput("sqlite_not_examined")
        if os.path.lexists(Path(str(path) + "-journal")):
            # A rollback journal requires a different capture protocol.
            raise UnsafeInput("sqlite_not_examined")
        siblings = [Path(str(path) + suffix) for suffix in ("", "-wal", "-shm")]
        before = {}
        for item in siblings:
            if os.path.lexists(item):
                before[item] = _signature(self.inspect(item))
        with tempfile.TemporaryDirectory(prefix="jk-store-report-", dir=temp_root) as temporary:
            target = Path(temporary) / "snapshot.db"
            hashes = {}
            for item in before:
                suffix = str(item)[len(str(path)):]
                with Path(str(target) + suffix).open("xb") as output:
                    hashes[item] = self.read(item, MAX_SQLITE_BYTES, output)
            unstable = False
            for item in siblings:
                present = os.path.lexists(item)
                if present != (item in before):
                    unstable = True
                    continue
                if present:
                    # Verify both metadata and bytes; simultaneous WAL writes
                    # can preserve length, and metadata alone is insufficient.
                    if _signature(self.inspect(item)) != before[item]:
                        unstable = True
                    with open(os.devnull, "wb") as sink:
                        if self.read(item, MAX_SQLITE_BYTES, sink) != hashes[item]:
                            unstable = True
            if unstable:
                self.coverage["unstable_captures"] += 1
                self.warn("sqlite_copy_unstable")
            connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=1)
            try:
                connection.execute("PRAGMA query_only=ON")
                connection.execute("PRAGMA trusted_schema=OFF")
                deadline = time.monotonic() + 30
                connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
                if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise UnsafeInput("sqlite_not_examined")
                tables = connection.execute("PRAGMA table_list").fetchall()
                if not tables:
                    raise UnsafeInput("sqlite_not_examined")
                for schema, name, table_type, *_rest in tables:
                    if schema != "main" or name.startswith("sqlite_"):
                        continue
                    if table_type != "table":
                        self.warn("unsupported_format")
                        continue
                    self.coverage["sqlite_tables"] += 1
                    quoted = '"' + name.replace('"', '""') + '"'
                    columns = connection.execute("PRAGMA table_xinfo(" + quoted + ")").fetchall()
                    allowed = [column[1] for column in columns
                               if column[1] in (*IDENTITY_FIELDS, *NAME_FIELDS) and not column[6]]
                    if not allowed:
                        self.coverage["tables_without_supported_columns"] += 1
                        self.warn("unsupported_format")
                        continue
                    selected = ", ".join('"' + column.replace('"', '""') + '"' for column in allowed)
                    query = "SELECT " + selected + ", COUNT(*) FROM " + quoted + " GROUP BY " + selected
                    for index, values in enumerate(connection.execute(query)):
                        if index >= MAX_RECORDS:
                            raise UnsafeInput("limit_reached")
                        self.consume(dict(zip(allowed, values[:-1])), "sqlite", values[-1])
            finally:
                connection.close()

    def scan(self):
        pending = [self.root]
        while pending:
            directory = pending.pop()
            try:
                self.inspect(directory)
                children = sorted(os.scandir(directory), key=lambda entry: entry.name.casefold())
            except (OSError, UnsafeInput) as exc:
                self.warn(str(exc) if isinstance(exc, UnsafeInput) else "read_failed")
                continue
            for child in children:
                path = Path(child.path)
                relative = path.relative_to(self.root)
                try:
                    value = self.inspect(path)
                    if stat.S_ISDIR(value.st_mode):
                        if _is_backup(relative / "placeholder"):
                            self.coverage["ignored_backups"] += 1
                        elif _is_sensitive(relative / "placeholder"):
                            self.coverage["sensitive_files_excluded"] += 1
                            self.warn("sensitive_excluded")
                        else:
                            pending.append(path)
                        continue
                    if not stat.S_ISREG(value.st_mode):
                        raise UnsafeInput("unsupported_format")
                    self.coverage["files_seen"] += 1
                    if self.coverage["files_seen"] > MAX_FILES:
                        self.warn("limit_reached")
                        return
                    if relative == Path("lojas_config.json"):
                        continue
                    if _is_backup(relative):
                        self.coverage["ignored_backups"] += 1
                        continue
                    if _is_sensitive(relative):
                        self.coverage["sensitive_files_excluded"] += 1
                        self.warn("sensitive_excluded")
                        continue
                    suffix = path.suffix.casefold()
                    if relative.parts[0].casefold() == "cadastro_fotos" and suffix in PHOTO_EXTENSIONS:
                        self.photo(relative)
                    elif suffix == ".csv":
                        self.coverage["csv_files"] += 1
                        self.csv(path)
                    elif suffix == ".json":
                        self.coverage["json_files"] += 1
                        self.json(path)
                    elif suffix in {".db", ".sqlite", ".sqlite3"}:
                        self.coverage["sqlite_files"] += 1
                        try:
                            self.sqlite(path)
                        except (FileNotFoundError, NotADirectoryError):
                            self.warn("sqlite_copy_unstable")
                            raise UnsafeInput("sqlite_not_examined")
                    elif path.name.endswith(("-wal", "-shm")) and path.with_name(path.name[:-4]).suffix.casefold() in {".db", ".sqlite", ".sqlite3"}:
                        # Captured together with the database; never parse alone.
                        try:
                            database_stat = self.inspect(path.with_name(path.name[:-4]))
                            if not stat.S_ISREG(database_stat.st_mode):
                                self.warn("sqlite_not_examined")
                        except FileNotFoundError:
                            self.warn("sqlite_not_examined")
                    else:
                        self.coverage["unsupported_files"] += 1
                        self.warn("unsupported_format")
                except UnsafeInput as exc:
                    if str(exc) == "linked_entry_skipped":
                        self.coverage["linked_entries"] += 1
                    self.warn(str(exc))
                except sqlite3.Error:
                    self.coverage["invalid_files"] += 1
                    self.warn("sqlite_not_examined")
                except (OSError, UnicodeError, ValueError, csv.Error, RecursionError):
                    self.coverage["invalid_files"] += 1
                    self.warn("invalid_format")
        for path, signature in self.observed.items():
            try:
                _without_aliases(path)
                if _signature(path.lstat()) != signature:
                    self.warn("capture_changed")
            except (OSError, UnsafeInput):
                self.warn("capture_changed")

    def result(self):
        groups = []
        ambiguous = []
        for normalized, identities in sorted(self.names.items()):
            name = self.stores[identities[0]]["name"]
            if len(identities) > 1:
                groups.append({"name": name, "store_ids": sorted(identities), "name_does_not_prove_account": True})
            counts = self.name_counts[normalized]
            if any(counts.values()):
                ambiguous.append({"name": name, "candidate_store_ids": sorted(identities),
                                  "reference_counts": counts, "assigned_to_store": False})
        return {"schema": SCHEMA, "complete": self.complete,
                "final_state_guaranteed": False, "zero_proves_unused": False,
                "counts_are_reference_occurrences": True,
                "stores": [self.stores[key] for key in sorted(self.stores)],
                "candidate_groups_by_name": groups, "ambiguous_name_references": ambiguous,
                "unattributed_exact_references": self.unknown,
                "unattributed_photos": self.unattributed_photos,
                "coverage": self.coverage,
                "warnings": [{"code": code, "message": MESSAGES[code]} for code in sorted(self.warnings)]}


def build_report(tenant_root):
    scanner = StoreReport(tenant_root)
    try:
        _without_aliases(scanner.root)
        if not scanner.root.is_dir():
            raise UnsafeInput("registry_invalid")
        scanner.registry()
        scanner.scan()
    except (OSError, UnsafeInput) as exc:
        scanner.warn(str(exc) if isinstance(exc, UnsafeInput) else "read_failed")
    return scanner.result()


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


def main(argv=None):
    parser = SafeArgumentParser(description="Relatorio somente leitura de referencias exatas de lojas JK.")
    parser.add_argument("--tenant-root", required=True, help="Pasta direta que contem lojas_config.json.")
    parser.add_argument("--output", required=True, help="JSON novo, fora da pasta do cliente.")
    try:
        args = parser.parse_args(argv)
        root = Path(os.path.abspath(args.tenant_root))
        output = Path(os.path.abspath(args.output))
        _without_aliases(output.parent)
        if _inside(output, root) or os.path.lexists(output):
            raise UnsafeInput("unsafe output")
        report = build_report(root)
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (Exception, KeyboardInterrupt):
        # Never expose raw exception text, command arguments or local paths.
        print("Nao foi possivel gerar o relatorio. Verifique as pastas e use um arquivo de saida novo, externo ao cliente.", file=sys.stderr)
        return 2
    print("Relatorio criado." if report["complete"] else "Relatorio criado com indicacoes de exame incompleto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
