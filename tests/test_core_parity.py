"""Node-side pipeline parity with the meshlink-core reference.

Replays test/fixtures/parity_vectors.json — the exact vectors the app-side
parity test (meshlink-app/test/core_parity_test.dart) uses — through the
pipeline the node imports via node.core, asserting identical accept/reject
decisions. Since node.core imports the reference implementation rather than
porting it, this test guards the integration (submodule pin + import shim),
not a re-implementation.
"""
import json
from pathlib import Path
from unittest.mock import patch

from node.core import Outcome, RelayPipeline

FIXTURE = Path(__file__).parent / "fixtures" / "parity_vectors.json"


def test_node_pipeline_matches_reference_vectors():
    vectors = json.loads(FIXTURE.read_text())["vectors"]
    fixed_now = vectors[0]["fixed_now"]

    # One pipeline for all vectors, in order — duplicate_msg_id relies on
    # dedup state written by valid_text_deliver, as in the generation run.
    pipeline = RelayPipeline()
    with patch("time.time", return_value=fixed_now):
        for vector in vectors:
            result = pipeline.process(bytes.fromhex(vector["raw_hex"]))
            assert result.outcome.value == vector["outcome"], vector["name"]
            assert result.drop_reason == vector["drop_reason"], vector["name"]


def test_outcome_enum_values_match_wire_strings():
    assert {o.value for o in Outcome} == {"deliver", "relay", "drop"}
