#!/usr/bin/env bash
# remote.sh — operate the rtmde collector on a remote VM over an IAP SSH tunnel.
#
# No secrets live here. Set your project/zone/VM via RTMDE_* env vars (or edit the
# placeholders below):
#   export RTMDE_GCP_PROJECT=my-project RTMDE_GCP_ZONE=us-central1-a RTMDE_VM_NAME=rtmde-collector
#
#   ./remote.sh report     # the verdict report (reward vs inventory PnL, by volatility)
#   ./remote.sh status     # systemd service state
#   ./remote.sh logs       # recent collector output
#   ./remote.sh pull       # copy the raw samples file to the current dir
#   ./remote.sh digest     # push the digest to Telegram now
#   ./remote.sh stop|start # pause / resume collection
#   ./remote.sh reset      # wipe data + restart fresh (re-picks markets)
#   ./remote.sh ssh        # interactive shell on the VM
set -euo pipefail

PROJECT="${RTMDE_GCP_PROJECT:-<GCP_PROJECT>}"
ZONE="${RTMDE_GCP_ZONE:-<GCP_ZONE>}"
VM="${RTMDE_VM_NAME:-<VM_NAME>}"
APP_DIR="${RTMDE_APP_DIR:-realtime-md-eval}"

G=(gcloud compute ssh "${VM}" --zone "${ZONE}" --project "${PROJECT}" --tunnel-through-iap --quiet)

case "${1:-report}" in
  report) "${G[@]}" --command "cd ${APP_DIR} && python3 -m rtmde.eval.report" ;;
  status) "${G[@]}" --command 'systemctl status rtmde-collector --no-pager | head -14' ;;
  logs)   "${G[@]}" --command 'journalctl -u rtmde-collector --no-pager -n 30' ;;
  pull)   gcloud compute scp "${VM}:~/${APP_DIR}/state/samples.jsonl" ./ \
            --zone "${ZONE}" --project "${PROJECT}" --tunnel-through-iap --quiet ;;
  digest) "${G[@]}" --command "cd ${APP_DIR} && python3 -m rtmde.notify.digest --report --telegram" ;;
  stop)   "${G[@]}" --command 'sudo systemctl stop rtmde-collector  && echo stopped' ;;
  start)  "${G[@]}" --command 'sudo systemctl start rtmde-collector && echo started' ;;
  reset)  "${G[@]}" --command "sudo systemctl stop rtmde-collector; cd ${APP_DIR} && python3 -m rtmde.eval.harness --reset; sudo systemctl start rtmde-collector" ;;
  ssh)    gcloud compute ssh "${VM}" --zone "${ZONE}" --project "${PROJECT}" --tunnel-through-iap ;;
  *) echo "usage: $0 {report|status|logs|pull|digest|stop|start|reset|ssh}"; exit 1 ;;
esac
