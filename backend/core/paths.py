"""Filesystem paths shared by isolated backend modules."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from backend.services.legacy_tenant_migration import (
    migrar_arquivo_legado_para_tenant_seguro,
)


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
        """Importa legado global apenas no tenant historico ``default``."""

        return migrar_arquivo_legado_para_tenant_seguro(
            client_id,
            filename,
            str(Path(legacy_path).expanduser()) if legacy_path else "",
            get_tenant_path=self.tenant_path,
            logger=self.logger,
        )


__all__ = ["AppPaths"]
