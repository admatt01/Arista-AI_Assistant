"""Wrapper module for gNMI operations providing async interface."""

import json
from typing import Dict, Any, Union, List
from datetime import datetime, timezone
import pytz
from gnmi_config import (
    set_gnmi_config,
    get_gnmi_data,
    apply_cli_commands as apply_cli_cmds,
    cleanup_connections
)
from pathlib import Path

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

def load_device_info():
    """Load device information from hosts.json."""
    try:
        with open('hosts.json', 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading hosts.json: {e}")
        return {"devices": {}}

def resolve_target(target_spec: str) -> Dict[str, str]:
    """Resolve a single target specification to connection details.
    
    Args:
        target_spec: Hostname or target address
        
    Returns:
        Dict containing target, username, and password
    """
    devices = load_device_info().get("devices", {})
    
    # Check if it's a hostname in our devices
    if target_spec in devices:
        device = devices[target_spec]
        return {
            "target": device["target"],
            "username": device["username"],
            "password": device["password"]
        }
    else:
        # Assume it's a direct target address
        first_device = next(iter(devices.values()))
        return {
            "target": target_spec,
            "username": first_device["username"],
            "password": first_device["password"]
        }

async def gnmi_configure(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute gNMI configuration.
    
    Args:
        arguments: Dictionary containing:
            - target: Target device or list of devices (hostname or address)
            - path: gNMI path
            - value: Value to set
            - username: Optional device username (if not using hostname)
            - password: Optional device password (if not using hostname)
            
    Returns:
        Dict containing operation result
    """
    try:
        target_spec = arguments.get("target")
        path = arguments.get("path")
        value = arguments.get("value")
        
        # For CLI commands, ensure proper path format
        if path == "cli:" and not value.startswith("cli:"):
            path = "cli:"
        
        # Handle multiple targets concurrently
        if isinstance(target_spec, list):
            import asyncio
            tasks = []
            for t in target_spec:
                conn_info = resolve_target(t)
                tasks.append(
                    set_gnmi_config(
                        target=conn_info["target"],
                        path=path,
                        value=value,
                        username=conn_info["username"],
                        password=conn_info["password"]
                    )
                )
            responses = await asyncio.gather(*tasks)
            return {
                "status": "success",
                "responses": responses
            }
        else:
            # Single target
            conn_info = resolve_target(target_spec)
            response = await set_gnmi_config(
                target=conn_info["target"],
                path=path,
                value=value,
                username=conn_info["username"],
                password=conn_info["password"]
            )
            return response
            
    except Exception as e:
        return {"error": str(e)}
    finally:
        # Cleanup connections if there was an error
        if 'error' in locals():
            cleanup_connections()

async def gnmi_get(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Retrieve data from an Arista device using gNMI.
    
    Args:
        arguments: Dictionary containing:
            - target: Target device or list of devices (hostname or address)
            - path: gNMI path to query
            - username: Optional device username (if not using hostname)
            - password: Optional device password (if not using hostname)
            
    Returns:
        Dict containing retrieved data
    """
    try:
        target_spec = arguments.get("target")
        path = arguments.get("path")
        
        # Handle multiple targets concurrently
        if isinstance(target_spec, list):
            import asyncio
            tasks = []
            for t in target_spec:
                conn_info = resolve_target(t)
                tasks.append(
                    get_gnmi_data(
                        target=conn_info["target"],
                        path=path,
                        username=conn_info["username"],
                        password=conn_info["password"]
                    )
                )
            responses = await asyncio.gather(*tasks)
            return {
                "status": "success",
                "responses": responses
            }
        else:
            # Single target
            conn_info = resolve_target(target_spec)
            response = await get_gnmi_data(
                target=conn_info["target"],
                path=path,
                username=conn_info["username"],
                password=conn_info["password"]
            )
            return response
            
    except Exception as e:
        return {"error": str(e)}
    finally:
        # Cleanup connections if there was an error
        if 'error' in locals():
            cleanup_connections()

async def apply_cli_commands(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute multiple CLI commands via gNMI.
    
    Args:
        arguments: Dictionary containing:
            - target: Target device or list of devices (hostname or address)
            - commands: List of CLI commands to execute
            - username: Optional device username (if not using hostname)
            - password: Optional device password (if not using hostname)
            
    Returns:
        Dict containing execution results
    """
    try:
        target_spec = arguments.get("target")
        commands = arguments.get("commands")
        
        if not isinstance(commands, list):
            raise ValueError("Commands must be provided as a list")
        
        # Handle target resolution
        if isinstance(target_spec, list):
            resolved_targets = []
            usernames = set()
            passwords = set()
            
            for t in target_spec:
                conn_info = resolve_target(t)
                resolved_targets.append(conn_info["target"])
                usernames.add(conn_info["username"])
                passwords.add(conn_info["password"])
            
            # Ensure consistent credentials
            if len(usernames) > 1 or len(passwords) > 1:
                raise ValueError("Inconsistent credentials across specified devices")
                
            target = resolved_targets
            username = usernames.pop()
            password = passwords.pop()
        else:
            conn_info = resolve_target(target_spec)
            target = conn_info["target"]
            username = conn_info["username"]
            password = conn_info["password"]
        
        response = await apply_cli_cmds({
            "target": target,
            "commands": commands,
            "username": username,
            "password": password
        })
        return response
        
    except Exception as e:
        return {"error": str(e)}
    finally:
        # Cleanup connections if there was an error
        if 'error' in locals():
            cleanup_connections()
