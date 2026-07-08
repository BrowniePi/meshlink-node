# Phase 7 node-side decisions not specified in Notion

Choices made implementing the dynamic zone-routing table (replacing Phase 3's
static `ZONE_TO_NODE_IP`). The Notion task fixes the contract — table updates
as nodes join/leave, re-syncs each heartbeat interval, unknown zone floods all
nodes — and leaves the mechanism open. Everything else follows that page.

- **Nodes learn the table by gossip over the backhaul, not from the backend.**
  The task says "when a new node announces itself *on the backhaul*". So each
  node broadcasts a tiny announcement ("node N serves zone Z at addr A") to
  every other node once per interval; receivers fold it into their own table
  (`node/backhaul/zone_sync.py`). Keeping it on the mesh means zone routing
  keeps adapting even with the internet uplink down — consistent with the
  Phase 5 rule that the backend is off the message path (touched only for the
  boot key-fetch and heartbeats).

- **Announce interval = the heartbeat interval (`MESHLINK_HEARTBEAT_INTERVAL_S`,
  60 s).** The task says "re-sync at each heartbeat interval", so the two share
  one cadence rather than adding a second knob. Nodes also announce once
  immediately on start so a joiner populates its neighbours without waiting a
  full interval.

- **Entries expire after 3 missed announcements (`MESHLINK_ZONE_ENTRY_TTL_S`,
  default 180 s).** A node that leaves stops announcing; its zone must drop out
  without manual action. Three intervals tolerates isolated packet loss before
  declaring a node gone — the same 3× rule the heartbeat doc suggests for
  marking a node offline.

- **Control frames ride the mesh socket, tagged with a 5-byte magic
  (`MLZS1`).** Announcements share the one backhaul UDP socket but are demuxed
  off the message path by their prefix (`batman_backhaul._recv_loop`), so they
  reach zone-sync and never the relay/phones. A real mesh packet is ≥131 bytes
  starting with a random `msg_id`, so a prefix collision is ~2⁻³² and would
  cost at most one dropped frame. Cheaper and simpler than a second socket/port.

- **Own address is announced explicitly (`MESHLINK_BACKHAUL_ADVERTISE_ADDR`,
  default `10.77.0.<zone_id>`).** A node has to tell peers where to reach it.
  The default follows `scripts/setup_batman.sh`'s zone N ↔ 10.77.0.N scheme;
  Mac/dev nodes on a LAN or loopback override it (and it doubles as the
  self-echo filter, replacing the old table-derived own-endpoint lookup).

- **`MESHLINK_ZONE_TABLE` survives as an operator-pinned seed.** It was the
  Phase 5 dev override for nodes with no batman-adv mesh (e.g. a Mac on
  loopback). Those nodes still have nothing to learn from, so seed entries are
  loaded as never-expiring fallbacks; a fresh learned entry for the same zone
  always wins over the seed. This is the config-level override, distinct from
  the removed Phase 3 `static_zone_table.py` code module.

- **One address per zone, latest-announcer-wins.** A zone can be served by
  several nodes, but forwarding only needs *one* working entry point into it.
  Keeping the most-recently-heard node per zone is enough for the routing
  contract and keeps the table shaped like the static one it replaces.

- **`batman_peer_count` now reflects the live table.** It counts zones other
  than our own currently routable, so it rises and falls as nodes join and age
  out — a free liveness signal for the organiser dashboard.
