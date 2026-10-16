"""Point OpenSSL at the bundled certificate list on Windows.

A new Windows install keeps only a few roots in its certificate store and
fetches the rest on demand. The embeddable CPython uses OpenSSL, which does
not do that, so HTTPS fails until a bundle is named. Linux keeps the system
store.
"""

from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping
from pathlib import Path


def bundled_ca_path() -> Path:
    """collector/cacert.pem beside the streamdonations package."""
    return Path(__file__).resolve().parent.parent / "cacert.pem"


def use_bundled_ca_bundle(
    bundle: Path | None = None,
    *,
    platform: str | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> str | None:
    """Set SSL_CERT_FILE when this is Windows, the bundle exists, and neither
    SSL_CERT_FILE nor SSL_CERT_DIR is already set.

    Returns the path that was written, or None when nothing changed.
    """
    env = os.environ if environ is None else environ
    system = platform or sys.platform
    if system != "win32":
        return None
    if str(env.get("SSL_CERT_FILE") or "").strip() or str(env.get("SSL_CERT_DIR") or "").strip():
        return None
    path = bundled_ca_path() if bundle is None else Path(bundle)
    if not path.is_file():
        return None
    chosen = str(path)
    env["SSL_CERT_FILE"] = chosen
    return chosen
