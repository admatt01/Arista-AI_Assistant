"""Module for gNMI schema discovery using pygnmi."""

from pygnmi.client import gNMIclient

def discover_schema(target: str, username: str = None, password: str = None):
    """Discover supported YANG models using gNMI capabilities.
    
    Args:
        target: Target device address including port (e.g. "switch:6030")
        username: Optional username for authentication
        password: Optional password for authentication
        
    Returns:
        List of supported models with name, organization, and version
    """
    try:
        host, port = target.split(':')
        with gNMIclient(
            target=(host, int(port)),
            username=username,
            password=password,
            insecure=True
        ) as gc:
            capabilities = gc.capabilities()
            
            if 'supported_models' in capabilities:
                return [
                    {
                        "name": model.get('name'),
                        "organization": model.get('organization'),
                        "version": model.get('version')
                    }
                    for model in capabilities['supported_models']
                ]
            return []
            
    except Exception as e:
        print(f"Error discovering schema: {str(e)}")
        raise
