import time
import datetime
import requests
import functions_framework
from google.cloud import compute_v1

# Professional Configuration
CFG = {
    "token": "####your_token####",
    "chat_id": "####your_chat_id####",
    "project": "majestic-cairn-487303-b3",
    "zone": "asia-east1-c",
    "prefix": "vpn-svr",
    "machine": "e2-micro",
    "hourly_rate": 0.005
}

compute = compute_v1.InstancesClient()

def notify(text):
    url = f"https://api.telegram.org/bot{CFG['token']}/sendMessage"
    requests.post(url, json={"chat_id": CFG['chat_id'], "text": f"[GCP Control] {text}"})

def get_active_vm():
    items = compute.list(project=CFG['project'], zone=CFG['zone'])
    for vm in items:
        if CFG['prefix'] in vm.name and vm.status == "RUNNING":
            return vm
    return None

def cleanup():
    items = compute.list(project=CFG['project'], zone=CFG['zone'])
    count = 0
    for vm in items:
        if CFG['prefix'] in vm.name:
            compute.delete(project=CFG['project'], zone=CFG['zone'], instance=vm.name)
            count += 1
    return count

@functions_framework.http
def deploy_vpn(request):
    data = request.get_json(silent=True)
    if not data or 'message' not in data: return "OK"
    
    msg = data['message']
    uid = str(msg['from'].get('id'))
    cmd = msg.get('text', '').lower()

    if uid != CFG['chat_id']: return "Forbidden", 403

    # --- COMMAND: NEW ---
    if cmd == "/new":
        cleanup()
        name = f"{CFG['prefix']}-{int(time.time())}"
        
        script = f"""#!/bin/bash
curl -s -X POST "https://api.telegram.org/bot{CFG['token']}/sendMessage" -d "chat_id={CFG['chat_id']}" -d "text=🛠️ Initializing Environment..."
docker volume create ovpn-data
IP=$(curl -s ifconfig.me)
docker run -v ovpn-data:/etc/openvpn --rm kylemanna/openvpn ovpn_genconfig -u udp://$IP
echo "yes" | docker run -v ovpn-data:/etc/openvpn --rm -i kylemanna/openvpn ovpn_initpki nopass
docker run -v ovpn-data:/etc/openvpn -d -p 1194:1194/udp --cap-add=NET_ADMIN --restart always --name ovpn kylemanna/openvpn
sleep 80
docker exec -t ovpn easyrsa build-client-full client1 nopass
docker exec -t ovpn ovpn_getclient client1 > /tmp/client1.ovpn
curl -F "chat_id={CFG['chat_id']}" -F "document=@/tmp/client1.ovpn" -F "caption=✅ VPN Ready: client1 (e2-micro)" https://api.telegram.org/bot{CFG['token']}/sendDocument
"""
        spec = {
            "name": name,
            "machine_type": f"zones/{CFG['zone']}/machineTypes/{CFG['machine']}",
            "can_ip_forward": True,
            "tags": {"items": ["vpn-server"]},
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
        compute.insert(project=CFG['project'], zone=CFG['zone'], instance_resource=spec)
        notify(f"Deploying {CFG['machine']} (Singleton).")

    # --- COMMAND: STATUS (Fix Applied) ---
    elif cmd == "/status":
        vm = get_active_vm()
        if not vm:
            notify("Status: Inactive. Cost: $0.00")
        else:
            # FIX: Use nat_i_p instead of nat_ip, and add error handling
            try:
                if len(vm.network_interfaces) > 0 and len(vm.network_interfaces[0].access_configs) > 0:
                    ip = vm.network_interfaces[0].access_configs[0].nat_i_p 
                else:
                    ip = "No Public IP"
            except Exception as e:
                ip = "IP Lookup Error"

            # Calculate Uptime
            try:
                start_str = vm.creation_timestamp
                # GCP timestamps usually have timezone info, handling it safely
                if "." in start_str:
                    # Truncate microseconds if format is weird, or just parse directly
                    pass 
                
                # Using simple string parsing or dateutil if available, but staying safe with basic split
                # This is a robust fallback if isoformat fails
                start_dt = datetime.datetime.fromisoformat(start_str)
                now_dt = datetime.datetime.now(datetime.timezone.utc)
                diff = now_dt - start_dt
                
                hours = diff.total_seconds() / 3600
                cost = hours * CFG['hourly_rate']
                # Format uptime nicely: HH:MM:SS
                uptime_str = str(datetime.timedelta(seconds=int(diff.total_seconds())))
            except Exception as e:
                uptime_str = "Calc..."
                cost = 0.0

            msg = (
                f"🟢 **RUNNING**\n"
                f"🌐 `{ip}`\n"
                f"⏱️ Uptime: `{uptime_str}`\n"
                f"💰 Est. Cost: `${cost:.4f}`"
            )
            requests.post(
                f"https://api.telegram.org/bot{CFG['token']}/sendMessage", 
                json={"chat_id": CFG['chat_id'], "text": msg, "parse_mode": "Markdown"}
            )

    # --- COMMAND: DEL ---
    elif cmd == "/del":
        num = cleanup()
        notify(f"Terminated {num} VPN instance(s).")

    return "OK"
