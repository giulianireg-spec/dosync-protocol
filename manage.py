#!/usr/bin/env python3
"""Compatibility shim — the implementation now lives in `dosync/manage.py`.

It moved so it ships INSIDE the installed package: `pip install dosync` now
provides it as the `dosync-manage` console script. Running
`python3 manage.py ...` from a clone keeps working exactly as before.

When imported (some repo scripts and tests pull SYMBOLS from here, not just
main) this aliases the module rather than re-exporting a chosen few: the name
resolves to the same module object as `dosync.manage`, so nothing can be missing
and nothing can diverge. New code should import `dosync.manage` directly.
"""
import sys

from dosync import manage as _impl

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
