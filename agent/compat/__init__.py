"""Explicit migration-only compatibility adapters.

New Runtime code must use scoped Context dependencies. Modules in this
package are the only place allowed to bridge old process-global services while
the v2.3A migration is in progress.
"""

__all__ = []
