"""Per-site adapters.

Each concrete adapter module (yanolja.py, yeogiotae.py, naver.py) calls
`registry.register_site_client(...)` at import time. Importing this package is
what triggers those registrations, so `registry.client_factory_for()` can find them.
"""

from . import naver, yanolja, yeogiotae  # noqa: F401  (import triggers register_site_client)
