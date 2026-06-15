# Deployment — run the collector 7×24 for ~$0/month

The evaluation harness needs to run continuously for days to span calm *and* news
periods. A laptop can't stay on, so it runs on a small always-on VM. This setup runs
on a **GCP free-tier `e2-micro`** and costs effectively **$0/month** — the engineering
that makes that true is the interesting part.

> All commands use placeholders: `<GCP_PROJECT>`, `<GCP_ZONE>`, `<VM_NAME>`, `<VM_USER>`.
> No secrets live in this repo. The ops script reads `RTMDE_GCP_PROJECT` / `RTMDE_GCP_ZONE`
> / `RTMDE_VM_NAME` from the environment.

## Why it's ~$0/month

| Lever | What it does |
|---|---|
| **`e2-micro` in `us-west1` / `us-central1` / `us-east1`** | one such instance is in the GCP free tier |
| **IPv6-only (no external IPv4)** | an external IPv4 address now costs ~$3/mo; the box has none and reaches the API (Cloudflare) over IPv6 |
| **IAP SSH tunnel** | reach the box for ops without any public IP or open firewall port |
| **Batched `POST /books` (1 request/round)** | keeps egress far under the 1 GB/mo free cap |
| **`systemd Restart=always` + 1-day sessions** | self-heals across crashes/reboots; each new day prunes resolved markets and tops back up — no babysitting |

This is a single personal-scale VM. It is **not** a distributed cluster and makes no
high-availability claims; it is a cheap, fault-tolerant box that collects data reliably.

## 1. Create the VM (IPv6-only, free tier)

```bash
gcloud compute instances create <VM_NAME> \
  --project=<GCP_PROJECT> --zone=<GCP_ZONE> \
  --machine-type=e2-micro --image-family=debian-12 --image-project=debian-cloud \
  --stack-type=IPV6_ONLY --no-address
```

Enable IAP SSH (one-time, per project) so you can reach a box with no public IP:

```bash
gcloud compute firewall-rules create allow-iap-ssh \
  --project=<GCP_PROJECT> --direction=INGRESS --action=ALLOW \
  --rules=tcp:22 --source-ranges=35.235.240.0/20
```

## 2. Install the app

```bash
gcloud compute ssh <VM_NAME> --zone <GCP_ZONE> --project <GCP_PROJECT> --tunnel-through-iap
# on the VM:
git clone <YOUR_REPO_URL> realtime-md-eval && cd realtime-md-eval
pip install -r requirements.txt && pip install -e .
cp config.example.yaml config.yaml          # edit knobs; secrets stay in env, not here
```

## 3. Enable the collector + daily digest

Edit the `<VM_USER>` placeholder in the unit files, then:

```bash
sudo cp deploy/collector.service /etc/systemd/system/rtmde-collector.service
sudo cp deploy/digest.service    /etc/systemd/system/rtmde-digest.service
sudo cp deploy/digest.timer      /etc/systemd/system/rtmde-digest.timer
sudo systemctl daemon-reload
sudo systemctl enable --now rtmde-collector.service
sudo systemctl enable --now rtmde-digest.timer
```

For the Telegram digest, create a bot with @BotFather and put the token + chat id in
`~/.rtmde_tg.env` (chmod 600) — see `rtmde/notify/digest.py`. Drop `--telegram` from
`digest.service` to log the digest to the journal instead.

## 4. Operate it from your laptop

```bash
export RTMDE_GCP_PROJECT=<GCP_PROJECT> RTMDE_GCP_ZONE=<GCP_ZONE> RTMDE_VM_NAME=<VM_NAME>
./deploy/remote.sh report     # verdict report (reward vs inventory PnL, by volatility)
./deploy/remote.sh status     # service health
./deploy/remote.sh logs       # recent samples
./deploy/remote.sh pull       # copy state/samples.jsonl down for offline analysis
./deploy/remote.sh digest     # push the digest now
```
