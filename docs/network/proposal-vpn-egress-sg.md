# Proposal: all routed networks egress via Proton VPN SG

Status: PROPOSED (Class C, Faris decides). Author: oc-net, 2026-10-18. Nothing below has been applied.
UniFi is not code yet: every step is a manual UniFi change by Faris.

## 1. Current state (read-only evidence, 2026-10-18)

Gateway lox-gw-01 (UDM Pro Max, fw 6.0.13.36383). Core switch lox-coresw-01 (USW Pro Max 24, fw 7.5.15) routes
mgt (VLAN 2), dmz (10), shr (20), int (40), stg (60) and reaches the gateway over trn (VLAN 4040, 10.255.253.0/24).
The gateway routes net (1), wifi (110), tru (50), iot (100), gst (120). No static routes, no port forwards, no firewall groups.

VPN client networks: `Proton VPN Egress SG` (10.2.0.2/32, enabled), `MY` (10.3.0.2/32, disabled), `JP` (10.4.0.2/32, disabled).

Traffic routes:

| Route | enabled | interface | target | sources (target_devices) | kill switch |
|---|---|---|---|---|---|
| Proton VPN Egress SG | yes | SG (enabled) | INTERNET | NETWORK Trusted, NETWORK IoT | **off** |
| Proton VPN Egress JP | yes | JP (**disabled**) | INTERNET | NETWORK Trusted | **off** |
| Proton VPN Egress MY | no | MY (disabled) | INTERNET | NETWORK WiFi | on |

Findings:
- **JP/SG overlap on Trusted**: two enabled routes match Trusted; the JP one points at a disabled VPN client with kill switch
  off. Depending on rule order Trusted either uses SG or falls back to plain WAN (leak). Inferred, not tested.
- **SG kill switch is off**: a tunnel drop sends Trusted and IoT to plain WAN today.
- wifi, gst, net and all switch-routed networks egress plain WAN today.

## 2. Option B (traffic route by source) - verdict

Verified from the controller objects:
- A traffic route has destination selectors (`matching_target` INTERNET/DOMAIN/IP/REGION with `domains`, `ip_addresses`,
  `ip_ranges`, `regions`) and one source selector, `target_devices`. `ip_addresses`/`ip_ranges` are **destination** fields.
- All existing `target_devices` entries are `{type: NETWORK, network_id}`. There is no source-CIDR field in the object.

Inferred (Ubiquiti docs could not be fetched from this agent; not tested live):
- `target_devices` accepts ALL_CLIENTS, NETWORK (UniFi network object) or CLIENT (by MAC). No source IP/CIDR.
- CLIENT-by-MAC cannot select switch-routed servers: the gateway sees every routed packet with the core switch's trn MAC,
  and the switch is a UniFi device, not a client.
- NETWORK for DMZ/Shared Services/Internal/Staging/Management-InternalServices: these networks exist as UniFi objects, so the
  UI will likely let you pick them, but the gateway's policy rule is expected to be bound to the network's own bridge
  interface, which does not carry this traffic (it arrives on the trn interface). Expect it to save but not match.

Verdict: **B as "source CIDRs" is not supported by the traffic-route object (verified). B via NETWORK objects for
switch-routed nets is unverified and probably fails (inferred)**. One cheap live test settles it (step 0 below).
If the controller version offers a newer Policy-Based Route / Policy Table with a source IP/subnet field, B works
natively; Faris to check the UI (Settings > Routing).

## 3. Recommendation

Stage it, least invasive first:

- **Step 0 (test B, 10 min, reversible)**: add `Staging` to the SG route (kill switch on). From a stg guest:
  `curl -s https://ifconfig.me` must show a Proton SG IP; then disable the SG VPN client briefly and confirm stg has no
  internet (kill switch). If both pass, B works: use section 4 with all networks as NETWORK targets, no routing move.
- **If step 0 fails: option C** - move L3 for dmz, shr, int, stg from lox-coresw-01 to lox-gw-01 (same subnets, same .1
  gateway IPs; the SVI moves from switch to gateway, VLANs stay trunked to the gateway). Then section 4 applies.
  C also lets the UDM zone firewall enforce flows.yaml. Cost: inter-VLAN traffic hairpins via the 10G gateway uplink,
  one maintenance window, and a **standards rule change** (see section 7).
- **mgt (VLAN 2)**: management network, hard limit. Do not move its routing for this; keep mgt switch-routed and
  **excluded** from the tunnel unless step 0 proves B works for it. Faris decides (section 6).
- Option D (separate VPN router guest on vmbr1) is not recommended: new single point of failure, cannot cover mgt
  (nothing attaches to vmbr0), VPN keys in a guest.

## 4. Rule design (target state)

One route, one tunnel:

- `Proton VPN Egress SG`: matching_target INTERNET, interface SG, **kill switch ON**, sources (NETWORK):
  WiFi, Trusted, IoT, Guest, DMZ, Shared Services, Internal, Staging; Management-Ubiquiti and Management-InternalServices
  only per Faris decision (section 6). Optionally the WG remote-user networks (192.168.4.0/24, 192.168.5.0/29) if their
  internet traffic should also be anonymised.
- **Delete or disable `Proton VPN Egress JP`** (fixes the Trusted overlap). Leave MY disabled; remove WiFi from it so it
  cannot overlap SG if someone re-enables it.
- Local exclusion: INTERNET target applies only to internet-bound traffic; inter-VLAN and local traffic stay local
  (inferred from UniFi behaviour). Verify after change: from int, `traceroute 192.168.70.20` must not leave via the tunnel,
  and the WG peers (192.168.4.x, 192.168.5.2 -> 192.168.71.10) must still work.
- Exclusions (destinations that must use plain WAN): a second traffic route, interface = WAN `Primary`, matching_target
  DOMAIN/IP, same sources. Whether UniFi evaluates it before the INTERNET route is **unverified**: test with one domain first.

### Kill switch
ON for the SG route. Effect: if Proton drops, every included network loses internet (no leak). This includes
OpenClaw (CT 100) if mgt is included - the agent team stops until the tunnel is back. Local/inter-VLAN traffic is unaffected.

### DNS (leak prevention)
- Clients must resolve via the AdGuard guests (hm1-iot-dns01/02 192.168.74.10-11, hm1-wifi-dns01/02 192.168.69.10-11),
  whose own upstream queries then egress through the tunnel (iot is already in SG; wifi is not today -> **wifi AdGuard
  upstreams leak via WAN now**).
- Set each network's DHCP DNS to the AdGuard pair (or keep the gateway as DNS only if its forwarder upstream is the
  tunnel). The gateway's own resolver/forwarder traffic is router-originated and is **not** policy-routed (inferred):
  any network using the gateway as DNS leaks its queries to the ISP resolver. Per-network DHCP DNS could not be read
  with the current tools - Faris to check each network.
- AdGuard upstreams: Proton DNS 10.2.0.1 (only reachable inside the SG tunnel) or DoT/DoH via the tunnel; with kill
  switch on, a tunnel drop then fails DNS closed rather than open.
- Block plain DNS/DoT (53, 853) to the internet from all networks except the AdGuard guests (zone firewall, Class C).
  Note stg template-build 192.168.76.250 uses 1.1.1.1/8.8.8.8 (flows.yaml): through the tunnel that is no IP leak but
  bypasses AdGuard; keep or redirect, Faris decides.
- No resolver guest exists in shr (homelab-infra specs); k3s CoreDNS forwards to the node resolver, so it follows the
  shr DHCP/static DNS setting.
- IPv6: VPN traffic routes are IPv4. If WAN IPv6 is enabled on any network, disable it or block v6 egress, or v6 leaks.
  Not readable with current tools - Faris to check.

## 5. Effects on protected guests (no change to them)

- CT 300 hm1-shr-cicd01 (192.168.70.20) and CT 303 OpenBao (192.168.70.12): internal reachability unchanged (local
  traffic excluded). Their internet egress (Gitea mirrors, GitHub, container registries, vendor image mirrors for
  templates) moves to a shared Proton exit IP: expect Docker Hub anonymous pull rate limits, GitHub API limits and
  some CDNs/captcha blocks; with kill switch, a Proton outage stops pipeline downloads and applies that fetch from
  the internet. Mitigation: exclusion route for registry/GitHub domains or for 192.168.70.20 egress (Faris decides;
  an exclusion reduces anonymity for those destinations).
- CT 100 OpenClaw (mgt): if included, its API calls go via SG; a VPN drop halts the team. Mitigation: exclude mgt or
  exclude the model API domains.

## 6. Management paths (Faris to decide)

- net (VLAN 1, Management-Ubiquiti): devices talk to the controller on the UDM locally (unaffected). Firmware downloads
  and UI cloud/remote access are made by devices and the gateway; gateway-originated traffic is not policy-routed
  (inferred). Recommendation: **exclude net** (or exclude `*.ui.com`, `*.ubnt.com`) - anonymising it adds little
  because the gateway itself talks to Ubiquiti over WAN anyway, and a VPN/kill-switch fault must never cut AP/switch
  firmware or remote management.
- mgt (VLAN 2): PVE host updates and OpenClaw. Recommendation: **exclude** until B is proven for switch-routed nets;
  if included, add exclusions for PVE repos and model APIs.
- Out-of-band: Faris keeps local UDM access from tru (local traffic, unaffected by the route).

## 7. Standards impact

- Option B / step 0: none.
- Option C: rule change in homelab-standards `naming/zones.yaml` (header rule "a guest zone must be routed by
  lox-coresw-01"; `router:` for dmz, shr, int, stg -> lox-gw-01) and any lint/ci that enforces it. Owner of zones.yaml is
  oc-net; that PR is Class C and will be opened only after Faris picks C, in the same window as the UniFi change.
- flows.yaml: no change needed; with C the inter-zone matrix becomes enforceable on the UDM later.

## 8. Rollback

- B: remove the added networks from the SG route (or disable it); re-enable nothing else. Instant.
- C: re-create the SVIs on lox-coresw-01 and set the networks' router back (export UniFi backup first; keep the old
  switch L3 config screenshot). Do it in a window; guests keep IPs, only the .1 owner moves. Revert the zones.yaml PR.
- Kill switch: toggle off restores plain-WAN fallback (leaks) - only as an emergency measure.
- Before any step: UniFi backup (Settings > System > Backup) and note current route settings (section 1 table).

## 9. Risks

- Shared exit IP: rate limits, captchas, geo-blocked services, Let's Encrypt/ACME HTTP-01 is not affected (no inbound
  forwards exist) but outbound ACME API calls go via SG.
- Kill switch = availability coupled to Proton; all included networks lose internet on a tunnel fault.
- Throughput capped by the UDM's WireGuard/OpenVPN performance for the whole house.
- Option C: brief outage of dmz/shr/int/stg routing during the move; inter-VLAN load moves onto the gateway.
- Unverified items: B for switch-routed NETWORK targets, exclusion-route precedence, local-traffic exclusion of
  INTERNET target for WG peers, per-network DNS and IPv6 settings.
