"""Plugin subsystem — lightweight Python plugin loader for OpenNote.

Plugins are Python modules discovered in:
  - .opennote/plugins/*.py  (project)
  - ~/.opennote/plugins/*.py (global, respects OPENNOTE_HOME)
  - pip entry-points group ``opennote.plugins``

Each plugin module should expose either:
  - ``register(ctx) -> PluginHooks``  (preferred)
  - ``Plugin`` class with ``register`` method
  - module-level ``hooks`` dict

``ctx`` is a PluginContext (capabilities, notebook, logger, httpx factory).
``PluginHooks`` is a dict-like object with optional keys: ``tools``, ``on_turn_complete``, etc.
"""

from opennote.plugins.loader import PluginLoader, PluginContext, PluginHooks  # noqa: F401

__all__ = ["PluginLoader", "PluginContext", "PluginHooks"]
