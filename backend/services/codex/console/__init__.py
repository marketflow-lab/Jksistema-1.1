"""Modular implementation of the Codex console facade."""

from .composition import wire

wire()

__all__: list[str] = []
