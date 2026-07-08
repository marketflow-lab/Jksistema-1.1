"""Shared Streamlit runtime adapter for marketplace label processors."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class _EtiquetasNoopProgress:
    def progress(self, *args, **kwargs):
        return self

    def empty(self):
        return None


class _EtiquetasNoopStreamlit:
    def progress(self, *args, **kwargs):
        return _EtiquetasNoopProgress()

    def error(self, mensagem, *args, **kwargs):
        logger.warning("[Etiquetas] %s", mensagem)

    def warning(self, mensagem, *args, **kwargs):
        logger.warning("[Etiquetas] %s", mensagem)

    def toast(self, mensagem, *args, **kwargs):
        logger.info("[Etiquetas] %s", mensagem)

    def info(self, mensagem, *args, **kwargs):
        logger.info("[Etiquetas] %s", mensagem)


st = _EtiquetasNoopStreamlit()


def set_streamlit_runtime(streamlit_module) -> None:
    global st
    st = streamlit_module or _EtiquetasNoopStreamlit()
