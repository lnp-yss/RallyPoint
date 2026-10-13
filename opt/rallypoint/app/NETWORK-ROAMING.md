# Automatic local-network reconnection

The host is `rallypoint.local`; Court 2 is `rallypoint-court2.local`. Open http://rallypoint.local/login from a computer or phone on the same network.

Both Pis use Avahi/mDNS and DHCP. Court 2’s persistent SSH tunnel resolves the host name on every reconnection, with keepalive failure detection and automatic service restart. Host court-management connections resolve Court 2’s hostname on every request. SSH host keys remain pinned; changing IP addresses does not disable identity verification. Score uploads use the hostname and retain the club URL suffix. Future downloaded device configurations use the host’s configured canonical URL.

To move networks, connect both Pis and your browser device to the new local network. Ethernet requires no new IP setting. A new Wi-Fi network requires its SSID/password to be configured on each Pi first. Allow approximately a minute for disconnected tunnels to be detected and recreated. No internet connection is required.

Guest Wi-Fi, client isolation, separate VLANs/subnets without mDNS forwarding, and duplicate hostnames can prevent discovery. Keep every Pi hostname unique. If discovery is blocked, adjust the network or use a router DHCP reservation. This configuration does not automatically learn Wi-Fi credentials or expose the server over the internet.

Verified on 2026-10-06: host and client resolve each other by hostname; client tunnel, scoreboard, Avahi are active; host login responds through its hostname; both paired management agents and GPIO are online. Court assignments were preserved (Court 1: Dink With Grace; Court 2: AmaPickle).

A physical move to another router was not performed. Current-network hostname reconnection was verified. Configuration backups: `/opt/rallypoint/backups/before-hostname-network-20261006-143225` on Court 2 and `/opt/rallypoint/backups/before-hostname-network-20261006-143255` on the host. Before-update full host backup: `/opt/rallypoint/backups/automatic/rallypoint-20261006-063248-886772`.


## Editable hostname / IP fallback

Sign in as **Host Administrator**, open **Admin Settings → Edit Network Settings** (also under Move courts), or visit `/network-settings`. Club administrators and operators cannot change shared host networking.

Enter bare hostnames or private LAN IPv4 addresses, without `http://`, paths or ports:

| Connection | Hostname | Current IP fallback |
|---|---|---|
| Host server used by clients | rallypoint.local | 192.168.2.204 |
| Court 2 used by host management | rallypoint-court2.local | 192.168.2.124 |

Use **CHECK CONNECTION** first. It runs in the background and verifies the host web service and the existing pinned SSH device keys. It makes no configuration changes. A checked draft remains in the form for APPLY.

Finish or end all court matches before **APPLY NETWORK SETTINGS**. The worker checks every proposed address before reconnecting any device. Each Pi backs up its environment and agent configuration, preserves club assignment, tokens, match files and queued score updates, and reverts its own settings if reconnection fails. Pending updates are allowed for an idle court so hostname failure can be repaired without discarding its outbox. Already reconnected clients retain their successful settings if another device subsequently fails; correct the address and retry.

These settings change application connection destinations, not the Pi's DHCP/static IP or Wi-Fi credentials. Reserve IPs in your router if using IP mode; use the new LAN addresses after moving networks. Existing SSH trust remains tied to the paired physical devices through HostKeyAlias. URLs used in future court moves and downloaded device configurations follow the chosen host address.

Deployment backups: host full `/opt/rallypoint/backups/automatic/rallypoint-20261006-092656-281612`, host configuration `/opt/rallypoint/backups/before-network-settings-20261006-172752`, Court 2 configuration `/opt/rallypoint/backups/before-network-settings-20261006-172808`.

Both hostname and current-IP connection checks were verified on the live devices. An actual address change was not forced while Court 2 was playing. Automated tests cover access control, validation, busy protection, serialization, ordering and per-device rollback.
