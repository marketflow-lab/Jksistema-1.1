"""Filesystem paths shared by isolated backend modules."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    """Resolve application and tenant paths without importing ``backend_api``."""

    base_dir: Path
    info_dir: Path
    logger: logging.Logger

    @classmethod
    def create(
        cls,
        *,
        base_dir: str | os.PathLike[str],
        info_dir: str | os.PathLike[str],
        logger: logging.Logger,
    ) -> "AppPaths":
        base = Path(base_dir).expanduser().resolve()
        info = Path(info_dir).expanduser()
        if not info.is_absolute():
            info = base / info
        return cls(base_dir=base, info_dir=info.resolve(), logger=logger)

    @property
    def legacy_products_csv(self) -> str:
        return str(self.info_dir / "produtos_compilado.csv")

    def tenant_path(self, client_id: str, *, create: bool = True) -> str:
        tenant = self.info_dir / str(client_id or "default")
        if create:
            tenant.mkdir(parents=True, exist_ok=True)
        return str(tenant)

    def tenant_file(self, client_id: str, filename: str, *, create_parent: bool = True) -> str:
        tenant = Path(self.tenant_path(client_id, create=create_parent))
        return str(tenant / filename)

    def migrate_legacy_file(
        self,
        client_id: str,
        filename: str,
        legacy_path: str | os.PathLike[str] | None,
    ) -> str:
        """Move a legacy shared file into a tenant, falling back to a safe copy."""

        destination = Path(self.tenant_file(client_id, filename))
        if destination.exists():
            return str(destination)

        source = Path(legacy_path).expanduser() if legacy_path else None
        if source is None or not source.exists():
            return str(destination)

        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(source), str(destination))
            self.logger.info("[MIGRACAO] %s movido para tenant %s", filename, client_id)
        except Exception as move_error:
            try:
                shutil.copy2(str(source), str(destination))
                self.logger.warning(
                    "[MIGRACAO] %s copiado para tenant %s (origem preservada): %s",
                    filename,
                    client_id,
                    move_error,
                )
            except Exception as copy_error:
                self.logger.warning(
                    "[MIGRACAO] Falha ao migrar %s para tenant %s: %s",
                    filename,
                    client_id,
                    copy_error,
                )
        return str(destination)


__all__ = ["AppPaths"]
