from schema_discovery import discover_schema
import json
from gnmi_tools import load_device_info

# Load device info from hosts.json
devices = load_device_info().get("devices", {})
print("\nAvailable devices from hosts.json:")
print(json.dumps(devices, indent=2))

# Get first device's connection details
if devices:
    first_device = next(iter(devices.values()))
    target = first_device["target"]
    username = first_device["username"]
    password = first_device["password"]
    print(f"\nAttempting to connect to {target}")
else:
    print("No devices found in hosts.json")
    exit(1)

# Get supported YANG models
try:
    print(f"\nCalling discover_schema with:")
    print(f"target: {target}")
    print(f"username: {username}")
    print(f"password: {password}")
    
    models = discover_schema(
        target=target,
        username=username,
        password=password
    )
    
    print("\nSupported YANG Models:")
    print("=====================")
    if models:
        for model in models:
            print(f"\nName: {model.get('name')}")
            print(f"Organization: {model.get('organization')}")
            print(f"Version: {model.get('version')}")
    else:
        print("No models returned")
except Exception as e:
    print(f"\nError: {str(e)}")
    print("Exception type:", type(e).__name__)
