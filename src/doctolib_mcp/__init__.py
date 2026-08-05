"""doctolib-mcp — read-only MCP tools over Doctolib's internal JSON API."""

from .client import DoctolibClient, DoctolibError

__all__ = ["DoctolibClient", "DoctolibError"]
