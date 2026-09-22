#!/bin/bash
# The shared webhook infra moved out of this repo on 2026-08-11.
# It now lives at github.com/waseem7b/webhooks-infra
# (local clone: ~/CLAUDSITO/webhooks-infra).
echo "Shared webhook infra now lives in its own repo:"
echo "  local clone:  ~/CLAUDSITO/webhooks-infra   (git pull --ff-only first)"
echo "  fresh clone:  gh repo clone waseem7b/webhooks-infra ~/CLAUDSITO/webhooks-infra"
echo "  fresh host:   see README.md → 'Fresh-VPS bootstrap (order of operations)'"
echo ""
echo "On the existing VPS it is already installed at /opt/webhooks-infra/ (read-only clone)."
echo "Voicsito's own deploy is: ./scripts/deploy-webhooks.sh"
exit 0
