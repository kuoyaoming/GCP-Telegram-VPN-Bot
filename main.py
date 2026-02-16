import time
import datetime
import requests
import functions_framework
from google.cloud import compute_v1
import random
import string

# Professional Configuration
CFG = {
    "token": "####your_token####",
    "chat_id": "####your_chat_id####", # This is the Admin ID
    "project": "majestic-cairn-487303-b3",
    "default_zone": "asia-east1-c",
    "prefix": "vpn-svr",
    "machine": "e2-micro",
    "hourly_rate": 0.005
}

# --- Global State (In-Memory, Volatile on GCF) ---
# 5 Hardcoded active codes for testing.
# WARNING: These reset if the Cloud Function instance restarts.
ACTIVE_CODES = {'111111', '222222', '333333', '444444', '555555'}
AUTHORIZED_USERS = {CFG['chat_id']} # Admin is authorized by default

# Initialize Clients
compute = compute_v1.InstancesClient()
regions_client = compute_v1.RegionsClient()

# Global Cache for Regions
ALL_REGIONS = []

def get_regions():
    global ALL_REGIONS
    if not ALL_REGIONS:
        try:
            request = compute_v1.ListRegionsRequest(project=CFG['project'])
            ALL_REGIONS = sorted([r.name for r in regions_client.list(request=request)])
        except Exception as e:
            print(f"Error fetching regions: {e}")
            return []
    return ALL_REGIONS

def get_region_keyboard(page=0, items_per_page=10):
    regions = get_regions()
    if not regions:
        return {"inline_keyboard": [[{"text": "❌ Error: No Regions Found", "callback_data": "none"}]]}

    total_pages = (len(regions) + items_per_page - 1) // items_per_page
    start = page * items_per_page
    end = start + items_per_page
    current_page_regions = regions[start:end]

    keyboard = []
    # 2 columns per row
    row = []
    for r in current_page_regions:
        row.append({"text": r, "callback_data": f"region:{r}"})
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # Navigation Buttons
    nav_row = []
    if page > 0:
        nav_row.append({"text": "⬅️ Prev", "callback_data": f"page:{page-1}"})
    if page < total_pages - 1:
        nav_row.append({"text": "Next ➡️", "callback_data": f"page:{page+1}"})

    if nav_row:
        keyboard.append(nav_row)

    return {"inline_keyboard": keyboard}

def edit_msg(chat_id, message_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{CFG['token']}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(url, json=payload)

def send_msg(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{CFG['token']}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(url, json=payload)

def answer_callback(callback_query_id, text=None):
    url = f"https://api.telegram.org/bot{CFG['token']}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    requests.post(url, json=payload)

def is_admin(chat_id):
    return str(chat_id) == str(CFG['chat_id'])

def get_zones(region):
    try:
        request = compute_v1.ListZonesRequest(project=CFG['project'], filter=f"name eq {region}-*")
        # Ensure we only get zones in that region
        zones = [z.name for z in compute_v1.ZonesClient().list(request=request)]
        return zones
    except Exception:
        # Fallback if list fails
        return [f"{region}-a", f"{region}-b", f"{region}-c"]

def count_user_vms(chat_id):
    # Filter by label owner_id
    f = f"labels.owner_id={chat_id} AND status=RUNNING"
    request = compute_v1.ListInstancesRequest(project=CFG['project'], zone="-", filter=f)
    agg_list = compute.aggregated_list(request=request)
    count = 0
    for _, response in agg_list:
        if response.instances:
            count += len(response.instances)
    return count

def delete_user_vms(chat_id):
    # Filter by label owner_id (all statuses)
    f = f"labels.owner_id={chat_id}"
    request = compute_v1.ListInstancesRequest(project=CFG['project'], zone="-", filter=f)
    agg_list = compute.aggregated_list(request=request)
    deleted_count = 0
    
    for zone_path, response in agg_list:
        if response.instances:
            zone = zone_path.split("/")[-1]
            for vm in response.instances:
                try:
                    compute.delete(project=CFG['project'], zone=zone, instance=vm.name)
                    deleted_count += 1
                except Exception as e:
                    print(f"Error deleting {vm.name}: {e}")
    return deleted_count

def get_all_active_vms_count():
    # Count all instances with 'owner_id' label
    f = "labels.owner_id:*"
    request = compute_v1.ListInstancesRequest(project=CFG['project'], zone="-", filter=f)
    agg_list = compute.aggregated_list(request=request)
    count = 0
    for _, response in agg_list:
        if response.instances:
            count += len(response.instances)
    return count

def deploy_vm_logic(chat_id, region):
    # 1. Check Quota
    if count_user_vms(chat_id) >= 5:
        send_msg(chat_id, "❌ **Quota Exceeded**\nYou have reached the limit of 5 active VMs.\nUse /del to terminate old ones.")
        return

    # 2. Select Zone (Random valid zone)
    zones = get_zones(region)
    if not zones:
        send_msg(chat_id, "❌ Error: No zones found in this region.")
        return
    zone = random.choice(zones)

    # 3. Prepare Configuration
    name = f"{CFG['prefix']}-{chat_id}-{int(time.time())}"

    # Dynamic Startup Script
    # Note: We inject the specific chat_id into the script so the VM sends the file to the right user.
    script = f"""#!/bin/bash
curl -s -X POST "https://api.telegram.org/bot{CFG['token']}/sendMessage" -d "chat_id={chat_id}" -d "text=🛠️ **Initializing {region}...**"
docker volume create ovpn-data
IP=$(curl -s ifconfig.me)
docker run -v ovpn-data:/etc/openvpn --rm kylemanna/openvpn ovpn_genconfig -u udp://$IP
echo "yes" | docker run -v ovpn-data:/etc/openvpn --rm -i kylemanna/openvpn ovpn_initpki nopass
docker run -v ovpn-data:/etc/openvpn -d -p 1194:1194/udp --cap-add=NET_ADMIN --restart always --name ovpn kylemanna/openvpn
sleep 80
docker exec -t ovpn easyrsa build-client-full client1 nopass
docker exec -t ovpn ovpn_getclient client1 > /tmp/client1.ovpn
curl -F "chat_id={chat_id}" -F "document=@/tmp/client1.ovpn" -F "caption=✅ **VPN Ready** ({region})" https://api.telegram.org/bot{CFG['token']}/sendDocument
"""

    spec = {
        "name": name,
        "machine_type": f"zones/{zone}/machineTypes/{CFG['machine']}",
        "can_ip_forward": True,
        "tags": {"items": ["vpn-server"]},
        "labels": {"owner_id": str(chat_id)}, # TAGGING THE USER
        "scheduling": {"provisioning_model": "SPOT"},
        "disks": [{
            "boot": True,
            "auto_delete": True,
            "initialize_params": {
                "source_image": "projects/cos-cloud/global/images/family/cos-stable"
            }
        }],
        "network_interfaces": [{
            "network": "global/networks/default",
            "access_configs": [{"type": "ONE_TO_ONE_NAT"}]
        }],
        "metadata": {
            "items": [{"key": "startup-script", "value": script}]
        }
    }

    # 4. Create VM
    try:
        compute.insert(project=CFG['project'], zone=zone, instance_resource=spec)
        send_msg(chat_id, f"🚀 **Deployment Started!**\nZone: `{zone}`\n\nYou will receive the configuration file shortly.")
    except Exception as e:
        send_msg(chat_id, f"❌ **Deployment Failed**\nError: `{str(e)}`")

@functions_framework.http
def deploy_vpn(request):
    data = request.get_json(silent=True)
    if not data: return "OK"

    try:
        if 'message' in data:
            handle_message(data['message'])
        elif 'callback_query' in data:
            handle_callback(data['callback_query'])
    except Exception as e:
        print(f"Error: {e}")

    return "OK"

def handle_message(msg):
    chat_id = str(msg.get('chat', {}).get('id'))
    text = msg.get('text', '').strip()
    user_id = str(msg.get('from', {}).get('id'))

    if not text: return

    # Authorization Check
    if chat_id not in AUTHORIZED_USERS:
        # Check if text is a valid code
        if text in ACTIVE_CODES:
            ACTIVE_CODES.remove(text)
            AUTHORIZED_USERS.add(chat_id)
            send_msg(chat_id, "✅ **Access Granted!**\nYou can now use /new to deploy a VPN.")
        else:
            send_msg(chat_id, "🔒 **Access Denied**\nPlease enter a valid activation code.")
        return

    # Commands
    cmd = text.lower().split()[0]

    if cmd == "/start":
        send_msg(chat_id, "👋 Welcome! Use /new to deploy a VPN.")

    elif cmd == "/new":
        kb = get_region_keyboard(page=0)
        send_msg(chat_id, "🌍 **Select a Region:**", reply_markup=kb)

    elif cmd == "/status":
        # Show User VMs
        f = f"labels.owner_id={chat_id} AND status=RUNNING"
        request = compute_v1.ListInstancesRequest(project=CFG['project'], zone="-", filter=f)
        agg_list = compute.aggregated_list(request=request)

        msg_lines = ["🟢 **Your Active VMs:**"]
        found = False

        for zone_path, response in agg_list:
            if response.instances:
                zone_name = zone_path.split("/")[-1]
                for vm in response.instances:
                    found = True
                    # IP Logic
                    ip = "No IP"
                    if len(vm.network_interfaces) > 0 and len(vm.network_interfaces[0].access_configs) > 0:
                        ip = vm.network_interfaces[0].access_configs[0].nat_i_p

                    # Uptime Logic
                    uptime_str = "N/A"
                    try:
                        start_dt = datetime.datetime.fromisoformat(vm.creation_timestamp)
                        now_dt = datetime.datetime.now(datetime.timezone.utc)
                        diff = now_dt - start_dt
                        uptime_str = str(datetime.timedelta(seconds=int(diff.total_seconds())))
                    except: pass

                    msg_lines.append(f"\n🌍 `{zone_name}` | 🌐 `{ip}`\n⏱️ `{uptime_str}`")

        if not found:
            send_msg(chat_id, "You have no active VMs.\nUse /new to create one.")
        else:
            send_msg(chat_id, "\n".join(msg_lines))

    elif cmd == "/del":
        # Delete User VMs
        count = delete_user_vms(chat_id)
        if count > 0:
            send_msg(chat_id, f"🗑️ **Deleted {count} VM(s).**")
        else:
            send_msg(chat_id, "No active VMs to delete.")

    elif cmd == "/gen" and is_admin(chat_id):
        # Admin: Generate Code
        new_code = ''.join(random.choices(string.digits, k=6))
        ACTIVE_CODES.add(new_code)
        send_msg(chat_id, f"🔑 **New Code:** `{new_code}`")

    elif cmd == "/admin" and is_admin(chat_id):
        # Admin: Show Stats
        active_codes_str = ", ".join(ACTIVE_CODES) if ACTIVE_CODES else "None"
        users_list = "\n".join([f"`{u}`" for u in AUTHORIZED_USERS])
        total_vms = get_all_active_vms_count()

        msg = (
            f"📊 **Admin Stats**\n\n"
            f"🖥️ **Total Active VMs:** `{total_vms}`\n"
            f"🔑 **Active Codes:**\n{active_codes_str}\n\n"
            f"👥 **Authorized Users ({len(AUTHORIZED_USERS)}):**\n{users_list}"
        )
        send_msg(chat_id, msg)

def handle_callback(cb):
    chat_id = str(cb.get('message', {}).get('chat', {}).get('id'))
    msg_id = cb.get('message', {}).get('message_id')
    data = cb.get('data')
    cb_id = cb.get('id')

    if chat_id not in AUTHORIZED_USERS:
        answer_callback(cb_id, "Unauthorized")
        return

    if data.startswith("page:"):
        page = int(data.split(":")[1])
        kb = get_region_keyboard(page=page)
        edit_msg(chat_id, msg_id, "🌍 **Select a Region:**", reply_markup=kb)
        answer_callback(cb_id)

    elif data.startswith("region:"):
        region = data.split(":")[1]
        answer_callback(cb_id, f"Selected {region}")
        edit_msg(chat_id, msg_id, f"🚀 **Deploying in {region}...**\nPLEASE WAIT. This may take 2-3 minutes.")

        # Trigger Deployment (Background or direct call?)
        # For GCF, we can call the deploy logic directly here.
        # But we need to make sure we don't time out the HTTP request if it's too long.
        # GCF HTTP timeout is usually 60s. Deployment takes 2-3 mins.
        # We must respond "OK" to Telegram and let the process run?
        # Standard GCF v2 can run up to 60 mins but we need to return response to TG quickly?
        # Actually, if we block here, the webhook might timeout.
        # However, the user wants 'active code' logic first.
        # Let's call a placeholder deploy function for now.
        deploy_vm_logic(chat_id, region)

