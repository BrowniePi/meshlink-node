# WiFi AP deployment procedure (Phase 6)

How to set — and later update — the deployment-wide SSID/passphrase across a
fleet of nodes. Every node must broadcast the exact same values (one ESS,
many radios); a node that drifts becomes an isolated WiFi island phones
cannot roam onto. Values therefore live in **one file, authored once,
pushed unchanged to every node** — never edited per-Pi.

## 1. Author the deployment config (once, centrally)

Create `wifi_deployment.conf` on the deployment machine:

```
ssid=MeshLink-Network
passphrase=<deployment-wide WPA2 passphrase, 8-63 printable ASCII chars>
```

The SSID must be 1–32 bytes. Keep this file with the rest of the event's
deployment secrets (it contains the WPA passphrase).

## 2. Push it to every node

```sh
for host in node-a node-b node-c; do
    ssh "$host" sudo mkdir -p /etc/meshlink
    scp wifi_deployment.conf "$host":/tmp/ && \
        ssh "$host" 'sudo install -m 600 /tmp/wifi_deployment.conf /etc/meshlink/'
done
```

(Any fleet tool — ansible, rsync — works; the invariant is that the *same
bytes* land at `/etc/meshlink/wifi_deployment.conf` on every node.)

## 3. Apply on each node

```sh
sudo scripts/setup_hostapd.sh          # renders + installs hostapd config, starts AP
scripts/verify_ssid_consistency.sh     # self-check: config file ↔ hostapd ↔ on-air SSID
```

`setup_hostapd.sh` is idempotent and refuses to run if the deployment config
is missing or invalid — a node never invents its own SSID.

## 4. Verify fleet-wide consistency

From a laptop scan the venue: all nodes must appear as **one** network name.
Distinct radios under the one SSID are visible at BSSID level:

```sh
sudo iw dev <iface> scan | grep -B5 'SSID: MeshLink-Network' | grep ^BSS
```

Expect one BSS line per node, all carrying the same SSID.

## Updating the SSID/passphrase later

Repeat steps 1–3 with the new values on **all** nodes in one maintenance
window — a half-updated fleet is two disjoint networks. `verify_ssid_consistency.sh`
fails loudly on any node still broadcasting stale values (it compares the
pushed config against both the installed hostapd config and what is actually
on the air), so run it everywhere as the final gate. It should also run at
node boot, after `setup_hostapd.sh`.
