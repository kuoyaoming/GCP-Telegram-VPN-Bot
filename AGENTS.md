# AGENTS.md — AI Agent Guidance for GCP-Telegram-VPN-Bot

> **Repository:** `Max97k/GCP-Telegram-VPN-Bot`  
> **Default Branch:** `main`  
> **Primary Technology Stack:** Python 3.11, Google Cloud Functions Gen 2, Google Cloud Compute Engine API, Google Secret Manager  
> **Visibility:** Public  

---

## 1. Project Overview & Architecture

### 1.1 Purpose & Mission
**GCP-Telegram-VPN-Bot** is a serverless, zero-idle-cost Telegram Bot running on Google Cloud Functions 2nd Gen. It enables authorized users to provision, monitor, and destroy on-demand disposable WireGuard VPN servers deployed on Google Cloud Platform (GCP) Compute Engine Spot `f1-micro` instances across 9 global regions. The bot automatically provisions the VPN, compiles client profiles, generates QR codes, and delivers them directly into the Telegram chat interface.

### 1.2 System Architecture & Component Diagram
```
+-----------------------------------------------------------------------------+
|                          GCP-Telegram-VPN-Bot Flow                          |
+-----------------------------------------------------------------------------+
                                       |
                   Telegram User Chat (/new, /status, /del)
                                       |
                                       v
                     +-----------------------------------+
                     |     Cloud Functions (Gen 2)       |
                     |     Entrypoint: deploy_vpn()      |
                     |     - Webhook Router & Auth       |
                     |     - Secret Manager Integration  |
                     +-----------------------------------+
                                       |
                   +-------------------+-------------------+
                   |                                       |
                   v                                       v
    +------------------------------+        +------------------------------+
    |    Google Secret Manager     |        |    GCP Compute Engine API    |
    |  - TELEGRAM_BOT_TOKEN        |        |  - Provision Spot f1-micro   |
    |  - AUTHORIZED_USER_ID        |        |  - Inject startup script     |
    +------------------------------+        |  - Query / Delete instances  |
                                            +------------------------------+
                                                           |
                                                           v
                                            +------------------------------+
                                            |       Spot VM Instance       |
                                            |  - Install WireGuard & qrencode|
                                            |  - Generate Keys & Config    |
                                            |  - Post QR Code PNG to Chat  |
                                            +------------------------------+
```

### 1.3 Key File & Directory Map
| Path | Purpose / Description |
|---|---|
| `main.py` | Core serverless function. Implements `deploy_vpn(request)` HTTP webhook handler, command routing, inline keyboard generation, Secret Manager access, and Compute Engine VM lifecycle operations. |
| `requirements.txt` | Python dependencies: `google-cloud-compute`, `google-cloud-secret-manager`, `requests`, `functions-framework`. |
| `README.md` / `README_zh-TW.md` | Comprehensive user guide, architectural breakdown, and deployment runbook in English and Traditional Chinese. |
| `assets/` | Documentation screenshots and interaction workflow diagrams. |

---

## 2. Development, Build & Verification Commands

### 2.1 Prerequisites & Environment Setup
- **Python:** Python 3.11.
- **Google Cloud SDK:** `gcloud` CLI installed and authenticated with active GCP project.
- **Telegram Bot:** A bot created via `@BotFather` with HTTP API token.
- **GCP Secret Manager:** Secrets created for `TELEGRAM_BOT_TOKEN` and `AUTHORIZED_USER_ID`.

### 2.2 Local Development & Testing
```bash
# Install dependencies
pip install -r requirements.txt

# Run locally using Functions Framework
export GCP_PROJECT_ID="your-gcp-project-id"
functions-framework --target=deploy_vpn --port=8080 --debug
```

### 2.3 Static Analysis & Syntax Verification
```bash
# Validate Python syntax
python -m py_compile main.py
```

### 2.4 Cloud Deployment Commands
```bash
# Deploy to Google Cloud Functions 2nd Gen
gcloud functions deploy vpn-bot \
  --gen2 \
  --runtime=python311 \
  --region=asia-northeast1 \
  --source=. \
  --entry-point=deploy_vpn \
  --trigger-http \
  --allow-unauthenticated \
  --set-env-vars GCP_PROJECT_ID=$(gcloud config get-value project) \
  --memory=512MB \
  --timeout=60s

# Register Telegram Webhook
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=<CLOUD_FUNCTION_TRIGGER_URL>"
```

---

## 3. Coding Standards & Conventions

### 3.1 Code Style & Idioms
- **Python Standard:** PEP 8 compliance, clean function separation, type hints where applicable.
- **Serverless Resilience:** Handle timeouts and asynchronous VM initialization gracefully. Cloud Function responses must return HTTP 200 to Telegram quickly to prevent webhook retry storms.

### 3.2 Telegram Interaction Patterns
- Use Telegram Inline Keyboards for interactive menu navigation (region selection, confirm deletion).
- Sanitize HTML/Markdown tags in Telegram text replies to prevent message parsing exceptions.

### 3.3 State Management & Error Handling
- Use try/except blocks around GCP API calls (`google.cloud.compute_v1.InstancesClient`) with descriptive error reporting to Telegram admins.
- Enforce strict authentication whitelist check at the entry of `deploy_vpn()` before any API call is made.

---

## 4. Safety, Security & Resource Constraints

### 4.1 Secrets & Identity Management
- **Secret Manager Mandatory:** NEVER commit or hardcode `TELEGRAM_BOT_TOKEN`, user IDs, or private keys. Always read credentials dynamically via `google.cloud.secretmanager`.
- **IAM Principle of Least Privilege:** Cloud Function service account only requires `roles/secretmanager.secretAccessor` and `roles/compute.admin`.

### 4.2 Cloud Cost & Resource Guardrails
- **Spot Instances Only:** Always provision `f1-micro` Spot/Preemptible instances (~$0.004/hour) to minimize cloud cost.
- **Quota Enforced:** Hard cap of maximum 5 active VMs per user to prevent runaway costs or quota exhaustion.
- **Firewall Rules:** Ensure VPC firewall rule allows ingress UDP traffic on port `51820` for WireGuard.

### 4.3 Git & Branch Workflow
- **Default Branch:** `main`.
- **Commit Standards:** Use conventional commit messages: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
