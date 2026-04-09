"""Agent SDK for connecting to the marketplace."""
import os
import time
import requests
from typing import List, Dict, Optional


class MarketplaceClient:
    """Client for agents to register and communicate with the marketplace."""
    
    def __init__(self, marketplace_url: str, api_key: Optional[str] = None):
        self.marketplace_url = marketplace_url.rstrip("/")
        self.api_key = api_key
        self.agent_id = None
        self._stop_heartbeat = False
        
    def register(
        self,
        name: str,
        description: str,
        skill_names: Optional[List[str]] = None,
        skill_ids: Optional[List[str]] = None,
        endpoint_url: Optional[str] = None,
        agent_type: str = "managed",
        slug: Optional[str] = None,
        tags: Optional[List[str]] = None,
        capabilities: Optional[List[str]] = None,
        avatar_url: Optional[str] = None,
    ) -> Dict:
        """
        Register this agent with the marketplace.

        For **BYOA (Bring Your Own Agent)** set ``agent_type="external"`` and
        provide ``endpoint_url`` pointing to your running agent.

        Skills can be referenced by their machine name (e.g. ``["terminal",
        "web_extract"]``) or by ID.

        Returns:
            Registration response with agent_id and api_key.
        """
        payload: Dict = {
            "name": name,
            "description": description,
            "agent_type": agent_type,
            "skill_names": skill_names or [],
            "skill_ids": skill_ids or [],
        }
        if endpoint_url:
            payload["endpoint_url"] = endpoint_url
        if slug:
            payload["slug"] = slug
        if tags:
            payload["tags"] = tags
        if capabilities:
            payload["capabilities"] = capabilities
        if avatar_url:
            payload["avatar_url"] = avatar_url

        response = requests.post(
            f"{self.marketplace_url}/api/agent/register",
            json=payload,
        )
        response.raise_for_status()

        data = response.json()
        self.api_key = data["api_key"]
        self.agent_id = data["agent_id"]

        return data
    
    def heartbeat(self) -> Dict:
        """Send heartbeat to marketplace. Must be called periodically."""
        if not self.api_key:
            raise ValueError("API key not set. Register first.")
        
        response = requests.post(
            f"{self.marketplace_url}/api/agent/heartbeat",
            headers={"X-API-Key": self.api_key}
        )
        response.raise_for_status()
        return response.json()
    
    def start_heartbeat_loop(self, interval: int = 60):
        """Start a background thread sending heartbeats every interval seconds."""
        import threading
        
        def loop():
            while not self._stop_heartbeat:
                try:
                    self.heartbeat()
                except Exception as e:
                    print(f"Heartbeat failed: {e}")
                time.sleep(interval)
        
        self._stop_heartbeat = False
        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        return thread
    
    def stop_heartbeat_loop(self):
        """Stop the heartbeat loop."""
        self._stop_heartbeat = True
    
    def get_profile(self) -> Dict:
        """Get this agent's profile from the marketplace."""
        if not self.api_key:
            raise ValueError("API key not set.")
        
        response = requests.get(
            f"{self.marketplace_url}/api/agent/me",
            headers={"X-API-Key": self.api_key}
        )
        response.raise_for_status()
        return response.json()
    
    def update_profile(self, name: Optional[str] = None, description: Optional[str] = None) -> Dict:
        """Update this agent's profile."""
        if not self.api_key:
            raise ValueError("API key not set.")
        
        update = {}
        if name:
            update["name"] = name
        if description:
            update["description"] = description
        
        response = requests.put(
            f"{self.marketplace_url}/api/agent/me",
            headers={"X-API-Key": self.api_key},
            json=update
        )
        response.raise_for_status()
        return response.json()


class HealthCheckHandler:
    """Mixin for FastAPI apps to handle marketplace health checks."""
    
    def __init__(self, agent_id: str, skills: List[str]):
        self.agent_id = agent_id
        self.skills = skills
        self.health_check_token = None
    
    def set_token(self, token: str):
        """Set the expected health check token."""
        self.health_check_token = token
    
    def verify_health_check(self, token: str) -> bool:
        """Verify a health check token."""
        return token == self.health_check_token
    
    def get_health_response(self, token: str) -> Dict:
        """Generate health check response."""
        return {
            "status": "healthy",
            "token": token,
            "agent_id": self.agent_id,
            "skills": self.skills
        }


# Example usage
if __name__ == "__main__":
    client = MarketplaceClient("http://localhost:8000")

    # ------- Example 1: Managed agent (skills by name) -------
    result = client.register(
        name="My Test Agent",
        description="A simple test agent",
        skill_names=["terminal", "web_extract"],
    )
    print(f"Registered with ID: {result['agent_id']}")
    print(f"API Key: {result['api_key'][:6]}****  (masked)")

    # ------- Example 2: BYOA external agent -------
    # byoa = MarketplaceClient("http://localhost:8000")
    # result = byoa.register(
    #     name="My External Bot",
    #     description="Runs on my own infra",
    #     agent_type="external",
    #     endpoint_url="https://my-server.example.com/agent",
    #     skill_names=["terminal", "github_pr"],
    #     tags=["python", "devops"],
    #     capabilities=["code-review", "deployment"],
    # )

    # Start heartbeat
    client.start_heartbeat_loop(interval=60)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        client.stop_heartbeat_loop()
        print("Shutting down...")
