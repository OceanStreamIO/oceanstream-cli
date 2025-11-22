"""R2R (Rolling Deck to Repository) provider package.

This subpackage contains the concrete :class:`R2RProvider` class,
archive helpers, and metadata parsers. The public provider class is
re-exported here for convenience so callers can simply import
``oceanstream.providers.r2r.R2RProvider``.
"""

from .r2r import R2RProvider

__all__ = ["R2RProvider"]
