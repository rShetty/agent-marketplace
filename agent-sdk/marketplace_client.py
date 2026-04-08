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
        skills: List[Dict[str, str]],
        endpoint_url: Optional[str] = None,
        version: str = "1.0.0"
    ) -> Dict:
        """
        Register this agent with the marketplace.
        
        Args:
            name: Display name for the agent
            description: What the agent does
            skills: List of skill dicts with 'name' and 'description'
            endpoint_url: URL where agent can be reached (optional)
            version: Agent software version
            
        Returns:
            Registration response with agent_id and api_key
        """
        response = requests.post(
            f"{self.marketplace_url}/api/agent/register",
            json={
                "name": name,
                "description": description,
                "skill_ids": [],  # Skills are passed differently in this POC
                "skills": skills,
                "version": version
            }
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
    # Example: Register a new agent
    client = MarketplaceClient("http://localhost:8000")
    
    result = client.register(
        name="My Test Agent",
        description="A simple test agent",
        skills=[
            {"name": "terminal", "description": "Run shell commands"},
            {"name": "web_extract", "description": "Fetch web content"}
        ]
    )
    
    print(f"Registered with ID: {result['agent_id']}")
    print(f"API Key: {result['api_key']}")
    
    # Start heartbeat
    client.start_heartbeat_loop(interval=60)
    
    # Keep running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        client.stop_heartbeat_loop()
        print("Shutting down...")
