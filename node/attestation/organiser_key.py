"""Organiser public key bootstrap: one backend call per boot, zero on the
message path.

The node fetches GET /attestation/public-key at startup and caches the 64-hex
Ed25519 key to disk, so a later boot with the backend unreachable still
enforces attestation with the cached key. Token verification itself is fully
offline (node/attestation/token_cache.py).
"""
import json
import logging
import urllib.request
from pathlib import Path

log = logging.getLogger("meshlink.attestation")


def load_organiser_pubkey(base_url: str, cache_path: Path,
                          timeout_s: float = 5.0) -> str:
    """Fetch the organiser public key, falling back to the disk cache.

    Raises RuntimeError when neither source is available — a node that cannot
    learn the organiser key cannot validate any token, and running step 7
    open would silently re-open the Sybil hole Phase 5 exists to close.
    """
    url = f"{base_url.rstrip('/')}/attestation/public-key"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            pub_hex = json.load(resp)["public_key"]
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(pub_hex + "\n")
        log.info("organiser public key fetched from %s (cached to %s): %s",
                 url, cache_path, pub_hex)
        return pub_hex
    except OSError as exc:
        if cache_path.exists():
            pub_hex = cache_path.read_text().strip()
            log.warning("backend unreachable (%s) — using cached organiser "
                        "key from %s", exc, cache_path)
            return pub_hex
        raise RuntimeError(
            f"cannot obtain organiser public key: backend fetch failed ({exc}) "
            f"and no cached copy at {cache_path}"
        ) from exc
