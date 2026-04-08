"""Docker container management for agents."""
import os
import uuid
import docker
from docker.errors import NotFound, APIError
from typing import Optional

# Docker client
docker_client = docker.from_env()

# Configuration
NETWORK_NAME = "agent-marketplace"
BASE_PORT = 10000
MAX_AGENTS = 100
AGENT_IMAGE = os.getenv("AGENT_IMAGE", "hermes-agent:latest")


def get_available_port() -> int:
    """Get next available port for agent container."""
    # Simple implementation - in production, track used ports
    import random
    return random.randint(BASE_PORT, BASE_PORT + MAX_AGENTS)


def ensure_network():
    """Ensure the agent marketplace network exists."""
    try:
        docker_client.networks.get(NETWORK_NAME)
    except NotFound:
        docker_client.networks.create(NETWORK_NAME, driver="bridge")


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
    ensure_network()
    
    port = get_available_port()
    container_name = f"agent-{agent_id[:8]}"
    
    # Prepare environment variables
    environment = {
        "AGENT_ID": agent_id,
        "AGENT_NAME": agent_name,
        "AGENT_API_KEY": api_key,
        "MARKETPLACE_URL": os.getenv("MARKETPLACE_URL", "http://host.docker.internal:8000"),
        "SKILLS": ",".join([s["name"] for s in skills]),
    }
    
    # Add user's model API keys
    for key, value in env_vars.items():
        environment[key.upper() + "_API_KEY"] = value
    
    try:
        container = docker_client.containers.run(
            image=AGENT_IMAGE,
            name=container_name,
            environment=environment,
            network=NETWORK_NAME,
            ports={"8000/tcp": ("127.0.0.1", port)},
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            labels={
                "agent.marketplace/id": agent_id,
                "agent.marketplace/name": agent_name,
                "agent.marketplace/managed": "true"
            }
        )
        
        return container.id, port
    
    except APIError as e:
        raise Exception(f"Failed to create container: {e}")


def start_container(container_id: str):
    """Start a stopped container."""
    try:
        container = docker_client.containers.get(container_id)
        container.start()
        return True
    except NotFound:
        return False
    except APIError:
        return False


def stop_container(container_id: str):
    """Stop a running container."""
    try:
        container = docker_client.containers.get(container_id)
        container.stop(timeout=10)
        return True
    except NotFound:
        return False
    except APIError:
        return False


def delete_container(container_id: str):
    """Delete a container."""
    try:
        container = docker_client.containers.get(container_id)
        container.stop(timeout=10)
        container.remove(force=True)
        return True
    except NotFound:
        return False
    except APIError:
        return False


def get_container_logs(container_id: str, tail: int = 100) -> str:
    """Get container logs."""
    try:
        container = docker_client.containers.get(container_id)
        logs = container.logs(tail=tail, timestamps=True)
        return logs.decode("utf-8")
    except NotFound:
        return "Container not found"
    except APIError as e:
        return f"Error getting logs: {e}"


def get_container_status(container_id: str) -> str:
    """Get container status."""
    try:
        container = docker_client.containers.get(container_id)
        return container.status
    except NotFound:
        return "not_found"
    except APIError:
        return "error"
