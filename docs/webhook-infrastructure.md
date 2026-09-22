> **Canonical guide:** `README.md` in `github.com/waseem7b/webhooks-infra`
> (local clone `~/CLAUDSITO/webhooks-infra` — `git pull --ff-only` before relying on it).
> This file is Voicsito's-slot pointer, not the guide. If it disagrees with README.md, README.md wins.

# `webhooks.c7a.io` — shared webhook infrastructure

The shared infra used to live in this repo at `infra/webhooks/`. It was extracted on 2026-08-11 into its own repo: shared infrastructure that several projects depend on has no business sitting inside one project's repo. On the VPS it is deployed at `/opt/webhooks-infra/` as a **read-only clone** — pull to update, never edit in place. There is also a user-level Claude Code skill (`webhooks-infra`) that routes any webhook-shaped task to the canonical README automatically.

This file is now only Voicsito's slot on that shared host. **Everything general — how to add a project, rule zero, conventions, the undo button, failure modes, capacity — is in the infra repo's `README.md`.**

---

## TL;DR

- One HTTPS host, `https://webhooks.c7a.io`, fronts every Cloud 7 webhook.
- Routing is path-prefix: `/<project>/*` → `localhost:<port>` on the VPS. nginx strips the prefix, so each app sees clean paths.
- Each project = own port + own systemd service + own venv + own `.env`.
- Adding a project = `sudo /opt/webhooks-infra/bin/add-project.sh` (no DNS or cert work).
- **Rule zero:** a project touches only `/opt/webhooks/<project>/` and files named `<project>.*`. Never another project's anything.

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
ssh voicsito-vps-direct 'git -C /opt/webhooks-infra pull --ff-only'
```

## Onboarding another project

Point the agent at `~/CLAUDSITO/webhooks-infra/README.md` (or just mention webhooks — the `webhooks-infra` user-level skill triggers on its own). `BRIEFING.md` in that repo is a copy-paste prompt for handing the whole system to a context-free agent.
