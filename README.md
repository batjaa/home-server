# Home Server

Ansible-based IaC for a home lab. Inspired by [Wolfgang's Channel](https://www.youtube.com/@WolfgangsChannel) and [Jeff Geerling](https://www.youtube.com/@JeffGeerling).

Credits: [notthebee](https://github.com/notthebee) & [geerlingguy](https://github.com/geerlingguy).

## Hosts

| Host | Hardware | IP | What runs here |
|------|----------|----|----|
| `homeserver` | x86_64 (Ubuntu 22.04) | `192.168.50.20` | SWAG, Plex, Jellyfin, Nextcloud, PhotoPrism, *arr stack (Prowlarr/Radarr/Sonarr/SABnzbd/Bazarr/Seerr), monitoring (Prometheus/Grafana/cAdvisor/node_exporter), storage role |
| `tentomon` | Raspberry Pi 4 4GB (Flirc case) | `192.168.50.10` | Pi-hole DNS, Cloudflare DDNS, Cloudflare DNS records, node_exporter |
| `pikvm` | PiKVM | `192.168.50.156` | Remote KVM (out-of-band recovery) |

### MAC Addresses

| Host | MAC |
|------|-----|
| `tentomon` | `dc:a6:32:8f:50:fc` |
| `pikvm` | `e4:5f:01:e4:5a:66` |
| `homeserver` | `a8:a1:59:a2:a7:c5` |

## Network

```
ISP Modem
    │ (WAN)
Asus ZenWifi ← router, NAT, DHCP, WiFi, port forward 80/443 → homeserver
    │ (LAN)                DNS server → tentomon (192.168.50.10)
    │
MikroTik CRS326-24G-2S+RM ← managed switch
    ├── homeserver (192.168.50.20)
    │     ├── SWAG (reverse proxy + SSL)
    │     ├── Plex, Grafana, Home Assistant, etc.
    │     └── all services share this IP, SWAG routes by subdomain
    ├── tentomon   (192.168.50.10)
    │     ├── Pi-hole (DNS + ad blocking)
    │     └── Cloudflare DDNS
    ├── pikvm      (192.168.50.156)
    └── other devices
```

### DNS & Traffic Flow

**From the internet** (`plex.batjaa.site`):
```
User → Cloudflare DNS → your public IP → ZenWifi :443 → homeserver → SWAG → Plex
```

**From inside the network** (`plex.home.local` or `plex.batjaa.site`):
```
Device → Pi-hole DNS → 192.168.50.20 → SWAG → Plex
```

- External: `*.batjaa.site` — Cloudflare DNS + DDNS → SWAG reverse proxy on homeserver
- Internal: `*.home.local` — Pi-hole local DNS → SWAG on homeserver
- Pi-hole also resolves `*.batjaa.site` to `192.168.50.20` directly (split DNS, avoids hairpin NAT)

## Prerequisites (macOS)

```bash
brew install ansible
```

Verify it's working:
```bash
ansible --version
ansible-vault --version
```

If `ansible-vault` is not found, ensure Homebrew's bin is in your PATH:
```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
source ~/.zprofile
```

## Disaster Recovery — tentomon (Raspberry Pi 4)

You just plugged the Pi's SD card into your Mac. Here's how to get it back up.

### 1. Flash the OS

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to flash the latest **Ubuntu Server LTS (arm64)** onto the SD card.

In the Imager settings (gear icon), configure:
- Device: Raspberry Pi 4
- Hostname: `tentomon`
- Username: `tentomon`
- Password: (something temporary — Ansible will set up key-based auth)
- Enable SSH with password authentication
- Locale: `America/Los_Angeles`
- Keyboard layout: `us`
- WiFi: **don't configure** — ethernet only

Eject the SD card, put it back in the Pi, plug ethernet into the CRS326, and power on. First boot takes 2-3 minutes.

### 2. Find the Pi on your network

```bash
# Broadcast ping then check ARP for the Pi's MAC (dc:a6:32:8f:50:fc)
ping -c 1 192.168.50.255
arp -a | grep "dc:a6:32:8f:50:fc"

# Or check the ZenWifi DHCP lease list at router.asus.com
```

### 3. Assign a static IP (manual — router admin)

In the Asus ZenWifi admin panel (`router.asus.com`):
1. Go to **LAN → DHCP Server → Manual Assignment**
2. Bind MAC `dc:a6:32:8f:50:fc` to `192.168.50.10`
3. Reboot the Pi so it picks up the static lease

### 4. Set up SSH access

SSH in with the password you set during flashing:
```bash
ssh tentomon@192.168.50.10
```

Copy your SSH key so future logins are passwordless:
```bash
ssh-copy-id -i ~/.ssh/id_ed25519 tentomon@192.168.50.10
```

Verify key auth works (should not prompt for password):
```bash
ssh tentomon@192.168.50.10 "hostname"
```

Set up passwordless sudo (required for Ansible to work without `-K`):
```bash
ssh -t tentomon@192.168.50.10 "echo 'tentomon ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/tentomon"
```
This will ask for the tentomon password one last time. After this, Ansible can run without prompts.

### 5. Clone and configure the repo

```bash
git clone https://github.com/batjaa/home-server
cd home-server
```

Create the inventory file:
```bash
cp hosts.example hosts
```

Edit `hosts` to include:
```ini
[pi]
tentomon ansible_host=192.168.50.10 ansible_user=tentomon ansible_connection=ssh ansible_ssh_private_key_file=~/.ssh/id_ed25519

[homeserver]
homeserver ansible_host=192.168.50.20 ansible_user=agumon ansible_connection=ssh ansible_ssh_private_key_file=~/.ssh/id_ed25519
```

### 6. Set up Ansible Vault (first time only)

The vault password is stored in your macOS Keychain. The `pass.sh` script reads it automatically whenever Ansible needs it.

```bash
security add-generic-password \
    -a batjaa \
    -s ansible-vault-password \
    -w
```

It will prompt for a password — **remember this**, it encrypts/decrypts all your secrets.

Verify it works:
```bash
security find-generic-password -w -a batjaa -l ansible-vault-password
```

### 7. Set up host variables

```bash
mkdir -p host_vars/tentomon
```

Create `host_vars/tentomon/vars.yml`:
```yaml
# tentomon — Raspberry Pi 4 infrastructure node
username: tentomon
guid: '1000'
host: 'batjaa.site'

enable_cloudflare_ddns: true
enable_pihole: true
```

Create the encrypted secrets file:
```bash
ansible-vault create host_vars/tentomon/secret.yml
```

Add your secrets:
```yaml
password: your-tentomon-user-password
ssh_public_key: "ssh-ed25519 AAAA... your-key-here"
cloudflare_dns_token: your-cloudflare-api-token-here
pihole_password: your-pihole-admin-password
```

Copy your SSH public key to clipboard:
```bash
pbcopy < ~/.ssh/id_ed25519.pub
```

To get a Cloudflare API token:
1. Go to https://dash.cloudflare.com/profile/api-tokens
2. Click **Create Token**
3. Use the **Edit zone DNS** template
4. Under Zone Resources, select **Specific zone** → `batjaa.site`
5. Click **Continue to summary** → **Create Token**
6. Copy the token and paste it above

### 8. Install dependencies

```bash
ansible-galaxy install -r requirements.yml
```

### 9. Run the playbook

```bash
ansible-playbook main.yml -l tentomon
```

### 10. Point network DNS to Pi-hole (manual — router admin)

In the Asus ZenWifi admin panel (`router.asus.com`):
1. Go to **LAN → DHCP Server**
2. Set **DNS Server 1** to `192.168.50.10` (tentomon / Pi-hole)
3. Set **DNS Server 2** to `1.1.1.1` (Cloudflare fallback — keeps internet working if Pi is down)
4. Apply and reboot router

All devices on the network will now use Pi-hole for DNS.

### 11. Local DNS records (automated)

DNS records are managed in `host_vars/tentomon/vars.yml` and deployed by the Pi-hole Ansible role — no manual setup needed.

To add a new record, edit `host_vars/tentomon/vars.yml`:
```yaml
pihole_local_dns:
    - { domain: pihole.home.local, ip: '192.168.50.10' }
    - { domain: newservice.home.local, ip: '192.168.50.20' }
    # ...

pihole_split_dns:
    - { domain: plex.batjaa.site, ip: '192.168.50.20' }
    # ...
```

Then re-run:
```bash
ansible-playbook main.yml -l tentomon --tags="pihole"
```

The `*.home.local` entries are internal only. The `*.batjaa.site` entries are split DNS so internal devices resolve to SWAG on the LAN instead of going out to Cloudflare and back.

### 12. Verify

```bash
ssh tentomon@192.168.50.10

# Check Docker is running
docker ps

# Check DDNS container
docker ps | grep cloudflare-ddns

# Check Pi-hole is running
docker ps | grep pihole

# Verify DNS is updating
dig batjaa.site +short

# Verify Pi-hole is resolving internally
dig @192.168.50.10 plex.batjaa.site +short

# From any device on the network, verify ad blocking
nslookup ads.google.com  # should return 0.0.0.0
```

## Disaster Recovery — homeserver

### 1. Install Ubuntu Server

Boot the homeserver from a USB stick with **Ubuntu Server 24.04 LTS (amd64)** and complete the installer:

- Hostname: `homeserver`
- Username: `batjaa`
- Password: (something temporary — Ansible will set up key-based auth)
- Install OpenSSH server: **yes**
- No snaps needed
- Installer should also create a partition layout — for the OS drive only (not data drives)

After install, log in at the console and confirm it's online:
```
ip a
```

### 2. Find it on the network

From your Mac:
```bash
ping -c 1 192.168.50.255
arp -a | grep "a8:a1:59:a2:a7:c5"   # homeserver MAC
```

Or check the ZenWifi DHCP lease list at `router.asus.com`.

### 3. Bind static IP (manual — router admin)

In the Asus ZenWifi admin panel:
1. **LAN → DHCP Server → Manual Assignment**
2. Bind MAC `a8:a1:59:a2:a7:c5` to `192.168.50.20`
3. Reboot the homeserver to pick up the static lease

### 4. Set up SSH access

SSH in (initial port is 22 on a fresh install):
```bash
ssh batjaa@192.168.50.20
```

Copy your SSH key:
```bash
ssh-copy-id -i ~/.ssh/id_ed25519 batjaa@192.168.50.20
```

Set up passwordless sudo:
```bash
ssh -t batjaa@192.168.50.20 "echo 'batjaa ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/batjaa"
```

### 5. Configure inventory

Make sure `hosts` includes:
```ini
[homeservers]
homeserver ansible_host=192.168.50.20 ansible_user=batjaa ansible_connection=ssh ansible_ssh_private_key_file=~/.ssh/id_ed25519
```

> Note: After the first playbook run, the `geerlingguy.security` role will move SSH to port 100. Update the inventory line to add `ansible_port=100` after the first run.

### 6. Set up host variables

```bash
mkdir -p host_vars/homeserver
```

Create `host_vars/homeserver/vars.yml`:
```yaml
# homeserver — main server (media, monitoring, home automation)
username: batjaa
guid: '1000'
host: 'batjaa.site'

# SSH port (custom, set by geerlingguy.security on first run)
security_ssh_port: 100

# Feature toggles — ordered by execution / dependency. Enable bottom-up.
# Foundation (storage + docker first; everything else depends on these)
enable_storage: false
enable_docker: false

# Infrastructure
enable_msmtp: false
enable_endlessh: false
enable_swag: false
enable_cloudflare_ddns: false

# Monitoring
enable_node_exporter: false
enable_prometheus: false
enable_grafana: false

# Services
enable_homeassistant: false
enable_plex: false
enable_photoprism: false

# Storage drives — locked by serial via /dev/disk/by-id (NOT /dev/sdX)
storage_drives:
    - name: primary
      by_id: ata-ST24000NM000H-3KS103_ZYD0F3TM
      expected_size_gb: 24000
      expected_uuid: 29f42d78-7970-430d-ad01-fb51307dd75b
      mount: /mnt/sda1
      role: data        # never reformatted by role (data preserved)
    - name: backup1
      by_id: ata-WDC_WD40EFRX-68N32N0_WD-WCC7K1XHVJPX
      expected_size_gb: 4000
      expected_uuid: 10c199d6-8f34-4a25-8ecb-1b20b7bf66c4   # pinned; assigned on first format
      mount: /mnt/backup1
      role: backup
    - name: backup2
      by_id: ata-WDC_WD40EFRX-68N32N0_WD-WCC7K7UTX0J4
      expected_size_gb: 4000
      expected_uuid: d7e69dec-0dba-4bf6-b803-3800714b8c25
      mount: /mnt/backup2
      role: backup
    - name: backup3
      by_id: ata-ST4000DM005-2DP166_ZGY0JAYX
      expected_size_gb: 4000
      expected_uuid: f975f821-47aa-4a5b-9d81-268698a73c7d
      mount: /mnt/backup3
      role: backup

# Safety flag — must be explicitly set true to allow drive wipes
confirm_storage_wipe: false
```

This minimal config runs only **system setup, security hardening, and NTP** on the homeserver. After verifying the base is solid, flip flags to `true` one at a time and re-run the playbook to add each service.

Create the encrypted secrets file:
```bash
ansible-vault create host_vars/homeserver/secret.yml
```

Add your secrets:
```yaml
password: your-batjaa-user-password
ssh_public_key: "ssh-ed25519 AAAA... your-key-here"
email_password: your-smtp-password
cloudflare_dns_token: your-cloudflare-api-token-here  # same token as tentomon
```

### 7. Run the playbook

First run uses port 22 (default). After the security role runs, SSH moves to 100.

```bash
# First run on port 22
ansible-playbook main.yml -l homeserver

# After SSH port changes to 100, update inventory:
#   homeserver ansible_host=192.168.50.20 ansible_port=100 ...
# Then for subsequent runs:
ansible-playbook main.yml -l homeserver
```

### 8. Configure port forwarding (manual — router admin)

In the ZenWifi admin panel:
1. **WAN → Virtual Server / Port Forwarding**
2. Forward `80/tcp` and `443/tcp` to `192.168.50.20`
3. Enable

This lets the internet reach SWAG for `*.batjaa.site` services.

### 9. Verify

```bash
ssh -p 100 batjaa@192.168.50.20

# Check containers are running
docker ps

# Visit external service
open https://plex.batjaa.site

# Visit internal service
open http://plex.home.local
```

## Storage Setup (homeserver)

The storage layout uses **mergerfs** to pool drives, with btrfs snapshots for backup integrity. Full design in [`docs/storage-ansible-requirements.md`](docs/storage-ansible-requirements.md).

```
/mnt/storage      = mergerfs(/mnt/storage_cache : /mnt/sda1)   ← primary, 24T + NVMe cache
/mnt/backup_pool  = mergerfs(/mnt/backup1 : /mnt/backup2 : /mnt/backup3)   ← backup, 12T pooled
```

### 1. Run the readiness check (mandatory before any storage op)

The verify script reads drives by serial/by-id, checks sizes, models, UUIDs, SMART health, and that nothing is actively writing to `/mnt/storage`. **Read-only** — safe to run anytime.

```bash
# Copy to homeserver and run
scp -P 100 roles/filesystems/storage/files/verify-readiness.sh batjaa@192.168.50.20:/tmp/
ssh -p 100 batjaa@192.168.50.20 "sudo bash /tmp/verify-readiness.sh"
```

Expected outcomes:
- **All passes** — safe to run the storage playbook
- **Warnings** about backup1/backup2 having existing data — expected, the role will wipe them on first run (data is just a backup of sda, will be re-seeded)
- **Any failure** — stop and investigate before going further

If a drive's serial/UUID has drifted from the inventory in `verify-readiness.sh`, that means a drive was swapped or a controller change reordered things. **Do not run the playbook until the inventory matches reality.**

### 2. Add storage drives to host_vars

Drive serials, sizes, and pinned UUIDs live in `host_vars/homeserver/vars.yml` under `storage_drives:` (see the example earlier in this README). The pinned UUIDs are committed and persistent — don't change them after the first format.

### 3. Phase 2a — Data drive + cache (non-destructive)

This remounts sda from `/mnt/storage` to `/mnt/sda1` and adds a mergerfs union with the NVMe cache. The 4T of data on sda is preserved.

```bash
# Set in host_vars/homeserver/vars.yml:
#   enable_storage: true

ansible-playbook main.yml -l homeserver --tags="storage"
# (storage_phase defaults to "data" — non-destructive)
```

Verify:
```bash
ssh -p 100 batjaa@192.168.50.20

# Both should show the same data
ls /mnt/sda1
ls /mnt/storage

# Should show mergerfs at /mnt/storage
mount | grep storage

# Free space should reflect cache (NVMe ~775GB) + sda (~17T)
df -h /mnt/storage
```

**Stop here and confirm everything works** before moving to 2b. Once confirmed, the data side is done.

### 4. Phase 2b — Backup drives (DESTRUCTIVE)

This wipes and reformats backup1, backup2, backup3 with btrfs `@current` subvolume layout. **Existing data on backup1/backup2 is destroyed** (it's only a backup of sda, will be re-seeded by the first backup run).

```bash
# Add to host_vars/homeserver/vars.yml:
#   confirm_storage_wipe: true

ansible-playbook main.yml -l homeserver --tags="storage" -e storage_phase=backups
```

Verify:
```bash
ssh -p 100 batjaa@192.168.50.20

mount | grep backup
# /mnt/backup1, /mnt/backup2, /mnt/backup3 all mounted with subvol=@current

# After this completes, set confirm_storage_wipe: false again
# so accidental re-runs can't wipe drives.
```

### 5. Phase 3a–3d — Pool, mover, backup, health

After the backup drives exist, layer in:

| Phase | Command | What it does |
|-------|---------|--------------|
| **3a — pool** | `... -e storage_phase=pool` | mergerfs union of `/mnt/backup{1,2,3}` → `/mnt/backup_pool` |
| **3b — mover** | `... -e storage_phase=mover` | systemd timer (daily 02:00) moves cache files >7d old onto sda1 |
| **3c — backup** | `... -e storage_phase=backup` | systemd timer (daily 03:00) rsyncs `/mnt/storage` + `/opt/docker/data` → `/mnt/backup_pool` with btrfs snapshots, 14-day retention |
| **3d — health** | `... -e storage_phase=health` | smartd config, btrfs scrub timers per mount, Prometheus textfile metrics |

All four are idempotent and non-destructive — safe to re-run.

## Disaster Recovery from Backup

If `/opt/docker/data` is wiped (re-install, swapped server) but `/mnt/backup_pool/_docker_data/` is intact, you skip every service's first-run wizard:

1. Boot homeserver, get through Phase 2a (sda mounted at `/mnt/sda1`, mergerfs at `/mnt/storage`)
2. Phase 3a (backup_pool) so `/mnt/backup_pool/_docker_data/` is reachable
3. Stop any containers that auto-started:
   ```bash
   ssh -p 100 batjaa@192.168.50.20 "sudo docker ps -q | xargs -r sudo docker stop"
   ```
4. Restore `/opt/docker/data`:
   ```bash
   ssh -p 100 batjaa@192.168.50.20 "sudo rsync -aHAX --delete /mnt/backup_pool/_docker_data/ /opt/docker/data/"
   ```
5. Re-run the playbook with all `enable_*` flags on:
   ```bash
   ansible-playbook main.yml -l homeserver
   ```

Containers come up reading their original config — API keys, library DBs, Plex/Jellyfin libraries, *arr quality profiles, indexer credentials all preserved. **No wizard click-through.**

## Services

Per-service runbooks (config steps that aren't yet automated, where credentials live, what manual setup is needed) are in [`docs/services.md`](docs/services.md).

## Day-to-day Usage

Run everything (all hosts):
```bash
ansible-playbook main.yml
```

Target a specific host:
```bash
ansible-playbook main.yml -l tentomon
ansible-playbook main.yml -l homeserver
```

Run specific roles:
```bash
ansible-playbook main.yml -l tentomon --tags="cloudflare-ddns"
ansible-playbook main.yml -l homeserver --tags="containers"
```

Edit secrets:
```bash
ansible-vault edit host_vars/tentomon/secret.yml
ansible-vault edit host_vars/homeserver/secret.yml
```
