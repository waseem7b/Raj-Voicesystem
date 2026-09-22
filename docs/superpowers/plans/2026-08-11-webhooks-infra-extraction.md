# Webhooks Infra Extraction Implementation Plan

> **v2 (2026-08-11):** Three independent audits (security / ops-correctness / agent-onboarding)
> reviewed v1 before execution. All accepted findings are folded into execution as the
> deltas listed in "Audit revisions (v2)" at the bottom of this file. Where a task below
> conflicts with a delta, **the delta wins.**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the shared webhook infrastructure out of the Voicsito repo into its own standalone repo, make the VPS a read-only clone of it, and add an hourly auto-committing snapshot of live state so any agent's mistake is diffable and revertible.

**Architecture:** Three layers with distinct jobs. (1) **Intent** — a private GitHub repo `waseem7b/webhooks-infra`, cloned locally at `~/CLAUDSITO/webhooks-infra`, edited from any machine, reviewed via git history. (2) **Deployment** — `/opt/webhooks-infra/` on the VPS is a *read-only* clone of that repo (read-only deploy key), updated by `git pull`. Nothing on the VPS can push, so no agent can corrupt shared source from the host. (3) **Undo** — `/opt/webhooks-infra-state/`, a VPS-local git repo that snapshots the live nginx includes, systemd units, and a generated live inventory every hour. Any unauthorized or broken change shows up as a commit diff and reverts with `git checkout`.

**Tech Stack:** bash, git, systemd (service + timer), nginx, GitHub CLI (`gh`), SSH deploy keys.

---

## Context an engineer needs before starting

**Current state (verified 2026-08-11):**

| Fact | Value |
|---|---|
| Infra source today | `~/CLAUDSITO/Voicsito/infra/webhooks/` (subfolder of an unrelated project — the defect this plan fixes) |
| VPS copy today | `/opt/webhooks-infra/` — a plain `rsync` target, **not** a git repo, files owned by uid `501` (leaked from the Mac) |
| Live projects | `voicsito` (port 8081), `ashley` (port 8082) — both returning HTTP 200 on `/health` |
| SSH access | `ssh voicsito-vps-direct` (72.60.175.24, key `~/.ssh/voicsito_vps`). The `voicsito-vps` alias uses Tailscale (100.105.84.1) and was **timing out** — use `-direct` |
| git on VPS | 2.43.0, present |
| GitHub account | `waseem7b`, no orgs. Repo will be `waseem7b/webhooks-infra`, **private** |

**Blast radius: zero for running services.** Both systemd units `ExecStart` from `/opt/webhooks/<project>/venv/`, and nothing under `/etc/` references `/opt/webhooks-infra` except two comment lines. Replacing the infra directory cannot stop a webhook. No task in this plan edits an nginx include, a systemd unit for a project, or restarts a project service.

**Known drift to fix in Task 1:** the VPS `REGISTRY.md` is stale — it lists only voicsito and claims "next free 8082", but ashley has been live on 8082 since June. The **local** copy at `Voicsito/infra/webhooks/REGISTRY.md` is correct and is the one to seed the new repo with. Every other file is byte-identical between local and VPS (verified by md5).

**The ownership model this plan encodes:** a project owns `/opt/webhooks/<project>/` and any file named `<project>.*`. It touches nothing else. `add-project.sh` already enforces this on creation (`add-project.sh:40-46` aborts if the project's nginx include or unit already exists). This plan adds the after-the-fact detection layer for edits.

---

## File Structure

**New repo `~/CLAUDSITO/webhooks-infra/` (mirrored to `/opt/webhooks-infra/`):**

| File | Responsibility |
|---|---|
| `README.md` | The operator/agent guide. Gains an ownership-rules section and the new git workflow. Moved verbatim otherwise. |
| `REGISTRY.md` | Hand-maintained intent: port → project, owner, upstream provider. Seeded from the *local* (correct) copy. |
| `BRIEFING.md` | The copy-paste prompt for a new agent. Rewritten for the repo-based model. |
| `.gitignore` | Excludes `__pycache__`, `.DS_Store`. |
| `bin/bootstrap.sh` | One-time host setup. Step 6 changes from `rsync` to `git clone`. |
| `bin/add-project.sh` | Provisions a slot. Stops editing `REGISTRY.md` in place (the clone is read-only); prints the row to paste instead. |
| `bin/status.sh` | Read-only live snapshot. Unchanged. |
| `bin/snapshot-state.sh` | **New.** Copies live nginx/systemd state into the state repo, regenerates `LIVE.md`, auto-commits on change. |
| `bin/install-state-tracking.sh` | **New.** One-time installer for the state repo + systemd timer. Idempotent. |
| `templates/nginx-site.template` | Unchanged. |
| `templates/systemd-unit.template` | Unchanged. |
| `templates/snapshot.service` | **New.** systemd oneshot unit that runs `snapshot-state.sh`. |
| `templates/snapshot.timer` | **New.** Hourly timer with `Persistent=true`. |

**Voicsito repo changes:**

| File | Change |
|---|---|
| `infra/webhooks/` | **Deleted** (entire directory — 8 files) |
| `docs/webhook-infrastructure.md` | Rewritten as a pointer to the new repo; internal `../infra/webhooks/` links removed |
| `CLAUDE.md` | Doc-list line for webhook infrastructure updated to name the new repo |

**VPS layout after this plan:**

```
/opt/webhooks-infra/          ← git clone (read-only deploy key). Source of truth for docs/scripts.
/opt/webhooks-infra-state/    ← VPS-local git repo. The undo button. Never pushed.
  ├── nginx/                  ← copy of /etc/nginx/sites.d/webhooks/
  ├── nginx-vhost.conf        ← copy of /etc/nginx/sites-available/webhooks-c7a
  ├── systemd/                ← copy of /etc/systemd/system/*-webhooks.service
  └── LIVE.md                 ← generated inventory: project, port, unit state, health
/opt/webhooks/<project>/      ← per-project app code (unchanged by this plan)
```

---

### Task 1: Create the standalone repo and local clone

**Files:**
- Create: `~/CLAUDSITO/webhooks-infra/` (new git repo, seeded from `Voicsito/infra/webhooks/`)
- Create: `~/CLAUDSITO/webhooks-infra/.gitignore`

- [ ] **Step 1: Create the local project folder and copy the infra files in**

The Voicsito copy is the accurate one (the VPS `REGISTRY.md` is stale). Copy from local, not from the VPS.

```bash
mkdir -p ~/CLAUDSITO/webhooks-infra
cp -R ~/CLAUDSITO/Voicsito/infra/webhooks/. ~/CLAUDSITO/webhooks-infra/
ls -R ~/CLAUDSITO/webhooks-infra
```

Expected: `BRIEFING.md README.md REGISTRY.md bin templates`, with `bin/` holding `add-project.sh bootstrap.sh status.sh` and `templates/` holding `nginx-site.template systemd-unit.template`.

- [ ] **Step 2: Add a .gitignore**

Create `~/CLAUDSITO/webhooks-infra/.gitignore`:

```gitignore
__pycache__/
*.pyc
.DS_Store
```

- [ ] **Step 3: Verify the scripts kept their executable bit**

```bash
ls -l ~/CLAUDSITO/webhooks-infra/bin/
```

Expected: all three `.sh` files show `-rwxr-xr-x`. If not:

```bash
chmod +x ~/CLAUDSITO/webhooks-infra/bin/*.sh
```

- [ ] **Step 4: Initialise the repo and commit**

```bash
cd ~/CLAUDSITO/webhooks-infra
git init -b main
git add -A
git commit -m "chore: extract shared webhook infra from the Voicsito repo

Moved verbatim from Voicsito/infra/webhooks/. Shared infrastructure has no
business living inside one project's repo — every other project that mounts
on webhooks.c7a.io had to reach into Voicsito to read its own operator guide.

REGISTRY.md is seeded from the local copy, which is the accurate one: the
VPS copy had drifted and still omitted ashley (live on 8082 since June)."
```

- [ ] **Step 5: Create the private GitHub repo and push**

```bash
cd ~/CLAUDSITO/webhooks-infra
gh repo create webhooks-infra --private --source=. --remote=origin --push
git remote -v
```

Expected: `origin  https://github.com/waseem7b/webhooks-infra.git (fetch)` and `(push)`.

- [ ] **Step 6: Confirm it exists on GitHub**

```bash
gh repo view waseem7b/webhooks-infra --json name,visibility,defaultBranchRef
```

Expected: `{"defaultBranchRef":{"name":"main"},"name":"webhooks-infra","visibility":"PRIVATE"}`

---

### Task 2: Write the ownership rules and git workflow into the README

**Files:**
- Modify: `~/CLAUDSITO/webhooks-infra/README.md` (insert after the "What this is" section, before "Where things live on the VPS")

- [ ] **Step 1: Insert the ownership section**

Add this block immediately after the `## What this is` bullet list:

```markdown
## Rule zero — stay in your lane

Multiple projects and multiple AI agents share this host. Damage here takes
down *other people's* production webhooks, so the boundary is absolute:

**You may create, edit, or delete only:**
- `/opt/webhooks/<your-project>/` — your code, your venv, your `.env`
- `/etc/nginx/sites.d/webhooks/<your-project>.conf`
- `/etc/systemd/system/<your-project>-webhooks.service`
- your own row in `REGISTRY.md`

**You may never touch** another project's directory, include file, unit file,
or registry row; the main vhost at `/etc/nginx/sites-available/webhooks-c7a`;
or anything under `/etc/letsencrypt/`.

**Never hand-write the nginx include or the systemd unit.** Run
`sudo /opt/webhooks-infra/bin/add-project.sh` — it picks a free port, renders
both files from the templates, validates nginx before reloading, and refuses
to overwrite an existing project.

Every change to the live config is snapshotted hourly into
`/opt/webhooks-infra-state/` (a git repo). If something breaks, `git -C
/opt/webhooks-infra-state log -p` shows exactly what changed and when, and
`git checkout` puts it back. Assume your edits are recorded, because they are.

## How this folder is updated

`/opt/webhooks-infra/` on the VPS is a **read-only clone** of
`github.com/waseem7b/webhooks-infra`. It cannot push. Do not edit files here —
your changes will block the next pull.

To change a doc, script, or template:

```bash
# on your own machine
cd ~/CLAUDSITO/webhooks-infra
git pull
# ... make the edit, commit ...
git push

# then on the VPS
ssh voicsito-vps-direct 'git -C /opt/webhooks-infra pull'
```
```

- [ ] **Step 2: Fix the stale "Source of truth" section at the bottom**

Replace the entire `## Source of truth` section (currently the last section, telling readers the source lives in the Voicsito repo) with:

```markdown
## Source of truth

`github.com/waseem7b/webhooks-infra` (private). `/opt/webhooks-infra/` on the
VPS is a read-only clone of it — pull to update, never edit in place.

Live *running* state is a separate thing from this repo's *intent*: see
`/opt/webhooks-infra-state/LIVE.md` on the host for what is actually mounted,
regenerated hourly.
```

- [ ] **Step 3: Rewrite BRIEFING.md for the new model**

Replace the whole of `~/CLAUDSITO/webhooks-infra/BRIEFING.md` with:

```markdown
# Briefing for AI agents

Paste this into any agent that needs a webhook endpoint. It is the whole prompt.

```
We already have a shared webhook host. Do NOT buy a domain or set up nginx/TLS.

https://webhooks.c7a.io fronts every Cloud 7 webhook. Routing is path-prefix:
/<project>/* → 127.0.0.1:<port> on the VPS, with the prefix stripped before it
reaches your app. TLS is nginx + certbot, already handled. Adding a project is
one nginx include + one systemd unit, provisioned by a script.

Read the guide first — it is a private GitHub repo:
  gh repo clone waseem7b/webhooks-infra
  (or on the host: cat /opt/webhooks-infra/README.md)

SSH: ssh voicsito-vps-direct   (root@72.60.175.24, key ~/.ssh/voicsito_vps)

Provision your slot:  sudo /opt/webhooks-infra/bin/add-project.sh
Check what's mounted:  /opt/webhooks-infra/bin/status.sh
Live inventory + history:  /opt/webhooks-infra-state/LIVE.md

RULE ZERO: you touch only /opt/webhooks/<your-project>/ and files named
<your-project>.*. Never another project's code, include, unit, or the main
vhost. Every config change is snapshotted hourly into a git repo on the host.

Conventions: bind 127.0.0.1 (never 0.0.0.0); serve GET /health -> {"ok": true};
verify the upstream's webhook signature and fail CLOSED; secrets only in
/opt/webhooks/<project>/.env at chmod 600; log JSON to stdout; keep handlers
under 10s.

Verify when done:  curl -fsS https://webhooks.c7a.io/<project>/health
Logs:              journalctl -u <project>-webhooks -f
```
```

- [ ] **Step 4: Commit**

```bash
cd ~/CLAUDSITO/webhooks-infra
git add README.md BRIEFING.md
git commit -m "docs: add rule zero (project ownership boundary) and the git-pull workflow"
git push
```

---

### Task 3: Adapt add-project.sh to the read-only clone

**Files:**
- Modify: `~/CLAUDSITO/webhooks-infra/bin/add-project.sh:116-128` (the REGISTRY.md update block)

The clone on the VPS is read-only. An in-place `REGISTRY.md` edit leaves a dirty working tree that blocks the next `git pull`. Print the row instead.

- [ ] **Step 1: Replace the registry-update block**

Replace lines 116-128 (everything from `# Update REGISTRY.md` through the closing `fi` of that block) with:

```bash
# REGISTRY.md lives in the git repo, and this clone is read-only — editing it
# here would leave a dirty tree and block the next `git pull`. Print the row
# for the operator to commit upstream instead.
REGISTRY_ROW="| $PORT | \`/$PROJECT/*\` | $PROJECT | (your repo) | (upstream) | $DESC |"
```

- [ ] **Step 2: Surface the row in the closing instructions**

In the final `echo` block (the "Next steps" section at the end of the file), change the numbered steps to include the registry step. Replace the `Next steps` lines with:

```bash
echo "  Next steps:"
echo "    1. Drop code into $PROJECT_DIR/src/"
echo "    2. Drop secrets into $PROJECT_DIR/.env"
echo "    3. systemctl enable --now $PROJECT-webhooks"
echo "    4. curl https://webhooks.c7a.io/$PROJECT/health"
echo ""
echo "    5. Record it upstream — add this row to REGISTRY.md in"
echo "       github.com/waseem7b/webhooks-infra, then push:"
echo ""
echo "       $REGISTRY_ROW"
echo ""
echo "       (the live inventory at /opt/webhooks-infra-state/LIVE.md"
echo "        updates itself within the hour either way)"
```

- [ ] **Step 3: Syntax-check the script**

```bash
bash -n ~/CLAUDSITO/webhooks-infra/bin/add-project.sh && echo "SYNTAX OK"
```

Expected: `SYNTAX OK`

- [ ] **Step 4: Confirm no stale references to the removed variables**

```bash
grep -n "REGISTRY\|awk" ~/CLAUDSITO/webhooks-infra/bin/add-project.sh
```

Expected: only the `REGISTRY="$INFRA_DIR/REGISTRY.md"` definition near the top (now unused but harmless as documentation of where it lives) and the new `REGISTRY_ROW` lines. **No `awk` block, no `mktemp`, no `mv "$TMP"`.** If the `REGISTRY=` line reads as confusing, delete it.

- [ ] **Step 5: Commit**

```bash
cd ~/CLAUDSITO/webhooks-infra
git add bin/add-project.sh
git commit -m "fix(add-project): print the registry row instead of editing the read-only clone"
git push
```

---

### Task 4: Convert bootstrap.sh from rsync to git clone

**Files:**
- Modify: `~/CLAUDSITO/webhooks-infra/bin/bootstrap.sh:122-128` (step 6)

- [ ] **Step 1: Replace step 6**

Replace the `[6/7] Installing infra to $INFRA_DEST ...` block (lines 122-128, the `rsync -a --delete` invocation and its two trailing echoes) with:

```bash
echo ""
echo "[6/7] Installing infra to $INFRA_DEST (git clone) ..."
INFRA_REPO="git@github.com:waseem7b/webhooks-infra.git"
if [ -d "$INFRA_DEST/.git" ]; then
  git -C "$INFRA_DEST" pull --ff-only
  echo "  ✓ pulled latest into $INFRA_DEST"
else
  if [ ! -f /root/.ssh/webhooks_infra_deploy ]; then
    echo "  ✗ No deploy key at /root/.ssh/webhooks_infra_deploy."
    echo "    Generate one, add the .pub to the repo's Deploy Keys (read-only),"
    echo "    then re-run. See README.md → 'How this folder is updated'."
    exit 1
  fi
  rm -rf "$INFRA_DEST"
  GIT_SSH_COMMAND="ssh -i /root/.ssh/webhooks_infra_deploy -o IdentitiesOnly=yes" \
    git clone "$INFRA_REPO" "$INFRA_DEST"
  echo "  ✓ cloned $INFRA_REPO → $INFRA_DEST"
fi
chmod +x "$INFRA_DEST"/bin/*.sh
echo "  ✓ Future operators: read $INFRA_DEST/README.md"
```

- [ ] **Step 2: Syntax-check**

```bash
bash -n ~/CLAUDSITO/webhooks-infra/bin/bootstrap.sh && echo "SYNTAX OK"
```

Expected: `SYNTAX OK`

- [ ] **Step 3: Confirm rsync is gone**

```bash
grep -n "rsync" ~/CLAUDSITO/webhooks-infra/bin/bootstrap.sh || echo "NO RSYNC — correct"
```

Expected: `NO RSYNC — correct`

- [ ] **Step 4: Commit**

```bash
cd ~/CLAUDSITO/webhooks-infra
git add bin/bootstrap.sh
git commit -m "feat(bootstrap): install infra by git clone instead of rsync from a dev machine"
git push
```

---

### Task 5: Build the state-tracking undo button

**Files:**
- Create: `~/CLAUDSITO/webhooks-infra/bin/snapshot-state.sh`
- Create: `~/CLAUDSITO/webhooks-infra/bin/install-state-tracking.sh`
- Create: `~/CLAUDSITO/webhooks-infra/templates/snapshot.service`
- Create: `~/CLAUDSITO/webhooks-infra/templates/snapshot.timer`

- [ ] **Step 1: Write the snapshot script**

Create `~/CLAUDSITO/webhooks-infra/bin/snapshot-state.sh`:

```bash
#!/bin/bash
# ────────────────────────────────────────────────────────────────────
# Snapshot the LIVE webhook config into a local git repo and commit
# on change. This is the undo button: every edit anyone (or any agent)
# makes to a project's nginx include or systemd unit becomes a diff.
#
# Read-only with respect to the live system. Copies out, never in.
# Restore is a deliberate manual act:
#   git -C /opt/webhooks-infra-state log -p nginx/ashley.conf
#   cp /opt/webhooks-infra-state/nginx/ashley.conf /etc/nginx/sites.d/webhooks/
#   nginx -t && systemctl reload nginx
# ────────────────────────────────────────────────────────────────────

set -euo pipefail

STATE_DIR="/opt/webhooks-infra-state"
SITES_DIR="/etc/nginx/sites.d/webhooks"
VHOST="/etc/nginx/sites-available/webhooks-c7a"
DOMAIN="webhooks.c7a.io"

mkdir -p "$STATE_DIR/nginx" "$STATE_DIR/systemd"

if [ ! -d "$STATE_DIR/.git" ]; then
  git -C "$STATE_DIR" init -q
  git -C "$STATE_DIR" config user.email "infra@c7a.io"
  git -C "$STATE_DIR" config user.name "webhooks-infra snapshot"
fi

# nginx per-project includes (mirror exactly, including deletions)
rsync -a --delete "$SITES_DIR/" "$STATE_DIR/nginx/"

# main vhost
[ -f "$VHOST" ] && cp "$VHOST" "$STATE_DIR/nginx-vhost.conf"

# systemd units (clear first so a removed project's unit disappears here too)
rm -f "$STATE_DIR"/systemd/*.service
for u in /etc/systemd/system/*-webhooks.service; do
  [ -f "$u" ] && cp "$u" "$STATE_DIR/systemd/"
done

# Generated live inventory — what is ACTUALLY mounted right now.
{
  echo "# Live state — regenerated by snapshot-state.sh. Do not edit."
  echo
  echo "Last snapshot: $(date -Is)"
  echo
  echo "| Project | Port | Unit | Health |"
  echo "|---|---|---|---|"
  for f in "$SITES_DIR"/*.conf; do
    [ -f "$f" ] || continue
    name=$(basename "$f" .conf)
    port=$(grep -oE '(localhost|127\.0\.0\.1):[0-9]+' "$f" | head -1 | cut -d: -f2)
    unit=$(systemctl is-active "$name-webhooks.service" 2>/dev/null || echo "absent")
    health=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
             "https://$DOMAIN/$name/health" 2>/dev/null || echo "---")
    echo "| $name | ${port:-?} | $unit | $health |"
  done
} > "$STATE_DIR/LIVE.md"

git -C "$STATE_DIR" add -A
if git -C "$STATE_DIR" diff --cached --quiet; then
  exit 0    # nothing changed; no empty commits
fi
git -C "$STATE_DIR" commit -q -m "snapshot: $(date -Is)"
echo "committed a config change"
```

Note: `LIVE.md` carries a timestamp that changes every run, so it alone would produce an hourly commit even when nothing changed. Step 2 handles that.

- [ ] **Step 2: Keep timestamp-only churn out of git history**

`LIVE.md` must be visible on the host but must not manufacture a commit every hour. Add it to the state repo's exclude file rather than tracking it. Insert this immediately after the `git ... init -q` block in `snapshot-state.sh`:

```bash
# LIVE.md is regenerated every run with a fresh timestamp — keep it on disk
# for humans but out of git, so commits mean "config actually changed".
printf 'LIVE.md\n' > "$STATE_DIR/.git/info/exclude"
```

- [ ] **Step 3: Write the systemd unit templates**

Create `~/CLAUDSITO/webhooks-infra/templates/snapshot.service`:

```ini
[Unit]
Description=Snapshot live webhooks.c7a.io config into /opt/webhooks-infra-state
After=network-online.target nginx.service

[Service]
Type=oneshot
ExecStart=/opt/webhooks-infra/bin/snapshot-state.sh
```

Create `~/CLAUDSITO/webhooks-infra/templates/snapshot.timer`:

```ini
[Unit]
Description=Hourly snapshot of live webhooks.c7a.io config

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Write the one-time installer**

Create `~/CLAUDSITO/webhooks-infra/bin/install-state-tracking.sh`:

```bash
#!/bin/bash
# ────────────────────────────────────────────────────────────────────
# One-time (idempotent) install of the config snapshot timer.
# Safe to re-run: overwrites the unit files, re-enables the timer.
# ────────────────────────────────────────────────────────────────────

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "✗ Run as root: sudo bash $0"
  exit 1
fi

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

install -m 644 "$INFRA_DIR/templates/snapshot.service" \
  /etc/systemd/system/webhooks-snapshot.service
install -m 644 "$INFRA_DIR/templates/snapshot.timer" \
  /etc/systemd/system/webhooks-snapshot.timer

systemctl daemon-reload
systemctl enable --now webhooks-snapshot.timer

# Take the first snapshot immediately so there is a baseline to diff against.
systemctl start webhooks-snapshot.service

echo "✓ snapshot timer installed and baseline captured"
systemctl list-timers webhooks-snapshot.timer --no-pager
```

- [ ] **Step 5: Syntax-check both scripts and commit**

```bash
chmod +x ~/CLAUDSITO/webhooks-infra/bin/snapshot-state.sh ~/CLAUDSITO/webhooks-infra/bin/install-state-tracking.sh
bash -n ~/CLAUDSITO/webhooks-infra/bin/snapshot-state.sh && bash -n ~/CLAUDSITO/webhooks-infra/bin/install-state-tracking.sh && echo "SYNTAX OK"
cd ~/CLAUDSITO/webhooks-infra
git add bin/snapshot-state.sh bin/install-state-tracking.sh templates/snapshot.service templates/snapshot.timer
git commit -m "feat: hourly git snapshot of live nginx/systemd config as an undo button"
git push
```

Expected: `SYNTAX OK`

---

### Task 6: Cut the VPS over to the clone

**Files:**
- Create on VPS: `/root/.ssh/webhooks_infra_deploy` (+ `.pub`)
- Replace on VPS: `/opt/webhooks-infra/` (backed up first, not deleted)
- Create on VPS: `/opt/webhooks-infra-state/`, `/etc/systemd/system/webhooks-snapshot.{service,timer}`

**Nothing in this task touches a project's nginx include, unit file, or running service.**

- [ ] **Step 1: Record the pre-cutover health baseline**

```bash
ssh voicsito-vps-direct 'curl -s -o /dev/null -w "voicsito:%{http_code} " https://webhooks.c7a.io/voicsito/health; curl -s -o /dev/null -w "ashley:%{http_code}\n" https://webhooks.c7a.io/ashley/health'
```

Expected: `voicsito:200 ashley:200`. **If either is not 200, stop and investigate before continuing** — you need a clean baseline to attribute any later failure.

- [ ] **Step 2: Generate a read-only deploy key on the VPS**

```bash
ssh voicsito-vps-direct 'ssh-keygen -t ed25519 -f /root/.ssh/webhooks_infra_deploy -N "" -C "webhooks-infra deploy key (read-only) — voicsito-vps"; cat /root/.ssh/webhooks_infra_deploy.pub'
```

Expected: a single `ssh-ed25519 AAAA... webhooks-infra deploy key ...` line. Copy it.

- [ ] **Step 3: Register it on the repo as read-only**

Paste the public key from Step 2 into the command below (replace `<PUBKEY>`):

```bash
gh repo deploy-key add /dev/stdin --repo waseem7b/webhooks-infra --title "voicsito-vps (read-only)" <<< '<PUBKEY>'
gh repo deploy-key list --repo waseem7b/webhooks-infra
```

Expected: the key listed, and **read-only** — do not pass `--allow-write`. Read-only is the point: the VPS can never push, so no agent on the host can corrupt shared source.

- [ ] **Step 4: Verify the key authenticates before destroying anything**

```bash
ssh voicsito-vps-direct 'GIT_SSH_COMMAND="ssh -i /root/.ssh/webhooks_infra_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" git ls-remote git@github.com:waseem7b/webhooks-infra.git HEAD'
```

Expected: a commit SHA followed by `HEAD`. If this fails with `Permission denied (publickey)`, the deploy key is not registered correctly — fix Step 3, do not proceed.

- [ ] **Step 5: Back up the existing directory, then clone over it**

```bash
ssh voicsito-vps-direct '
  mv /opt/webhooks-infra /opt/webhooks-infra.pre-git-backup
  GIT_SSH_COMMAND="ssh -i /root/.ssh/webhooks_infra_deploy -o IdentitiesOnly=yes" \
    git clone git@github.com:waseem7b/webhooks-infra.git /opt/webhooks-infra
  chmod +x /opt/webhooks-infra/bin/*.sh
  ls -la /opt/webhooks-infra/
'
```

Expected: clone succeeds; listing shows `.git`, `README.md`, `REGISTRY.md`, `BRIEFING.md`, `bin/`, `templates/`, all owned by `root` (fixing the old uid-501 ownership leak).

Rollback if anything looks wrong: `rm -rf /opt/webhooks-infra && mv /opt/webhooks-infra.pre-git-backup /opt/webhooks-infra`.

- [ ] **Step 6: Make `git pull` work without the env var every time**

```bash
ssh voicsito-vps-direct 'git -C /opt/webhooks-infra config core.sshCommand "ssh -i /root/.ssh/webhooks_infra_deploy -o IdentitiesOnly=yes"; git -C /opt/webhooks-infra pull'
```

Expected: `Already up to date.`

- [ ] **Step 7: Install the snapshot timer**

```bash
ssh voicsito-vps-direct 'bash /opt/webhooks-infra/bin/install-state-tracking.sh'
```

Expected: `✓ snapshot timer installed and baseline captured`, then a timer row showing `webhooks-snapshot.timer` with a NEXT time within the hour.

- [ ] **Step 8: Verify the baseline snapshot captured real state**

```bash
ssh voicsito-vps-direct '
  echo "=== tracked files ==="; git -C /opt/webhooks-infra-state ls-files
  echo "=== history ==="; git -C /opt/webhooks-infra-state log --oneline
  echo "=== LIVE.md ==="; cat /opt/webhooks-infra-state/LIVE.md
'
```

Expected: tracked files include `nginx/voicsito.conf`, `nginx/ashley.conf`, `nginx-vhost.conf`, `systemd/voicsito-webhooks.service`, `systemd/ashley-webhooks.service`; one `snapshot:` commit; and `LIVE.md` showing both projects with ports 8081/8082, unit `active`, health `200`. `LIVE.md` must **not** appear in `ls-files`.

- [ ] **Step 9: Prove the undo button actually works**

This is the whole point of the task — verify it rather than assuming. Make a harmless change to a **throwaway** file (never a real project's config), snapshot, and confirm it shows as a diff.

```bash
ssh voicsito-vps-direct '
  echo "# scratch probe — safe, not included by nginx" > /etc/nginx/sites.d/webhooks/zzprobe.conf.disabled
  cp /etc/nginx/sites.d/webhooks/zzprobe.conf.disabled /etc/nginx/sites.d/webhooks/zzprobe.conf
  systemctl start webhooks-snapshot.service
  git -C /opt/webhooks-infra-state log --oneline -2
  git -C /opt/webhooks-infra-state show --stat HEAD
'
```

Expected: a new `snapshot:` commit whose stat names `nginx/zzprobe.conf`.

Note: `zzprobe.conf` *is* matched by the vhost's `include ... *.conf` glob, but it contains only a comment, so nginx is unaffected and is not reloaded here. Clean it up immediately in the next step.

- [ ] **Step 10: Remove the probe and confirm the deletion is also tracked**

```bash
ssh voicsito-vps-direct '
  rm -f /etc/nginx/sites.d/webhooks/zzprobe.conf /etc/nginx/sites.d/webhooks/zzprobe.conf.disabled
  systemctl start webhooks-snapshot.service
  git -C /opt/webhooks-infra-state show --stat HEAD
  ls /etc/nginx/sites.d/webhooks/
  nginx -t
'
```

Expected: latest commit shows `nginx/zzprobe.conf` deleted; the directory again holds only `.placeholder.conf`, `ashley.conf`, `voicsito.conf`; `nginx -t` reports `syntax is ok` / `test is successful`.

- [ ] **Step 11: Confirm both services are still healthy**

```bash
ssh voicsito-vps-direct 'curl -s -o /dev/null -w "voicsito:%{http_code} " https://webhooks.c7a.io/voicsito/health; curl -s -o /dev/null -w "ashley:%{http_code}\n" https://webhooks.c7a.io/ashley/health; /opt/webhooks-infra/bin/status.sh'
```

Expected: `voicsito:200 ashley:200`, matching the Step 1 baseline, and `status.sh` showing both projects `✓ running` / `✓ /health ok`.

- [ ] **Step 12: Drop the pre-cutover backup**

Only after Step 11 passes:

```bash
ssh voicsito-vps-direct 'rm -rf /opt/webhooks-infra.pre-git-backup && ls /opt/'
```

Expected: `/opt/` lists `webhooks`, `webhooks-infra`, `webhooks-infra-state` and no `.pre-git-backup`.

---

### Task 7: Remove the infra from the Voicsito repo

**Files:**
- Delete: `~/CLAUDSITO/Voicsito/infra/webhooks/` (all 8 files, and the now-empty `infra/`)
- Modify: `~/CLAUDSITO/Voicsito/docs/webhook-infrastructure.md` (full rewrite)
- Modify: `~/CLAUDSITO/Voicsito/CLAUDE.md` (the webhook-infrastructure doc-list line)

Do this **only after Task 6 Step 11 passes.** Until the VPS is confirmed running off the clone, this directory is the only copy of the templates.

- [ ] **Step 1: Confirm the new repo has everything before deleting anything**

```bash
cd ~/CLAUDSITO/webhooks-infra && git status --porcelain && git log --oneline
diff -r ~/CLAUDSITO/Voicsito/infra/webhooks ~/CLAUDSITO/webhooks-infra --exclude=.git --exclude=.gitignore
```

Expected: clean working tree, 4+ commits, and the `diff -r` showing **only** the intentional edits from Tasks 2-5 (README, BRIEFING, add-project.sh, bootstrap.sh, plus the four new files as "Only in ~/CLAUDSITO/webhooks-infra"). No file present only in the Voicsito copy. If anything is missing, copy it over and commit before proceeding.

- [ ] **Step 2: Delete the directory from Voicsito**

```bash
cd ~/CLAUDSITO/Voicsito
git rm -r infra/webhooks
rmdir infra 2>/dev/null || true
ls infra 2>&1 || echo "infra/ gone — correct"
```

Expected: `infra/ gone — correct`

- [ ] **Step 3: Rewrite the pointer doc**

Replace the entire contents of `~/CLAUDSITO/Voicsito/docs/webhook-infrastructure.md` with:

```markdown
# `webhooks.c7a.io` — shared webhook infrastructure

**Source of truth:** `github.com/waseem7b/webhooks-infra` (private).
Local clone: `~/CLAUDSITO/webhooks-infra`. On the VPS: `/opt/webhooks-infra/`
(a read-only clone — pull to update, never edit in place).

This infra used to live in this repo at `infra/webhooks/`. It was extracted on
2026-08-11: shared infrastructure that several projects depend on has no
business sitting inside one project's repo. This file is now only Voicsito's
slot on that shared host. **Everything general — how to add a project,
conventions, failure modes, capacity — is in the infra repo's `README.md`.**

---

## TL;DR

- One HTTPS host, `https://webhooks.c7a.io`, fronts every Cloud 7 webhook.
- Routing is path-prefix: `/<project>/*` → `localhost:<port>` on the VPS.
  nginx strips the prefix, so each app sees clean paths.
- Each project = own port + own systemd service + own venv + own `.env`.
- Adding a project = one nginx include + one systemd unit, both written by
  `sudo /opt/webhooks-infra/bin/add-project.sh`. No DNS or cert work.
- **Rule zero:** a project touches only `/opt/webhooks/<project>/` and files
  named `<project>.*`. Never another project's anything.

## Voicsito's slot

| Field | Value |
|---|---|
| Public URL | `https://webhooks.c7a.io/voicsito/vapi/tool` |
| Local port | `127.0.0.1:8081` |
| Code dir on VPS | `/opt/webhooks/voicsito/` |
| Systemd unit | `voicsito-webhooks.service` |
| nginx include | `/etc/nginx/sites.d/webhooks/voicsito.conf` |
| Source repo path | [`src/webhooks/`](../src/webhooks/) |
| Deploy script | [`scripts/deploy-webhooks.sh`](../scripts/deploy-webhooks.sh) |

## Commands

```bash
# Deploy or redeploy the Voicsito webhook server
./scripts/deploy-webhooks.sh

# Tail Voicsito's app logs
ssh voicsito-vps-direct 'journalctl -u voicsito-webhooks -f'

# What's mounted on the shared host right now
ssh voicsito-vps-direct '/opt/webhooks-infra/bin/status.sh'

# Live inventory + the change history of every project's config
ssh voicsito-vps-direct 'cat /opt/webhooks-infra-state/LIVE.md'
ssh voicsito-vps-direct 'git -C /opt/webhooks-infra-state log --oneline'

# Update the shared infra docs/scripts on the host after pushing to the repo
ssh voicsito-vps-direct 'git -C /opt/webhooks-infra pull'
```

## Onboarding another project

Clone `waseem7b/webhooks-infra` and read its `README.md`. `BRIEFING.md` in
that repo is a copy-paste prompt for handing the whole system to a new agent.
```

- [ ] **Step 4: Update the doc list in CLAUDE.md**

In `~/CLAUDSITO/Voicsito/CLAUDE.md`, under `## Project Documentation`, replace the webhook line:

```markdown
- See `/docs/webhook-infrastructure.md` for the shared `webhooks.c7a.io` infra (also at `/opt/webhooks-infra/` on the VPS)
```

with:

```markdown
- See `/docs/webhook-infrastructure.md` for Voicsito's slot on the shared `webhooks.c7a.io` host. The infra itself now lives in its own repo: `github.com/waseem7b/webhooks-infra` (clone at `~/CLAUDSITO/webhooks-infra`, deployed read-only to `/opt/webhooks-infra/` on the VPS)
```

- [ ] **Step 5: Check nothing else in the repo still points at the old path**

```bash
cd ~/CLAUDSITO/Voicsito
grep -rn "infra/webhooks" --exclude-dir=.git --exclude-dir=docs/superpowers . || echo "NO STALE REFS"
```

Expected: `NO STALE REFS`. Hits inside `docs/superpowers/plans/` (this plan) are expected and fine. Any hit in `scripts/`, `docs/` proper, or `CLAUDE.md` must be fixed before committing — check `scripts/setup-webhook-infra.sh` in particular, since it wraps `infra/webhooks/bin/bootstrap.sh`.

- [ ] **Step 6: Handle scripts/setup-webhook-infra.sh**

If Step 5 flagged it, that wrapper now points at a deleted path. Replace its body with a pointer:

```bash
#!/bin/bash
# The shared webhook infra moved out of this repo on 2026-08-11.
# It now lives at github.com/waseem7b/webhooks-infra.
echo "Shared webhook infra now lives in its own repo:"
echo "  gh repo clone waseem7b/webhooks-infra"
echo "  sudo bash webhooks-infra/bin/bootstrap.sh    # fresh-host setup"
echo ""
echo "On the existing VPS it is already installed at /opt/webhooks-infra/."
echo "Voicsito's own deploy is: ./scripts/deploy-webhooks.sh"
exit 0
```

- [ ] **Step 7: Commit**

```bash
cd ~/CLAUDSITO/Voicsito
git add -A
git commit -m "refactor: move shared webhook infra out to its own repo

infra/webhooks/ served every project mounted on webhooks.c7a.io but lived
inside Voicsito, so other projects had to reach into an unrelated repo to
read their own operator guide — and the VPS copy silently drifted (it still
listed ashley's port as free).

Now: github.com/waseem7b/webhooks-infra is the source, /opt/webhooks-infra
is a read-only clone of it, and live config is snapshotted hourly into a git
repo on the host so any change is diffable and revertible.

docs/webhook-infrastructure.md is now just Voicsito's slot on the host."
```

---

### Task 8: End-to-end verification

**Files:** none — verification only.

- [ ] **Step 1: The whole system, from the outside**

```bash
curl -s -o /dev/null -w "voicsito:%{http_code}\n" https://webhooks.c7a.io/voicsito/health
curl -s -o /dev/null -w "ashley:%{http_code}\n"  https://webhooks.c7a.io/ashley/health
```

Expected: both `200`.

- [ ] **Step 2: The VPS is genuinely a clone, and genuinely read-only**

```bash
ssh voicsito-vps-direct '
  git -C /opt/webhooks-infra remote -v
  git -C /opt/webhooks-infra status --porcelain && echo "(clean tree)"
  git -C /opt/webhooks-infra push --dry-run 2>&1 | tail -2
'
```

Expected: remote is `git@github.com:waseem7b/webhooks-infra.git`; clean tree; and the push attempt **fails** with a read-only/permission error. A push that succeeds means the deploy key was added with write access — remove it and re-add read-only.

- [ ] **Step 3: The pull path works (this is how every future doc change lands)**

```bash
ssh voicsito-vps-direct 'git -C /opt/webhooks-infra pull && grep -c "Rule zero" /opt/webhooks-infra/README.md'
```

Expected: `Already up to date.` and a count `≥ 1`, proving the Task 2 ownership rules actually reached the host.

- [ ] **Step 4: The timer is armed and will keep running**

```bash
ssh voicsito-vps-direct 'systemctl is-enabled webhooks-snapshot.timer; systemctl list-timers webhooks-snapshot.timer --no-pager'
```

Expected: `enabled`, and a NEXT time within the next hour.

- [ ] **Step 5: Both git repos are pushed and clean**

```bash
cd ~/CLAUDSITO/webhooks-infra && git status -sb && git log --oneline -5
cd ~/CLAUDSITO/Voicsito && git status -sb && git log --oneline -1
```

Expected: `webhooks-infra` on `## main...origin/main` with nothing ahead and a clean tree; Voicsito showing the extraction commit.

- [ ] **Step 6: Fix the registry drift that started all this**

The new repo's `REGISTRY.md` is the accurate copy, but confirm it against live truth now that `LIVE.md` exists:

```bash
ssh voicsito-vps-direct 'cat /opt/webhooks-infra-state/LIVE.md'
grep -E "^\| 80" ~/CLAUDSITO/webhooks-infra/REGISTRY.md
```

Expected: both list voicsito on 8081 and ashley on 8082, and `REGISTRY.md` says next free is **8083**. If they disagree, fix `REGISTRY.md`, commit, push, and pull on the VPS — `LIVE.md` is the truth.

---

## Rollback

Nothing here is one-way.

| If | Then |
|---|---|
| The clone on the VPS is wrong (Tasks 6.5-6.11) | `rm -rf /opt/webhooks-infra && mv /opt/webhooks-infra.pre-git-backup /opt/webhooks-infra` — the backup exists until Task 6 Step 12 |
| The snapshot timer misbehaves | `systemctl disable --now webhooks-snapshot.timer` — it only ever copies *out* of `/etc`, so it cannot have damaged live config |
| The Voicsito deletion was premature | `git revert` the Task 7 commit; the files are also in the new repo's history |
| A project's config gets broken later | `git -C /opt/webhooks-infra-state log -p nginx/<project>.conf`, copy the good version back, `nginx -t && systemctl reload nginx` |

---

## Audit revisions (v2) — accepted deltas, by source

Three pre-execution audits ran 2026-08-11. Every delta below was accepted and is binding on execution.

### Blockers (both fixed)

- **SEC-1 (CRITICAL):** `webhooks-snapshot.service` must NOT ExecStart a file inside the cloned repo — a repo compromise would mean root code execution on the next timer fire. `install-state-tracking.sh` instead installs the script to `/usr/local/sbin/webhooks-snapshot-state` (`install -m 755`) and the unit points there. Updating the script requires deliberately re-running the installer, never a bare `git pull`.
- **SEC-2/OPS-1 (confirmed crash):** the `port=$(grep ... | cut ...)` substitution in `snapshot-state.sh` dies under `set -euo pipefail` on any `.conf` without a port (including the plan's own probe). Append `|| true`.

### Security deltas

- **SEC-3:** snapshot redacts `Environment=` lines when copying systemd units (`sed 's/^\(\s*Environment=\).*/\1[REDACTED-by-snapshot]/'`); rule zero gains "secrets only via `EnvironmentFile=`, never inline `Environment=`"; state dir `chmod 700`.
- **SEC-4:** no TOFU on the code-supply channel: pre-seed `/root/.ssh/known_hosts` with GitHub's host keys fetched over TLS from `https://api.github.com/meta`, then `StrictHostKeyChecking=yes` everywhere (supersedes `accept-new`).
- **SEC-5:** README wording softened — snapshots detect honest mistakes, they are not tamper-proof against a root actor; state dir 700.
- **SEC-6:** template rendering in `add-project.sh` switches from `sed` substitution (injection via `|`, `&`, `\`, `\n` in ExecStart/Description) to pure-bash literal replacement (`${var//\{\{PROJECT\}\}/$PROJECT}`).
- **SEC-8:** the Task 6 undo-proof probe uses **only** `zzprobe.conf.disabled` (not glob-matched by the vhost include); never write a `.conf` into the live include dir for a test.
- **SEC-10/OPS-6:** gh-added deploy keys are deleted if the gh token is revoked/expires — documented in README troubleshooting with re-add instructions (web UI for permanence).
- **SEC-11/OPS-8:** keygen guarded for idempotency (`[ -f key ] ||`).
- **SEC-12:** `systemctl is-active` capture fixed (`|| true` + `${unit:-absent}`) — the `|| echo` form yields a two-line value.
- **SEC-15:** `git pull --ff-only` everywhere, no bare pulls.
- **SEC-14 (noted, out of scope):** true enforcement (per-project users, ProtectSystem=strict) is a future hardening plan; this system is detection + convention, and the docs must not claim more.

### Ops deltas

- **OPS-2:** bootstrap clone branch: `cd /` + `mv` aside before `rm`, backup removed only after clone succeeds (protects against self-deletion of cwd and GitHub outage mid-step).
- **OPS-3:** bootstrap persists `core.sshCommand` on the clone (otherwise future pulls fall back to root's default identity — potentially write-capable).
- **OPS-4:** Voicsito extraction commit stages files explicitly; never `git add -A` (a dirty `.claude/settings.local.json` and this plan would ride along).
- **OPS-5:** two stale-reference files the plan missed get explicit edits: `docs/system-breakdown.md:177` and `ashley/HANDOFF.md:374,527`.
- **OPS-7:** all prose claiming add-project.sh auto-updates the registry is corrected in the same commit (script header line 7, README line 48, REGISTRY lines 5/31, README line 18/21 tree comments).
- **OPS-9:** `set -e` prepended inside remote ssh blocks of the cutover.
- **OPS-10:** snapshot.service gains `Wants=network-online.target`; health-curl fallback becomes `|| true`.
- **OPS-11:** state repo `git init -q -b main`.
- **OPS-12:** bootstrap replacement block spans lines 121–128 (not 122–128) to avoid a duplicated blank echo.
- **OPS-14:** stale-ref grep uses the portable form (`| grep -v docs/superpowers`).
- **OPS-16:** `scripts/deploy-webhooks.sh` default `VPS_HOST` flipped to the working `root@voicsito-vps-direct` alias (Tailscale alias times out).
- **OPS-17:** README gains a fresh-VPS order-of-operations note (key → register → first clone → bootstrap).
- **OPS-18:** pre-existing bug fixed while in the file: unescaped backticks in add-project.sh line 109 execute `nginx -t` via command substitution on the error path.

### Onboarding deltas (the discovery layer v1 lacked)

- **ONB-1:** a user-level **skill** is the discovery mechanism: `skills/webhooks-infra/SKILL.md` lives in the repo, symlinked to `~/.claude/skills/webhooks-infra` so `git pull` updates it globally. Discovery through the symlink gets verified with a headless run; fallback is a copy. This is the answer to "what do I point future agents at": they don't need pointing — the skill triggers on webhook-shaped asks in any project. The human-memorable pointer is `~/CLAUDSITO/webhooks-infra/README.md`.
- **ONB-2:** README "Path 3 — Manual" (hand-writing nginx/units — contradicts rule zero, ports picked from the drift-prone REGISTRY) is replaced by "What the script does (reference only — do not provision manually)". `add-project.sh` already-exists branch reworded away from "edit those files directly".
- **ONB-3:** `add-project.sh` gains a non-interactive arg mode (`add-project.sh <name> "<execstart>" [--desc "..."] --yes`) — the interactive prompts hang every agent invoking it over ssh without a tty.
- **ONB-4:** registry-rot defense: `snapshot-state.sh` appends a `⚠ REGISTRY DRIFT` line to `LIVE.md` for any live project missing from the repo's REGISTRY.md; the skill makes "commit your REGISTRY row" an explicit numbered closing step.
- **ONB-5:** "`git pull --ff-only` before reading/acting" is the first instruction in skill, BRIEFING, and repo CLAUDE.md.
- **ONB-7:** README states explicitly that app code under `/opt/webhooks/<project>/` is NOT snapshotted and is recoverable only from that project's own repo — the boundary is absolute because there is no undo there.
- **ONB-8:** convention check added: `ss -tlnp | grep :<port>` must show `127.0.0.1`, not `0.0.0.0`.
- **ONB-9:** the repo gets its own `CLAUDE.md` (session bootstrap: pull first, README is truth, sweep routers on procedure changes, bash -n before commit, no secrets ever).
- **ONB-10:** every non-README doc (BRIEFING, REGISTRY, Voicsito pointer doc) opens with the same "you are here" header naming README.md as canonical and self-resolving conflicts in its favor.
- **ONB-BRIEFING:** BRIEFING's read-the-guide block reordered: existing local clone first, VPS copy second, clone-from-GitHub demoted to fresh-machine-only with an explicit "never clone inside another project's folder".

### Architecture decision confirmed after audit

Pull-model (VPS clones from GitHub via read-only deploy key) is **kept** over a push-model (Mac rsyncs to VPS), because: the dirty-tree signal on the VPS clone makes in-place edits loudly visible; `git log` on the host shows exactly what version is deployed; and with SEC-1 fixed (no repo file is ever executed by a timer) plus SEC-4 (pinned host keys) and read-only key (push rejected), the supply-chain surface reduces to "attacker with GitHub write can change docs/scripts that a human/agent then *chooses* to run" — equivalent to the push-model's trust in the Mac. The deploy-key/token-lifetime coupling (SEC-10) fails loud (pull errors), is documented, and is re-addable in one minute.
