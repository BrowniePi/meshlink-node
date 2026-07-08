"""Import shim for the vendored meshlink-core package.

meshlink-core is consumed as a pinned git submodule (vendor/meshlink-core)
because its pyproject.toml carries no build-system metadata, so it cannot be
pip-installed — and Phase 2 consumes core without changing it. This module
puts the submodule on sys.path and re-exports the pieces the node uses, so
the rest of the codebase writes `from node.core import RelayPipeline` and
never touches sys.path itself.

No relay-pipeline logic is re-implemented or copied here — everything below
is imported straight from meshlink-core.
"""
import sys
from pathlib import Path

_CORE_ROOT = Path(__file__).resolve().parents[2] / "vendor" / "meshlink-core"
if not (_CORE_ROOT / "pipeline" / "pipeline.py").exists():
    raise ImportError(
        f"meshlink-core submodule missing at {_CORE_ROOT} — "
        "run: git submodule update --init"
    )
if str(_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CORE_ROOT))

from pipeline.message import (  # noqa: E402
    HEADER_SIZE,
    MAX_PACKET,
    MIN_PACKET,
    SIGNATURE_SIZE,
    Message,
    MessageType,
    parse_packet,
)
from capability.token import (  # noqa: E402
    TOKEN_SIZE,
    parse as parse_capability_token,
    pubkey_id,
    revocation_key,
    verify as verify_capability_token,
)
from crypto.sealed import seal  # noqa: E402
from identity import build_signed_packet, load_or_create_identity  # noqa: E402
from location.wire import (  # noqa: E402
    LocationResponsePayload,
    decode_location_beacon,
    decode_location_query,
    decode_location_revoke,
    encode_location_response,
)
from pipeline.attestation_check import AttestationCache  # noqa: E402
from pipeline.pipeline import Outcome, PipelineResult, RelayPipeline  # noqa: E402
from routing.spray_and_wait import split_copies  # noqa: E402
from transport.base import Transport  # noqa: E402

__all__ = [
    "AttestationCache",
    "LocationResponsePayload",
    "MessageType",
    "TOKEN_SIZE",
    "build_signed_packet",
    "decode_location_beacon",
    "decode_location_query",
    "decode_location_revoke",
    "encode_location_response",
    "load_or_create_identity",
    "parse_capability_token",
    "pubkey_id",
    "revocation_key",
    "seal",
    "verify_capability_token",
    "HEADER_SIZE",
    "MAX_PACKET",
    "MIN_PACKET",
    "SIGNATURE_SIZE",
    "Message",
    "Outcome",
    "PipelineResult",
    "RelayPipeline",
    "Transport",
    "parse_packet",
    "split_copies",
]
