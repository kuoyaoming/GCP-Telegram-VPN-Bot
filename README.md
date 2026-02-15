# GCP Telegram VPN Bot (Serverless & Singleton)

![Python](https://img.shields.io/badge/Python-3.10-3776AB?style=for-the-badge&logo=python&logoColor=white)
![GCP](https://img.shields.io/badge/Google_Cloud-4285F4?style=for-the-badge&logo=google-cloud&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge)

A lightweight, serverless Telegram bot that deploys a **disposable OpenVPN server** on Google Cloud Platform (GCP) on demand.

Designed for engineers who need a clean IP address or a secure tunnel temporarily, without the high cost of running a VPS 24/7. It utilizes the **Singleton Pattern** to ensure only one instance runs at a time, preventing accidental billing.

## 🚀 Features

* **Serverless Architecture**: Logic runs on **Google Cloud Functions (2nd Gen)**. Zero cost when idle.
* **Singleton Enforcement**: Automatically destroys existing instances before creating a new one. Never pay for duplicate servers.
* **Cost Effective**: Uses **Spot Instances (e2-micro)** for maximum savings (~$0.005/hour).
* **Automated Deployment**:
    * One-click setup via Telegram.
    * Auto-generates OpenVPN (`.ovpn`) configuration.
    * Sends the config file directly to your chat.
* **Real-time Status**: Check IP, Uptime, and **Estimated Cost** directly from the bot.

## 🛠️ Architecture

1.  **User** sends `/new` command to Telegram Bot.
2.  **Cloud Function** receives the webhook and calls GCP Compute Engine API.
3.  **Compute Engine** launches an `e2-micro` Spot instance.
4.  **Startup Script** (Bash) inside the VM:
    * Installs Docker.
    * Pulls `kylemanna/openvpn` image.
    * Generates PKI and Client Certificates.
    * Uploads the `.ovpn` file back to Telegram.
5.  **User** imports the file and connects.

## 📋 Prerequisites

* **Google Cloud Platform Account** with billing enabled.
* **Telegram Account**.
* **Google Cloud SDK (`gcloud`)** installed (or use Cloud Shell).

## ⚙️ Configuration

Create a file named `main.py` and update the `CFG` dictionary with your details:

```python
# main.py configuration block
CFG = {
    "token": "YOUR_TELEGRAM_BOT_TOKEN",  # Get from @BotFather
    "chat_id": "YOUR_TELEGRAM_USER_ID",  # Get from @userinfobot
    "project": "your-gcp-project-id",
    "zone": "asia-east1-c",              # Or your preferred zone
    "prefix": "vpn-svr",
    "machine": "e2-micro",               # Cost-effective choice
    "hourly_rate": 0.005                 # Spot price for estimation
}

📦 Deployment Guide
1. Enable Required APIs

Run the following commands in your terminal or Cloud Shell:
Bash

gcloud services enable cloudfunctions.googleapis.com \
    cloudbuild.googleapis.com \
    compute.googleapis.com \
    run.googleapis.com

2. Deploy the Function

Deploy the Python script to Cloud Functions (Gen 2).
Note: We increase memory to 512MB to handle google-cloud-compute libraries efficiently.
Bash

gcloud functions deploy deploy-vpn \
    --gen2 \
    --runtime=python310 \
    --region=asia-east1 \
    --source=. \
    --entry-point=deploy_vpn \
    --trigger-http \
    --allow-unauthenticated \
    --memory=512MB

3. Set Webhook

Once deployed, copy the URL provided by the output (e.g., https://asia-east1-project.cloudfunctions.net/deploy-vpn) and set it as your Telegram Bot's webhook:
Bash

curl "[https://api.telegram.org/bot](https://api.telegram.org/bot)<YOUR_TOKEN>/setWebhook?url=<YOUR_FUNCTION_URL>"

📱 Bot Commands

Configure these commands via @BotFather:
Command	Description
/new	Deploy VPN: Deletes old VMs and starts a fresh one. Sends .ovpn file in ~2 mins.
/status	Check Status: Shows current IP, Uptime, and Real-time Estimated Cost.
/del	Destroy All: Immediately terminates all VPN instances to stop billing.
💰 Cost Analysis (Estimated)

    Cloud Functions: Free tier covers 2 million invocations/month. (Free)

    Compute Engine (e2-micro Spot):

        ~ $0.005 USD / hour (varies by region).

        10 hours of usage ≈ $0.05 USD.

    Network Egress: Standard GCP rates apply (first 1GB is usually free/month).

📝 License

This project is licensed under the MIT License - see the LICENSE file for details.
