"""Per-site adapters.

Each concrete adapter module (yanolja.py, yeogiotae.py, naver.py - added in later
tasks) calls `registry.register_site_client(...)` at import time. Importing this
package is what triggers those registrations, so `registry.client_factory_for()` can
find them.
"""
