"""Proveedores intercambiables del dashboard.

Solo queda el descubrimiento de publicaciones. El análisis editorial y la
entrega se quitaron con el editor: este dashboard es un visor.
"""

from . import base, timelines

__all__ = ["base", "timelines"]
