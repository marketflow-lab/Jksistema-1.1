"""Context Hub journal component."""

from __future__ import annotations

import json
import re



from backend.modules.context_hub.contracts import (
    ContextHubPaths,
    ContextHubValidationError,
)

from backend.modules.context_hub.filesystem import (
    _replace_with_retry,
)

from backend.modules.context_hub.locking import (
    _safe_remove_tree,
)

from backend.modules.context_hub.storage import (
    _connect,
)


def _recover_publish_journal(paths: ContextHubPaths) -> None:
    """Recover a directory swap interrupted before the database CAS completed."""

    if not paths.journal_path.is_file():
        return
    try:
        journal = json.loads(paths.journal_path.read_text(encoding="utf-8"))
    except Exception:
        # An unreadable journal must fail closed; leave it for an administrator.
        raise ContextHubValidationError("Journal de publicacao invalido; publicacao bloqueada.")
    generation_id = str(journal.get("generation_id") or "")
    if not re.fullmatch(r"[a-f0-9]{32}", generation_id):
        raise ContextHubValidationError("Journal de publicacao invalido; publicacao bloqueada.")
    temporary = paths.vault_dir / f".context_hub_publish_{generation_id}"
    backup = paths.vault_dir / f".context_hub_backup_{generation_id}"
    state = str(journal.get("state") or "")
    if state not in {"prepared", "old_moved", "new_active"}:
        raise ContextHubValidationError("Estado do journal de publicacao invalido; publicacao bloqueada.")
    previous_generation_id = str(journal.get("previous_generation_id") or "")
    if previous_generation_id and not re.fullmatch(r"[a-f0-9]{32}", previous_generation_id):
        raise ContextHubValidationError("Journal de publicacao invalido; publicacao bloqueada.")
    raw_had_previous = journal.get("had_previous")
    if raw_had_previous is not None and not isinstance(raw_had_previous, bool):
        raise ContextHubValidationError("Journal de publicacao invalido; publicacao bloqueada.")
    active_id = None
    with _connect(paths) as connection:
        row = connection.execute(
            "SELECT generation_id FROM context_hub_active_generation WHERE singleton_id=1"
        ).fetchone()
        active_id = str(row["generation_id"] or "") if row else ""
    if previous_generation_id and active_id not in {previous_generation_id, generation_id}:
        raise ContextHubValidationError("Geracao ativa divergiu do journal; publicacao bloqueada.")
    had_previous = (
        raw_had_previous
        if isinstance(raw_had_previous, bool)
        else bool(previous_generation_id or (active_id and active_id != generation_id) or backup.exists())
    )
    if state == "new_active" and active_id == generation_id:
        _safe_remove_tree(backup, paths.vault_dir)
        _safe_remove_tree(temporary, paths.vault_dir)
        paths.journal_path.unlink(missing_ok=True)
        return

    # Antes do CAS do banco, qualquer arvore nova deve ser abortada e a
    # geracao indicada pelo ponteiro ativo deve continuar visivel.  O estado
    # `prepared` tambem pode ter backup: existe uma janela entre mover a arvore
    # antiga e persistir `old_moved`.
    if backup.exists():
        if paths.generated_dir.exists():
            _safe_remove_tree(paths.generated_dir, paths.vault_dir)
        _replace_with_retry(backup, paths.generated_dir)
    elif had_previous:
        if state != "prepared" or not paths.generated_dir.exists():
            raise ContextHubValidationError(
                "Backup da geracao ativa indisponivel; recuperacao bloqueada."
            )
        # prepared + arvore presente + sem backup: o movimento ainda nao
        # aconteceu; a arvore atual ja e a anterior e deve ser preservada.
    elif paths.generated_dir.exists():
        # Primeira publicacao interrompida antes do CAS: nao existe geracao
        # anterior no banco, portanto uma arvore nova parcial nao pode ficar
        # exposta como ativa.
        _safe_remove_tree(paths.generated_dir, paths.vault_dir)
    _safe_remove_tree(temporary, paths.vault_dir)
    paths.journal_path.unlink(missing_ok=True)
