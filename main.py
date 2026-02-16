import time
import datetime
import requests
import functions_framework
import random
import string
import re
import traceback
from google.cloud import compute_v1
from google.cloud import secretmanager

# --- Configuration & Secrets ---

def get_secret(secret_id, project_id=None):
    """
    Retrieves a secret from Google Secret Manager.
    """
    client = secretmanager.SecretManagerServiceClient()

    if not project_id:
        # Fallback to init_config logic
        return None

    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    try:
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8").strip()
    except Exception as e:
        print(f"Error retrieving secret {secret_id}: {e}")
        return None

# Placeholder - these will be populated at runtime
CFG = {
    "token": None,
    "project": None,
    "authorized_users": [],
    "machine": "f1-micro",
    "prefix": "vpn-svr"
}

# Static Region Map (City -> Zone)
REGION_MAP = {
    "Taiwan": "asia-east1-b",
    "Tokyo": "asia-northeast1-b",
    "Singapore": "asia-southeast1-b",
    "Iowa (US Central)": "us-central1-a",
    "Oregon (US West)": "us-west1-b",
    "S. Carolina (US East)": "us-east1-b",
    "London": "europe-west2-c",
    "Frankfurt": "europe-west3-c",
    "Netherlands": "europe-west4-a"
}

compute_instances_client = None

def get_compute_client():
    global compute_instances_client
    if not compute_instances_client:
        compute_instances_client = compute_v1.InstancesClient()
    return compute_instances_client

def init_config():
    """Initializes configuration from Secret Manager."""
    import os
    # 1. Try env var set by user
    project_id = os.environ.get("GCP_PROJECT_ID")

    # 2. Try standard GCF env vars
    if not project_id:
        project_id = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")

    # 3. Try Metadata Server (if running on GCF/GCE)
    if not project_id:
        try:
            url = "http://metadata.google.internal/computeMetadata/v1/project/project-id"
            headers = {"Metadata-Flavor": "Google"}
            project_id = requests.get(url, headers=headers, timeout=1).text.strip()
        except:
            pass

    if not project_id:
        print("CRITICAL: Could not determine GCP Project ID from Environment or Metadata.")
        return False

    print(f"Initializing with Project ID: {project_id}")
    CFG["project"] = project_id

    token = get_secret("TELEGRAM_BOT_TOKEN", project_id)
    if token:
        CFG["token"] = token
    else:
        print("CRITICAL: TELEGRAM_BOT_TOKEN secret not found.")
        return False

    auth_users = get_secret("AUTHORIZED_USER_ID", project_id)
    if auth_users:
        CFG["authorized_users"] = [u.strip() for u in auth_users.split(",")]
    else:
        print("CRITICAL: AUTHORIZED_USER_ID secret not found.")
        return False

    return True

# --- Telegram Helpers ---

def send_msg(chat_id, text, reply_markup=None):
    if not CFG["token"]: return
    print(f"DEBUG: send_msg called for chat_id={chat_id}")
    url = f"https://api.telegram.org/bot{CFG['token']}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        print(f"DEBUG: sending payload: {payload}")
        response = requests.post(url, json=payload, timeout=10)
        print(f"DEBUG: response status: {response.status_code}, body: {response.text}")
    except Exception as e:
        print(f"Error sending message: {e}")

def edit_msg(chat_id, message_id, text, reply_markup=None):
    if not CFG["token"]: return
    url = f"https://api.telegram.org/bot{CFG['token']}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error editing message: {e}")

def answer_callback(callback_query_id, text=None):
    if not CFG["token"]: return
    url = f"https://api.telegram.org/bot{CFG['token']}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error answering callback: {e}")

def get_region_keyboard():
    print("DEBUG: Generating Region Keyboard")
    try:
        keyboard = []
        row = []
        for city, zone in REGION_MAP.items():
            row.append({"text": city, "callback_data": f"region:{zone}"})
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        print(f"DEBUG: Keyboard Generated: {keyboard}")
        return {"inline_keyboard": keyboard}
    except Exception as e:
        print(f"ERROR: get_region_keyboard failed: {e}")
        return None

def get_peers_keyboard(zone):
    keyboard = []
    row = []
    for i in range(1, 6):
        row.append({"text": str(i), "callback_data": f"deploy:{zone}:{i}"})
    keyboard.append(row)
    return {"inline_keyboard": keyboard}

# --- VM Management ---

def launch_vm(chat_id, zone, peers):
    """
    Provisions an f1-micro Spot Instance with WireGuard.
    """
    client = get_compute_client()
    name = f"{CFG['prefix']}-{chat_id}-{int(time.time())}"

    # Startup Script
    script = f"""#!/bin/bash
# 1. Swap Setup (Critical for f1-micro)
fallocate -l 1G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
sysctl vm.swappiness=10
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# 2. Install Dependencies
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y wireguard qrencode curl

# 3. Server Configuration
umask 077
wg genkey | tee /etc/wireguard/server_private.key | wg pubkey > /etc/wireguard/server_public.key
SERVER_PRIV=$(cat /etc/wireguard/server_private.key)
SERVER_PUB=$(cat /etc/wireguard/server_public.key)
IP=$(curl -s ifconfig.me)

cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = 10.100.0.1/24
SaveConfig = true
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o ens4 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o ens4 -j MASQUERADE
ListenPort = 51820
PrivateKey = $SERVER_PRIV
EOF

# 4. Peer Generation Loop
for i in $(seq 1 {peers}); do
  CLIENT_PRIV=$(wg genkey)
  CLIENT_PUB=$(echo $CLIENT_PRIV | wg pubkey)

  # Append to Server Config
  cat >> /etc/wireguard/wg0.conf <<EOF

[Peer]
PublicKey = $CLIENT_PUB
AllowedIPs = 10.100.0.$((i+1))/32
EOF

  # Create Client Config
  cat > /tmp/peer$i.conf <<EOF
[Interface]
PrivateKey = $CLIENT_PRIV
Address = 10.100.0.$((i+1))/24
DNS = 8.8.8.8

[Peer]
PublicKey = $SERVER_PUB
Endpoint = $IP:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
EOF

  # Generate QR
  qrencode -o /tmp/peer$i.png -t PNG < /tmp/peer$i.conf

  # Send to Telegram
  # Send Photo
  curl -s -F "chat_id={chat_id}" -F "photo=@/tmp/peer$i.png" -F "caption=🔐 Peer $i ({zone})" "https://api.telegram.org/bot{CFG['token']}/sendPhoto"

  # Short delay to avoid rate limits
  sleep 2
done

# 5. Start Service
systemctl enable wg-quick@wg0
systemctl start wg-quick@wg0
"""

    instance = compute_v1.Instance()
    instance.name = name
    instance.machine_type = f"zones/{zone}/machineTypes/{CFG['machine']}"

    # Network Interface
    network_interface = compute_v1.NetworkInterface()
    network_interface.network = "global/networks/default"
    access_config = compute_v1.AccessConfig()
    access_config.type_ = compute_v1.AccessConfig.Type.ONE_TO_ONE_NAT
    network_interface.access_configs = [access_config]
    instance.network_interfaces = [network_interface]

    # Disk
    disk = compute_v1.AttachedDisk()
    disk.boot = True
    disk.auto_delete = True
    initialize_params = compute_v1.AttachedDiskInitializeParams()
    initialize_params.source_image = "projects/debian-cloud/global/images/family/debian-12"
    disk.initialize_params = initialize_params
    instance.disks = [disk]

    # Metadata (Startup Script)
    metadata = compute_v1.Metadata()
    metadata.items = [{"key": "startup-script", "value": script}]
    instance.metadata = metadata

    # Tags & Labels
    instance.tags = compute_v1.Tags(items=["vpn-server"])
    instance.labels = {"owner-id": str(chat_id)}

    # Spot Provisioning
    scheduling = compute_v1.Scheduling()
    scheduling.provisioning_model = compute_v1.Scheduling.ProvisioningModel.SPOT
    instance.scheduling = scheduling

    # IP Forwarding
    instance.can_ip_forward = True

    try:
        operation = client.insert(project=CFG['project'], zone=zone, instance_resource=instance)
        # We do NOT wait for operation here to avoid GCF timeout.
        # But we could check for immediate errors.
        return True, None
    except Exception as e:
        error_msg = str(e)
        if "resource pool exhausted" in error_msg.lower() or "zone resource" in error_msg.lower():
            return False, "Zone Resource Pool Exhausted. Please try another region."
        return False, error_msg

def delete_vm(zone, instance_name):
    client = get_compute_client()
    try:
        client.delete(project=CFG['project'], zone=zone, instance=instance_name)
        return True
    except Exception as e:
        print(f"Error deleting {instance_name}: {e}")
        return False

def count_user_vms(chat_id):
    try:
        print(f"DEBUG: count_user_vms({chat_id}) - Start")
        client = get_compute_client()

        # Use AggregatedListInstancesRequest directly
        request = compute_v1.AggregatedListInstancesRequest()
        request.project = CFG['project']
        request.filter = f"labels.owner-id={chat_id} AND status=RUNNING"

        agg_list = client.aggregated_list(request=request)
        count = 0
        for _, response in agg_list:
            if response.instances:
                count += len(response.instances)
        print(f"DEBUG: count_user_vms({chat_id}) - End: {count}")
        return count
    except Exception as e:
        print(f"ERROR: count_user_vms failed: {e}")
        return 0 # Fail safe

def delete_all_vms(chat_id):
    client = get_compute_client()

    request = compute_v1.AggregatedListInstancesRequest()
    request.project = CFG['project']
    request.filter = f"labels.owner-id={chat_id}"

    agg_list = client.aggregated_list(request=request)
    deleted_count = 0
    
    for zone_path, response in agg_list:
        if response.instances:
            zone = zone_path.split("/")[-1]
            for vm in response.instances:
                try:
                    client.delete(project=CFG['project'], zone=zone, instance=vm.name)
                    deleted_count += 1
                except Exception as e:
                    print(f"Error deleting {vm.name}: {e}")
    return deleted_count

# --- Core Entry Point ---

@functions_framework.http
def deploy_vpn(request):
    if not CFG["token"]:
        if not init_config():
            return "Configuration Error", 500

    data = request.get_json(silent=True)
    if not data: return "OK"

    try:
        if 'message' in data:
            handle_message(data['message'])
        elif 'callback_query' in data:
            handle_callback(data['callback_query'])
    except Exception as e:
        print(f"Top Level Error: {e}")
        traceback.print_exc()

    return "OK"

def handle_message(msg):
    chat_id = str(msg.get('chat', {}).get('id'))
    text = msg.get('text', '').strip()

    print(f"DEBUG: handle_message: chat_id={chat_id}, text={text}")

    if not text: return

    if chat_id not in CFG["authorized_users"]:
        print(f"DEBUG: Unauthorized access attempt by {chat_id}")
        send_msg(chat_id, "🔒 **Unauthorized**\nYour User ID is not authorized to use this bot.")
        return

    cmd = text.lower().split()[0]
    print(f"DEBUG: Parsed command: {cmd}")

    if cmd == "/start":
        send_msg(chat_id, "👋 Welcome! Use /new to deploy a VPN.")

    elif cmd == "/new":
        print("DEBUG: Processing /new command...")
        count = count_user_vms(chat_id)
        if count >= 5:
            print("DEBUG: Quota Exceeded")
            send_msg(chat_id, "❌ **Quota Exceeded**\nYou have 5 active VMs. Use /del to cleanup.")
            return

        print("DEBUG: Sending Region Keyboard")
        kb = get_region_keyboard()
        send_msg(chat_id, "🌏 **Select VPN Region:**", reply_markup=kb)

    elif cmd == "/status":
        client = get_compute_client()

        request = compute_v1.AggregatedListInstancesRequest()
        request.project = CFG['project']
        request.filter = f"labels.owner-id={chat_id} AND status=RUNNING"

        agg_list = client.aggregated_list(request=request)

        found = False
        send_msg(chat_id, "🔍 **Scanning for active instances...**")

        for zone_path, response in agg_list:
            if response.instances:
                zone_name = zone_path.split("/")[-1]
                for vm in response.instances:
                    found = True
                    # IP Logic
                    ip = "No IP"
                    if len(vm.network_interfaces) > 0 and len(vm.network_interfaces[0].access_configs) > 0:
                        ip = vm.network_interfaces[0].access_configs[0].nat_i_p

                    # Uptime
                    uptime_str = "N/A"
                    try:
                        start_dt = datetime.datetime.fromisoformat(vm.creation_timestamp)
                        now_dt = datetime.datetime.now(datetime.timezone.utc)
                        diff = now_dt - start_dt
                        uptime_str = str(datetime.timedelta(seconds=int(diff.total_seconds())))
                    except: pass

                    msg_text = (
                        f"🌍 **Region:** `{zone_name}`\n"
                        f"🌐 **IP:** `{ip}`\n"
                        f"⏱️ **Uptime:** `{uptime_str}`\n"
                        f"💰 **Est. Cost:** `~$0.004/h`"
                    )

                    kb = {"inline_keyboard": [[
                        {"text": "💣 Destroy", "callback_data": f"destroy:{zone_name}:{vm.name}"}
                    ]]}

                    send_msg(chat_id, msg_text, reply_markup=kb)

        if not found:
            send_msg(chat_id, "🤷‍♂️ **No active VMs found.**\nUse /new to deploy one.")

    elif cmd == "/del":
        send_msg(chat_id, "🗑️ **Deleting all active VMs...**")
        count = delete_all_vms(chat_id)
        send_msg(chat_id, f"✅ **Deleted {count} instances.**")

    elif cmd == "/log":
        # Simple Health Check / Debug Info
        try:
            count = count_user_vms(chat_id)
            status_msg = (
                f"🛠️ **System Diagnostics**\n\n"
                f"🆔 **Project ID:** `{CFG['project']}`\n"
                f"👤 **Your ID:** `{chat_id}`\n"
                f"🔢 **Active VMs (API Check):** `{count}`\n"
                f"✅ **Authorized Users:** `{len(CFG['authorized_users'])}`\n"
                f"⚙️ **Config Loaded:** `True`"
            )
            send_msg(chat_id, status_msg)
        except Exception as e:
            send_msg(chat_id, f"❌ **Diagnostics Failed**\nError: `{str(e)}`")

def handle_callback(cb):
    chat_id = str(cb.get('message', {}).get('chat', {}).get('id'))
    msg_id = cb.get('message', {}).get('message_id')
    data = cb.get('data')
    cb_id = cb.get('id')

    if chat_id not in CFG["authorized_users"]:
        answer_callback(cb_id, "Unauthorized")
        return

    if data.startswith("region:"):
        zone = data.split(":")[1]
        kb = get_peers_keyboard(zone)
        edit_msg(chat_id, msg_id, f"📱 **Select Number of Devices for {zone}:**", reply_markup=kb)
        answer_callback(cb_id)

    elif data.startswith("deploy:"):
        # deploy:zone:peers
        parts = data.split(":")
        zone = parts[1]
        peers = int(parts[2])

        # 1. Validate Zone (Security)
        valid_zones = REGION_MAP.values()
        if zone not in valid_zones:
            answer_callback(cb_id, "Invalid Region")
            return

        # 2. Re-check Quota (Race Condition)
        if count_user_vms(chat_id) >= 5:
            answer_callback(cb_id, "Quota Exceeded (5 Max)")
            send_msg(chat_id, "❌ **Deployment Failed:** You already have 5 active VMs.")
            return

        answer_callback(cb_id, "Deploying...")
        edit_msg(chat_id, msg_id, f"🚀 **Deploying f1-micro in {zone}...**\n(Approx. 3 mins for setup & QR code delivery)")

        success, err = launch_vm(chat_id, zone, peers)
        if not success:
             send_msg(chat_id, f"❌ **Deployment Failed**\n{err}")

    elif data.startswith("destroy:"):
        # destroy:zone:name
        parts = data.split(":")
        zone = parts[1]
        name = parts[2]

        answer_callback(cb_id, "Terminating...")
        delete_vm(zone, name)
        edit_msg(chat_id, msg_id, f"💀 **Terminated** `{name}`")

