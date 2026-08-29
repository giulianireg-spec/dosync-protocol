"""DoSync Protocol — the semantic layer between AI agents and physical devices.

Apache 2.0 — github.com/giulianireg-spec/dosync-protocol

This module is the SINGLE SOURCE for both version numbers. Until 2026-07-22
there were three: this file said 0.1.0/0.1, server.py hardcoded 0.4.0 in four
places, and pyproject.toml carried its own copy — three declarations nobody
checked against each other, so `import dosync; dosync.__version__` reported a
version three releases old. pyproject reads the value from here and the server
imports it, so they cannot drift again (and a test asserts it).

The two numbers move independently on purpose:
  __version__           this implementation of the hub  (semver)
  __protocol_version__  the wire contract other implementations must match
"""
__version__ = "0.6.1"
__protocol_version__ = "0.4"
