# Deploying to Oracle Cloud (Always Free)

This is a reference doc, not something I ran or verified against real Oracle
infrastructure - I have no access to your Oracle account or any server. The
commands and gotchas below are accurate as of this research, but "worked in
this sandbox" isn't a claim I can make for anything past step 1.

## 1. Create the account and instance

1. Go to oracle.com/cloud/free, sign up. You'll need a phone number and a
   credit card for verification - it will not be charged unless you
   explicitly upgrade to a paid account.
2. **Pick your home region carefully at signup.** Always Free resources can
   only be provisioned in your home region, and it can't be changed later
   without a new account. Popular regions (us-ashburn-1, etc.) sometimes
   report "Out of Capacity" for Ampere A1 shapes - if that happens, either
   retry over the next day or two (capacity frees up) or fall back to the
   AMD Always Free shape (VM.Standard.E2.1.Micro), which is smaller (1/8
   OCPU, 1GB RAM) but has near-zero capacity issues and is plenty for this
   app - it's just SQLite + FastAPI, not something that needs 4+ GB of RAM
   unless you build the ChromaDB/curation-agent phase later.
3. **Current Always Free quota (verify against your own console - this
   changed in 2026 and older guides are wrong):** up to 2 OCPUs and 12GB
   RAM total across Ampere A1 (`VM.Standard.A1.Flex`) instances, or 2 AMD
   micro instances. For this project, 1 OCPU / 6GB on a single A1 instance
   is comfortable headroom without using your whole quota.
4. Console → Compute → Instances → Create Instance.
   - Image: Ubuntu 24.04 (or 22.04) - not Oracle Linux, so the systemd/apt
     commands below apply directly.
   - Shape: Ampere `VM.Standard.A1.Flex`, 1 OCPU / 6GB (adjust the sliders).
   - SSH keys: upload your own public key (`~/.ssh/id_ed25519.pub` or
     similar) rather than letting Oracle generate one - simpler if you
     already manage keys locally.
   - Everything else can stay default; Oracle will auto-create a VCN,
     subnet, and internet gateway if you don't have one yet.
5. Once running, note the instance's **public IP** from the instance
   details page.

## 2. Open the ports - both firewall layers

**Layer 1: Oracle's Security List / NSG** (console side)

Compute → Instances → your instance → click the subnet link → Security
Lists → your list → Add Ingress Rules:

| Source CIDR | Protocol | Port | Purpose |
|---|---|---|---|
| 0.0.0.0/0 | TCP | 22 | SSH (usually already open) |
| 0.0.0.0/0 | TCP | 80 | HTTP (Caddy, for the Let's Encrypt challenge) |
| 0.0.0.0/0 | TCP | 443 | HTTPS (Caddy, the actual dashboard traffic) |

Do **not** open 8000 here - the app stays bound to `127.0.0.1` and only
Caddy talks to it directly (see step 4). Nothing but Caddy should be
reachable from the internet.

**Layer 2: the instance's own iptables** (this is the part that trips
almost everyone up on OCI specifically)

SSH in (`ssh ubuntu@<public-ip>`), then:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo apt-get update && sudo apt-get install -y iptables-persistent
sudo netfilter-persistent save
```

The `-I INPUT 6` inserts *before* Oracle's default reject-everything-else
rule rather than appending after it (order matters in iptables). Don't
touch the rule for port 3260 - that's Oracle's iSCSI boot volume, breaking
it can make the instance fail to boot.

Sanity check from your own machine after both layers are done:
`curl -I http://<public-ip>` should get *some* response (even an error)
once Caddy is running - a hang/timeout means one of the two firewall layers
is still blocking it.

## 3. Get the code onto the instance

Cleanest option, since you're already a git user: push this project to a
private GitHub repo from your machine, then on the instance:

```bash
sudo apt-get update && sudo apt-get install -y git python3-venv python3-pip
git clone git@github.com:<you>/sfevents.git
cd sfevents
```

Faster right now, since you already have the project as zip files locally:

```bash
# from your local machine
scp sfevents_project.zip ubuntu@<public-ip>:~
# on the instance
sudo apt-get update && sudo apt-get install -y unzip python3-venv python3-pip
unzip sfevents_project.zip
cd sfevents
```

Either way, then:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python3 -m pytest -q   # confirm it's healthy on this machine before going further
```

## 4. Install Caddy and put it in front of the dashboard

```bash
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy
```

Generate a bcrypt hash for whatever password you want to log in with:

```bash
caddy hash-password --plaintext 'choose-a-real-password-here'
# copy the long $2a$... output for the Caddyfile below
```

Edit `/etc/caddy/Caddyfile`:

```
# If you have a domain pointed at the instance's IP (an A record), use it
# here and Caddy gets you real HTTPS automatically via Let's Encrypt:
your-domain.example.com {
    basicauth {
        kandai $2a$...the hash you just generated...
    }
    reverse_proxy 127.0.0.1:8000
}

# If you DON'T have a domain yet, use the bare IP - you get Basic Auth
# protection but plain HTTP (no TLS) until you point a domain at it:
:80 {
    basicauth {
        kandai $2a$...the hash you just generated...
    }
    reverse_proxy 127.0.0.1:8000
}
```

Use one block or the other, not both. Then:

```bash
sudo systemctl restart caddy
sudo systemctl enable caddy
```

A free option if you don't own a domain: DuckDNS or a similar dynamic-DNS
service will give you a subdomain (e.g. `kandai-events.duckdns.org`)
pointed at your instance's IP, which is enough for Caddy to get a real
Let's Encrypt cert - worth doing over the bare-IP option if you want actual
HTTPS rather than just Basic Auth over plaintext.

## 5. Install the dashboard as a systemd service

Reuse `deploy/sfevents.service` from before - the placeholders
just need Oracle-specific values now:

```bash
# edit deploy/sfevents.service:
#   <YOUR_USER>    -> ubuntu
#   <PROJECT_DIR>  -> /home/ubuntu/sfevents
#   <VENV_PYTHON>  -> /home/ubuntu/sfevents/.venv/bin/python3

sudo cp deploy/sfevents.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sfevents
sudo systemctl status sfevents   # confirm Active: running
curl http://127.0.0.1:8000/              # confirm it responds locally
```

It's already configured to bind `127.0.0.1` only - correct for this setup,
since Caddy is the only thing that should be internet-facing.

## 6. Add the weekly cron fetch

Same as the local self-hosting setup, just with Oracle's paths:

```bash
chmod +x scripts/weekly_fetch.sh
crontab -e
# paste the line from deploy/crontab.txt, with <YOUR_USER> -> ubuntu and
# the path updated to /home/ubuntu/sfevents/scripts/weekly_fetch.sh
```

## 7. Verify end to end

From your own machine (not the instance):

```bash
curl -I https://your-domain.example.com/     # or http://<public-ip>/ if no domain
# expect a 401 Unauthorized (Caddy's Basic Auth prompt) - that's success,
# it means Caddy is reachable and gatekeeping correctly
```

Then open it in a browser, enter the Basic Auth credentials, and confirm
the dashboard loads and shows events.

## What I couldn't verify from this sandbox

Everything past step 1 (account creation) requires an actual Oracle
account, an actual public IP, and an actual browser hitting a real
domain/IP - none of which exist from here. This doc is accurate sysadmin
guidance based on current research, not something I ran end-to-end. Two
places most likely to need troubleshooting on your end: capacity
availability for the A1 shape in your home region, and whichever DNS/domain
path you pick for Caddy's TLS.

## Alternative worth knowing: Tailscale instead of public exposure

If you'd rather not expose *anything* to the public internet at all,
installing Tailscale on the instance and only accessing the dashboard over
your tailnet skips steps 2 and 4 entirely (no Security List changes beyond
SSH, no Caddy, no domain, no Basic Auth needed since only your own devices
can reach it). Slightly more setup on your client devices, meaningfully
less exposure. Worth considering if this is genuinely just for your own
use and not something you want to share a link to.
