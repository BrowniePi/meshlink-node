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

## macOS dev machines (Internet Sharing backend)

The Pi path above uses hostapd. On a Mac — for developing/testing a node
without Pi hardware, the WiFi twin of the CoreBluetooth BLE backend — AP
bring-up goes through macOS Internet Sharing instead, selected automatically
(`sys.platform == "darwin"`; override with `MESHLINK_AP_BACKEND`).

This is **dev/test parity only**, not a deployment target: enabling Internet
Sharing needs root and takes the built-in Wi-Fi card over as an AP, dropping
whatever network the Mac was joined to.

```sh
# 1. Point at a local deployment config (no /etc/meshlink on a Mac):
export MESHLINK_WIFI_DEPLOYMENT_CONF="$PWD/wifi_deployment.conf"

# 2. Run the node as root so it can configure Internet Sharing. It writes the
#    SSID/passphrase into com.apple.nat.plist and kicks com.apple.NetworkSharing.
MESHLINK_AP_PROVISION=on sudo -E python3 -m node.main
```

If you run the node **without** root, it won't touch networking — it logs the
exact System Settings steps (General > Sharing > Internet Sharing, share to
Wi-Fi, Wi-Fi Options with the deployment SSID/passphrase) and the node still
serves the WiFi listener + BLE. Because Apple gates the real toggle behind a
GUI and the plist schema drifts between releases, treat bring-up as
best-effort: if the phone can't see the SSID, enable Internet Sharing by hand
(the SSID/passphrase are already configured for you).

**Known limitation — single-radio laptops with no spare uplink (2026-07-07):**
tested live on a MacBook whose only network connection is the built-in Wi-Fi
card itself (no Ethernet/Thunderbolt adapter plugged in). `start()` reported
success — the `nat.plist` config is accepted, `com.apple.NetworkSharing`
reports `running`, and `verify_consistency()` passes because it only checks
that the plist carries our SSID — but the phone got `ETIMEDOUT` joining, and
the actual AP radio interface (`ap1`) never came up (`ifconfig ap1` showed
`status: none`). Root cause: macOS Internet Sharing's Wi-Fi hotspot mode
shares an *existing* internet connection from one interface out over
another; it needs a distinct "share from" source (Ethernet, Thunderbolt,
another phone's Personal Hotspot, etc.). A laptop whose only connection is
its own Wi-Fi radio has no valid source, so the daemon accepts the config but
never actually broadcasts — and no GUI toggle fixes this, since the Wi-Fi
chip can't be both the client connection and the AP at once. There is no
software workaround for this in `InternetSharingProvisioner`; it needs a
non-Wi-Fi uplink (an Ethernet/USB-C dongle, or another device's hotspot as
the source) to actually work. Until then, treat the macOS AP backend as
untested/inert on typical single-radio laptops — the WiFi *listener*
(`node/transport/wifi_transport.py`) still works fine bound to the Mac's
normal Wi-Fi IP if you join the same existing network as the phone instead of
trying to host a new SSID from the Mac.

On-air SSID verification is phone-side on macOS (a single radio can't scan for
its own hosted AP); the node's self-check compares the deployment config
against what it wrote into the plist.

## Updating the SSID/passphrase later

Repeat steps 1–3 with the new values on **all** nodes in one maintenance
window — a half-updated fleet is two disjoint networks. `verify_ssid_consistency.sh`
fails loudly on any node still broadcasting stale values (it compares the
pushed config against both the installed hostapd config and what is actually
on the air), so run it everywhere as the final gate. It should also run at
node boot, after `setup_hostapd.sh`.
