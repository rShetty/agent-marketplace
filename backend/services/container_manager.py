"""Docker container management for agents."""
import os
import uuid
from typing import Optional

# Docker client - lazy initialization
docker_client = None

def get_docker_client():
    """Get or create Docker client."""
    global docker_client
    if docker_client is None:
        try:
            import docker
            docker_client = docker.from_env()
        except Exception as e:
            print(f"Warning: Docker not available: {e}")
            return None
    return docker_client

# Configuration
NETWORK_NAME = "agent-marketplace"
BASE_PORT = 10000
MAX_AGENTS = 100
AGENT_IMAGE = os.getenv("AGENT_IMAGE", "hive-agent:latest")


def get_available_port() -> int:
    """Get next available port for agent container."""
    import random
    return random.randint(BASE_PORT, BASE_PORT + MAX_AGENTS)


def ensure_network():
    """Ensure the agent marketplace network exists."""
    client = get_docker_client()
    if not client:
        return
    try:
        from docker.errors import NotFound
        client.networks.get(NETWORK_NAME)
    except NotFound:
        client.networks.create(NETWORK_NAME, driver="bridge")


def create_container(
    agent_id: str,
    agent_name: str,
    skills: list,
    env_vars: dict,
    api_key: str
) -> tuple[str, int]:
    """
    Create a Docker container for an agent.
    
    Returns:
        tuple: (container_id, internal_port)
    """
    client = get_docker_client()
    if not client:
        # Mock container for testing without Docker
        print(f"Mock: Creating container for agent {agent_id}")
        return f"mock-container-{agent_id[:8]}", get_available_port()
    
    ensure_network()
    
    port = get_available_port()
    container_name = f"agent-{agent_id[:8]}"
    
    environment = {
        "AGENT_ID": agent_id,
        "AGENT_NAME": agent_name,
        "AGENT_API_KEY": api_key,
        "MARKETPLACE_URL": os.getenv("MARKETPLACE_URL", "http://host.docker.internal:8000"),
        "SKILLS": ",".join([s.get("name", "") for s in skills]),
    }
    
    for key, value in env_vars.items():
        environment[key.upper() + "_API_KEY"] = value
    
    try:
        from docker.errors import APIError
        container = client.containers.run(
            image=AGENT_IMAGE,
            name=container_name,
            environment=environment,
            network=NETWORK_NAME,
            ports={"8000/tcp": ("127.0.0.1", port)},
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            labels={
                "hive/agent-id": agent_id,
                "hive/agent-name": agent_name,
                "hive/managed": "true"
            }
        )
        return container.id, port
    except Exception as e:
        raise Exception(f"Failed to create container: {e}")


def start_container(container_id: str):
    """Start a stopped container."""
    client = get_docker_client()
    if not client:
        return True  # Mock success
    try:
        container = client.containers.get(container_id)
        container.start()
        return True
    except Exception:
        return False


def stop_container(container_id: str):
    """Stop a running container."""
    client = get_docker_client()
    if not client:
        return True  # Mock success
    try:
        container = client.containers.get(container_id)
        container.stop(timeout=10)
        return True
    except Exception:
        return False


def delete_container(container_id: str):
    """Delete a container."""
    client = get_docker_client()
    if not client:
        return True  # Mock success
    try:
        container = client.containers.get(container_id)
        container.stop(timeout=10)
        container.remove(force=True)
        return True
    except Exception:
        return False


def get_container_logs(container_id: str, tail: int = 100) -> str:
    """Get container logs."""
    client = get_docker_client()
    if not client:
        return "Docker not available - mock logs"
    try:
        container = client.containers.get(container_id)
        logs = container.logs(tail=tail, timestamps=True)
        return logs.decode("utf-8")
    except Exception as e:
        return f"Error getting logs: {e}"


def get_container_status(container_id: str) -> str:
    """Get container status."""
    client = get_docker_client()
    if not client:
        return "running"  # Mock status
    try:
        container = client.containers.get(container_id)
        return container.status
    except Exception:
        return "not_found"
