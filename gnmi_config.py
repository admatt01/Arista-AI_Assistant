"""Module for gNMI configuration operations using pygnmi."""

import json
import yaml
import asyncio
import dictdiffer
from typing import List, Dict, Any, Union
from pygnmi.client import gNMIclient
from contextlib import contextmanager
from datetime import datetime, timezone
import pytz

# Connection pool to reuse gNMI connections
connection_pool: Dict[str, gNMIclient] = {}

def convert_epoch_to_aedt(epoch_ns: Union[int, float, None]) -> str:
    """Convert nanosecond epoch timestamp to AEDT time string.
    
    Args:
        epoch_ns: Epoch timestamp in nanoseconds
        
    Returns:
        str: Formatted datetime string in AEDT
    """
    try:
        if epoch_ns is None:
            return ""
        # Convert nanoseconds to seconds
        epoch_sec = float(epoch_ns) / 1e9
        # Convert to datetime
        utc_dt = datetime.fromtimestamp(epoch_sec, timezone.utc)
        # Convert to AEDT
        aedt = pytz.timezone('Australia/Sydney')
        aedt_dt = utc_dt.astimezone(aedt)
        # Format the datetime
        return aedt_dt.strftime('%Y-%m-%d %H:%M:%S %Z')
    except (ValueError, TypeError, OverflowError):
        return str(epoch_ns)  # Return original value if conversion fails

def format_uptime(value: Union[int, float, str, None]) -> str:
    """Convert uptime to human readable format.
    
    Args:
        value: Uptime in nanoseconds, seconds, or string format
        
    Returns:
        str: Formatted uptime string
    """
    try:
        if value is None:
            return ""
            
        # Convert string to float if needed
        if isinstance(value, str):
            if 'seconds' in value:
                # Extract numeric value if it's in format "X seconds"
                value = float(value.split()[0])
            else:
                value = float(value)
        
        # Determine if value is in nanoseconds or seconds
        # If value is very large (> 1e11), assume it's in nanoseconds
        if value > 1e11:
            seconds = value / 1e9
        else:
            seconds = value
            
        # Convert to days, hours, minutes, seconds
        days = int(seconds // (24 * 3600))
        seconds %= (24 * 3600)
        hours = int(seconds // 3600)
        seconds %= 3600
        minutes = int(seconds // 60)
        seconds = int(seconds % 60)
        
        return f"{days}d {hours}h {minutes}m {seconds}s"
        
    except (ValueError, TypeError):
        return str(value)  # Return original value if conversion fails

@contextmanager
def get_gnmi_connection(target: str, username: str, password: str):
    """Get or create a gNMI connection from the pool.
    
    Args:
        target: Target device address including port
        username: Device username
        password: Device password
        
    Yields:
        gNMIclient: Connected gNMI client
    """
    connection_key = f"{target}:{username}"
    
    if connection_key not in connection_pool:
        host, port = target.split(':')
        gnmi_opts = {
            'target': (host, int(port)),
            'username': username,
            'password': password,
            'insecure': True,
            'override': 'ascii',
            'encoding': 'ascii'
        }
        connection_pool[connection_key] = gNMIclient(**gnmi_opts)
        connection_pool[connection_key].__enter__()
    
    try:
        yield connection_pool[connection_key]
    except Exception as e:
        # If there's an error, remove the connection from pool
        if connection_key in connection_pool:
            connection_pool[connection_key].__exit__(type(e), e, e.__traceback__)
            del connection_pool[connection_key]
        raise

async def set_gnmi_config(target: str, path: str, value: str, username: str, password: str) -> dict:
    """Execute gNMI SET operation asynchronously.
    
    Args:
        target: Target device address including port
        path: gNMI path
        value: Value to set
        username: Device username
        password: Device password
        
    Returns:
        Dict containing operation result
    """
    try:
        print(f"=== gNMI SET Request Details ===")
        print(f"Target: {target}")
        print(f"Path: {path}")
        print(f"Value: {value}")
        
        with get_gnmi_connection(target, username, password) as gc:
            if path.startswith("cli:"):
                response = gc.set(
                    encoding='ascii',
                    update=[
                        (path, value, 'ascii')
                    ]
                )
            else:
                response = gc.set(
                    update=[
                        (path, value)
                    ]
                )
                
            return response
            
    except Exception as e:
        print(f"\nError occurred: {str(e)}")
        raise Exception(f"gNMI SET operation failed: {str(e)}")

async def get_gnmi_data(target: str, path: str, username: str, password: str) -> dict:
    """Retrieve data from an Arista device using gNMI GET asynchronously.
    
    Args:
        target: Target device address including port
        path: The path to query
        username: Device username
        password: Device password
        
    Returns:
        Dict containing the retrieved data
    """
    try:
        print(f"=== gNMI GET Request Details ===")
        print(f"Target: {target}")
        print(f"Path: {path}")
        
        with get_gnmi_connection(target, username, password) as gc:
            # Handle different path types
            if path.startswith("cli:/"):
                path = path.replace("cli:/", "cli:")
            elif path.startswith("eos_native:"):
                # pygnmi handles eos_native paths directly
                pass
            
            response = gc.get(path=[path])
            
            results = []
            if 'notification' in response:
                for notif in response['notification']:
                    timestamp = notif.get('timestamp', 0)
                    for update in notif.get('update', []):
                        # Create update entry with original values
                        update_entry = {
                            'timestamp': timestamp,
                            'path': update.get('path', ''),
                            'value': update.get('val')
                        }
                        
                        # Only convert timestamps if not a CLI response
                        if not path.startswith('cli:'):
                            # Convert timestamp if present
                            if timestamp:
                                update_entry['timestamp'] = convert_epoch_to_aedt(timestamp)
                            
                            # Handle uptime values
                            val = update.get('val')
                            path_str = str(update.get('path', '')).lower()
                            if any(term in path_str for term in ['uptime', 'up/down']):
                                update_entry['value'] = format_uptime(val)
                        
                        results.append(update_entry)
            
            return {'updates': results}
            
    except Exception as e:
        print(f"\nError occurred: {str(e)}")
        raise Exception(f"gNMI GET operation failed: {str(e)}")

def interpret_gnmi_response(response: Dict) -> Dict:
    """Interpret the gNMI response to determine if operation was successful.
    
    Args:
        response: The pygnmi response dictionary
        
    Returns:
        Dict containing status and message
    """
    if not response:
        return {"status": "failure", "message": "Empty response"}
    
    # pygnmi returns a dict with 'notification' key for successful operations
    if 'notification' in response:
        return {"status": "success", "message": "Operation completed successfully."}
    
    error_msg = response.get('error', 'Unknown error')
    return {"status": "failure", "message": str(error_msg)}

def compare_configs(config1: Dict, config2: Dict, path_prefix: str = "") -> List[Dict]:
    """Compare two configurations and return their differences.
    
    Args:
        config1: First configuration dictionary
        config2: Second configuration dictionary
        path_prefix: Optional prefix for the diff paths
            
    Returns:
        List of differences with human-readable format
    """
    differences = []
    for diff in dictdiffer.diff(config1, config2):
        change_type, path, change = diff
        
        # Format the path
        if isinstance(path, str):
            full_path = f"{path_prefix}/{path}" if path_prefix else path
        else:
            path_str = "/".join(str(p) for p in path)
            full_path = f"{path_prefix}/{path_str}" if path_prefix else path_str
        
        if change_type == "add":
            for k, v in change:
                differences.append({
                    "type": "added",
                    "path": f"{full_path}/{k}",
                    "value": v
                })
        elif change_type == "remove":
            for k, v in change:
                differences.append({
                    "type": "removed",
                    "path": f"{full_path}/{k}",
                    "value": v
                })
        elif change_type == "change":
            differences.append({
                "type": "changed",
                "path": full_path,
                "old_value": change[0],
                "new_value": change[1]
            })
        
    return differences

def compare_acls(acl1: Dict, acl2: Dict) -> Dict[str, Any]:
    """Compare two ACL configurations and provide detailed analysis.
    
    Args:
        acl1: First ACL configuration
        acl2: Second ACL configuration
            
    Returns:
        Dictionary containing:
            - differences: List of specific differences
            - summary: Summary of changes
            - compliance: Compliance check results
    """
    differences = compare_configs(acl1, acl2, path_prefix="acl")
    
    # Analyze differences for compliance
    compliance_issues = []
    critical_changes = []
    
    for diff in differences:
        # Check for critical changes (e.g., permit/deny changes)
        if "action" in diff["path"].lower():
            if diff["type"] == "changed":
                critical_changes.append(
                    f"Rule action changed at {diff['path']}: {diff['old_value']} -> {diff['new_value']}"
                )
        
        # Check for compliance issues
        if diff["type"] == "removed":
            compliance_issues.append(
                f"Potentially required rule removed at {diff['path']}"
            )
    
    # Generate summary
    summary = {
        "total_differences": len(differences),
        "added_rules": len([d for d in differences if d["type"] == "added"]),
        "removed_rules": len([d for d in differences if d["type"] == "removed"]),
        "modified_rules": len([d for d in differences if d["type"] == "changed"]),
        "critical_changes": len(critical_changes)
    }
    
    return {
        "differences": differences,
        "summary": summary,
        "compliance": {
            "issues": compliance_issues,
            "critical_changes": critical_changes,
            "compliant": len(compliance_issues) == 0 and len(critical_changes) == 0
        }
    }

async def apply_config_from_file(target: str, config_data: dict, username: str, password: str):
    """Apply configuration from a parsed JSON/YAML data structure asynchronously.
    
    Args:
        target: Switch address including port (e.g., "switch:6030")
        config_data: Parsed configuration data
        username: Switch username
        password: Switch password
        
    Returns:
        List of responses from each configuration command
    """
    print("\n=== Applying Configuration from File ===")
    print(f"Target switch: {target}")
    print("Configuration commands to be applied:")
    for update in config_data.get("updates", []):
        print(f"- {update.get('value')}")
    
    responses = []
    
    try:
        with get_gnmi_connection(target, username, password) as gc:
            for update in config_data.get("updates", []):
                path = update.get("path", "cli:")
                value = update.get("value")
                
                print(f"\nApplying command: {value}")
                
                response = gc.set(update=[
                    (path, value)
                ])
                
                responses.append({
                    "command": value,
                    "response": response
                })
            
        return responses
        
    except Exception as e:
        print(f"Error applying configuration: {str(e)}")
        raise

async def load_and_apply_config(target: str, file_content: str, file_format: str, username: str, password: str):
    """Load configuration from file content and apply it to the switch asynchronously.
    
    Args:
        target: Switch address including port
        file_content: Content of the configuration file
        file_format: Format of the file ('json' or 'yaml')
        username: Switch username
        password: Switch password
        
    Returns:
        List of responses from configuration application
    """
    try:
        if file_format.lower() == 'json':
            config_data = json.loads(file_content)
        elif file_format.lower() in ['yaml', 'yml']:
            config_data = yaml.safe_load(file_content)
        else:
            raise ValueError(f"Unsupported file format: {file_format}")
        
        return await apply_config_from_file(target, config_data, username, password)
        
    except Exception as e:
        print(f"Error processing configuration file: {str(e)}")
        raise

async def apply_cli_commands(params: dict):
    """Apply CLI commands to multiple devices concurrently."""
    targets = params["target"] if isinstance(params["target"], list) else [params["target"]]
    commands = params["commands"]
    username = params["username"]
    password = params["password"]

    async def configure_single_device(target: str):
        print(f"\n=== Applying CLI Commands to {target} ===")
        
        # Filter out configuration mode commands and create proper configuration block
        filtered_commands = []
        in_config_block = False
        
        for cmd in commands:
            cmd = cmd.strip()
            
            # Skip empty commands and configuration mode commands
            if not cmd or cmd in ['configure terminal', 'end', 'exit']:
                continue
                
            # Add command to filtered list
            filtered_commands.append(cmd)

        if not filtered_commands:
            print("No valid configuration commands found")
            return {
                "target": target,
                "status": "failure",
                "confirmation": "No valid configuration commands to apply",
                "results": []
            }

        print("Applying configuration commands:")
        for cmd in filtered_commands:
            print(f"- {cmd}")

        results = []
        
        try:
            config_block = '\n'.join(filtered_commands)
            with get_gnmi_connection(target, username, password) as gc:
                try:
                    # Send configuration as a single block
                    response = gc.set(
                        encoding='ascii',
                        update=[
                            ('cli:', config_block, 'ascii')
                        ]
                    )
                    
                    # Check response
                    if response:
                        print("Configuration response received:", response)
                        status = "success"
                        message = "Configuration applied successfully"
                    else:
                        status = "failure"
                        message = "No confirmation received from device"
                        
                    results.append({
                        "command": config_block,
                        "status": status,
                        "response": message
                    })
                    
                except Exception as e:
                    error_msg = str(e)
                    print(f"Error applying configuration: {error_msg}")
                    results.append({
                        "command": config_block,
                        "status": "failed",
                        "response": error_msg
                    })

            all_succeeded = all(r["status"] == "success" for r in results)
            return {
                "target": target,
                "status": "success" if all_succeeded else "failure",
                "confirmation": f"Configuration {'successful' if all_succeeded else 'failed'} on {target}",
                "results": results
            }

        except Exception as e:
            error_msg = str(e)
            print(f"Connection error: {error_msg}")
            return {
                "target": target,
                "status": "failure",
                "confirmation": error_msg,
                "results": results
            }

    # Execute configuration tasks concurrently
    tasks = [configure_single_device(target) for target in targets]
    device_results = await asyncio.gather(*tasks)

    if len(targets) == 1:
        return device_results[0]

    # Process overall results
    all_succeeded = all(r["status"] == "success" for r in device_results)
    successful_devices = [r["target"] for r in device_results if r["status"] == "success"]
    failed_devices = [r["target"] for r in device_results if r["status"] != "success"]

    return {
        "status": "success" if all_succeeded else "partial_success" if successful_devices else "failure",
        "confirmation": (
            "All devices configured successfully" if all_succeeded
            else f"Successfully configured {len(successful_devices)} devices, {len(failed_devices)} failed"
        ),
        "successful_devices": successful_devices,
        "failed_devices": failed_devices,
        "device_results": device_results
    }

def cleanup_connections():
    """Cleanup all connections in the pool."""
    for key, connection in connection_pool.items():
        try:
            connection.__exit__(None, None, None)
        except:
            pass
    connection_pool.clear()
