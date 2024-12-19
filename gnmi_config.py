"""Module for gNMI configuration operations using pygnmi."""

import json
import yaml
import asyncio
import threading
import dictdiffer
from typing import List, Dict, Any, Union
from pygnmi.client import gNMIclient
from datetime import datetime, timezone
import pytz
from tenacity import retry, stop_after_attempt, wait_exponential

# Configure logging
import logging
logger = logging.getLogger(__name__)

class GNMIConnectionPool:
    """Thread-safe connection pool for gNMI clients."""
    
    def __init__(self):
        self._connections: Dict[str, gNMIclient] = {}
        self._lock = threading.Lock()
        
    def get_connection(self, target: str, username: str, password: str) -> gNMIclient:
        """Get or create a gNMI connection."""
        connection_key = f"{target}:{username}"
        
        with self._lock:
            if connection_key in self._connections:
                return self._connections[connection_key]
            
            host, port = target.split(':')
            gnmi_opts = {
                'target': (host, int(port)),
                'username': username,
                'password': password,
                'insecure': True,
                'timeout': 30  # 30 second timeout
            }
            
            client = gNMIclient(**gnmi_opts)
            try:
                client.__enter__()
                self._connections[connection_key] = client
                return client
            except Exception as e:
                logger.error(f"Failed to create connection for {target}: {str(e)}")
                try:
                    client.__exit__(type(e), e, e.__traceback__)
                except:
                    pass
                raise
                
    def remove_connection(self, target: str, username: str):
        """Remove a connection from the pool."""
        connection_key = f"{target}:{username}"
        
        with self._lock:
            if connection_key in self._connections:
                try:
                    self._connections[connection_key].__exit__(None, None, None)
                except:
                    pass
                del self._connections[connection_key]
                
    def cleanup(self):
        """Clean up all connections in the pool."""
        with self._lock:
            for connection in self._connections.values():
                try:
                    connection.__exit__(None, None, None)
                except:
                    pass
            self._connections.clear()

# Global connection pool instance
connection_pool = GNMIConnectionPool()

def convert_epoch_to_aedt(epoch_ns: Union[int, float, None]) -> str:
    """Convert nanosecond epoch timestamp to AEDT time string."""
    try:
        if epoch_ns is None:
            return ""
        epoch_sec = float(epoch_ns) / 1e9
        utc_dt = datetime.fromtimestamp(epoch_sec, timezone.utc)
        aedt = pytz.timezone('Australia/Sydney')
        aedt_dt = utc_dt.astimezone(aedt)
        return aedt_dt.strftime('%Y-%m-%d %H:%M:%S %Z')
    except (ValueError, TypeError, OverflowError):
        return str(epoch_ns)

def format_uptime(value: Union[int, float, str, None]) -> str:
    """Convert uptime to human readable format."""
    try:
        if value is None:
            return ""
            
        if isinstance(value, str):
            if 'seconds' in value:
                value = float(value.split()[0])
            else:
                value = float(value)
        
        if value > 1e11:
            seconds = value / 1e9
        else:
            seconds = value
            
        days = int(seconds // (24 * 3600))
        seconds %= (24 * 3600)
        hours = int(seconds // 3600)
        seconds %= 3600
        minutes = int(seconds // 60)
        seconds = int(seconds % 60)
        
        return f"{days}d {hours}h {minutes}m {seconds}s"
        
    except (ValueError, TypeError):
        return str(value)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    reraise=True
)
async def set_gnmi_config(target: str, path: str, value: str, username: str, password: str) -> dict:
    """Execute gNMI SET operation with retries."""
    try:
        logger.info(f"=== gNMI SET Request Details ===")
        logger.info(f"Target: {target}")
        logger.info(f"Path: {path}")
        logger.info(f"Value: {value}")
        
        client = connection_pool.get_connection(target, username, password)
        
        try:
            if path.startswith("cli:"):
                # For CLI commands, path must be exactly "cli:" with no additional path elements
                response = client.set(
                    encoding='ascii',
                    update=[
                        ('cli:', value, 'ascii')
                    ]
                )
            else:
                # For non-CLI paths, use JSON encoding
                response = client.set(
                    encoding='json_ietf',
                    update=[
                        (path, value)
                    ]
                )
            return response
            
        except Exception as e:
            # Remove failed connection from pool
            connection_pool.remove_connection(target, username)
            raise
            
    except Exception as e:
        logger.error(f"Error in set_gnmi_config: {str(e)}")
        raise Exception(f"gNMI SET operation failed: {str(e)}")

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    reraise=True
)
async def get_gnmi_data(target: str, path: str, username: str, password: str) -> dict:
    """Retrieve data using gNMI GET with retries."""
    try:
        logger.info(f"=== gNMI GET Request Details ===")
        logger.info(f"Target: {target}")
        logger.info(f"Path: {path}")
        
        client = connection_pool.get_connection(target, username, password)
        
        try:
            if path.startswith("cli:/"):
                path = path.replace("cli:/", "cli:")
                
            response = client.get(path=[path])
            
            results = []
            if 'notification' in response:
                for notif in response['notification']:
                    timestamp = notif.get('timestamp', 0)
                    for update in notif.get('update', []):
                        update_entry = {
                            'timestamp': timestamp,
                            'path': update.get('path', ''),
                            'value': update.get('val')
                        }
                        
                        if not path.startswith('cli:'):
                            if timestamp:
                                update_entry['timestamp'] = convert_epoch_to_aedt(timestamp)
                            
                            val = update.get('val')
                            path_str = str(update.get('path', '')).lower()
                            if any(term in path_str for term in ['uptime', 'up/down']):
                                update_entry['value'] = format_uptime(val)
                        
                        results.append(update_entry)
            
            return {'updates': results}
            
        except Exception as e:
            # Remove failed connection from pool
            connection_pool.remove_connection(target, username)
            raise
            
    except Exception as e:
        logger.error(f"Error in get_gnmi_data: {str(e)}")
        raise Exception(f"gNMI GET operation failed: {str(e)}")

async def apply_cli_commands(params: dict):
    """Apply CLI commands to multiple devices with improved error handling."""
    targets = params["target"] if isinstance(params["target"], list) else [params["target"]]
    commands = params["commands"]
    username = params["username"]
    password = params["password"]

    async def configure_single_device(target: str):
        logger.info(f"\n=== Applying CLI Commands to {target} ===")
        
        filtered_commands = []
        for cmd in commands:
            cmd = cmd.strip()
            if not cmd or cmd in ['configure terminal', 'end', 'exit']:
                continue
            filtered_commands.append(cmd)

        if not filtered_commands:
            return {
                "target": target,
                "status": "failure",
                "confirmation": "No valid configuration commands to apply",
                "results": []
            }

        logger.info("Applying configuration commands:")
        for cmd in filtered_commands:
            logger.info(f"- {cmd}")

        results = []
        retries = 3
        
        while retries > 0:
            try:
                config_block = '\n'.join(filtered_commands)
                client = connection_pool.get_connection(target, username, password)
                
                try:
                    # Use ASCII encoding for CLI commands with empty path
                    response = client.set(
                        encoding='ascii',
                        update=[
                            ('cli:', config_block, 'ascii')
                        ]
                    )
                    
                    if response:
                        logger.info(f"Configuration response received: {response}")
                        results.append({
                            "command": config_block,
                            "status": "success",
                            "response": "Configuration applied successfully"
                        })
                        break
                        
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Error applying configuration: {error_msg}")
                    connection_pool.remove_connection(target, username)
                    
                    if retries > 1:
                        logger.info(f"Retrying configuration... ({retries-1} attempts remaining)")
                        retries -= 1
                        await asyncio.sleep(2)  # Wait before retry
                    else:
                        results.append({
                            "command": config_block,
                            "status": "failed",
                            "response": error_msg
                        })
                        break

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Connection error: {error_msg}")
                if retries > 1:
                    logger.info(f"Retrying connection... ({retries-1} attempts remaining)")
                    retries -= 1
                    await asyncio.sleep(2)
                else:
                    return {
                        "target": target,
                        "status": "failure",
                        "confirmation": error_msg,
                        "results": results
                    }

        all_succeeded = all(r["status"] == "success" for r in results)
        return {
            "target": target,
            "status": "success" if all_succeeded else "failure",
            "confirmation": f"Configuration {'successful' if all_succeeded else 'failed'} on {target}",
            "results": results
        }

    # Execute configuration tasks concurrently with semaphore to limit concurrent connections
    sem = asyncio.Semaphore(5)  # Limit to 5 concurrent connections
    
    async def wrapped_configure(target):
        async with sem:
            return await configure_single_device(target)
    
    tasks = [wrapped_configure(target) for target in targets]
    device_results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Process results
    successful_results = []
    failed_results = []
    
    for result in device_results:
        if isinstance(result, Exception):
            failed_results.append({
                "target": "unknown",
                "status": "failure",
                "confirmation": str(result),
                "results": []
            })
        elif result["status"] == "success":
            successful_results.append(result)
        else:
            failed_results.append(result)
    
    if len(targets) == 1:
        return device_results[0] if not isinstance(device_results[0], Exception) else {
            "status": "failure",
            "confirmation": str(device_results[0]),
            "results": []
        }

    return {
        "status": "success" if not failed_results else "partial_success" if successful_results else "failure",
        "confirmation": (
            "All devices configured successfully" if not failed_results
            else f"Successfully configured {len(successful_results)} devices, {len(failed_results)} failed"
        ),
        "successful_devices": [r["target"] for r in successful_results],
        "failed_devices": [r["target"] for r in failed_results],
        "device_results": successful_results + failed_results
    }

def cleanup_connections():
    """Clean up all connections in the pool."""
    connection_pool.cleanup()
