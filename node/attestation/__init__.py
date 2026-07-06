"""Node-local wire constant for attestation token presentation.

meshlink-core validates tokens (pipeline.AttestationCache) but has no notion
of a message type for delivering one — tokens reach the node "out of band"
per its docstring. On this BLE mesh, out-of-band still means over the same
signed packet envelope, just tagged with a msg_type NodeRelay intercepts
before step 7 (see node/relay.py): the presenter can't be attested yet, so
running its own presentation through the attestation-gated pipeline would be
a chicken-and-egg drop.

Must match meshlink-app's msgTypeAttestation (lib/core/message_factory.dart)
— this is a cross-repo wire-format constant, not a node-internal choice.
"""

MSG_TYPE_ATTESTATION_PRESENT = 0x06
