"""HTTP client for making requests to external agents."""
import os
import asyncio
from typing import Dict, Any, Optional
import aiohttp
from datetime import datetime


class AgentClientError(Exception):
    """Base exception for agent client errors."""
    pass


class AgentTimeoutError(AgentClientError):
    """Agent request timed out."""
    pass


class AgentConnectionError(AgentClientError):
    """Failed to connect to agent."""
    pass


class AgentClient:
    """Client for making HTTP requests to agents."""
    
    def __init__(self, timeout: int = 300):
        """
        Initialize agent client.
        
        Args:
            timeout: Request timeout in seconds (default 5 minutes)
        """
        self.timeout = timeout
        self.marketplace_url = os.getenv("MARKETPLACE_URL", "http://localhost:8000")
    
    async def send_delegation_task(
        self,
        target_endpoint: str,
        delegation_id: str,
        task_description: str,
        max_tokens: float,
        callback_url: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Send a delegation task to a target agent.
        
        Args:
            target_endpoint: Agent's endpoint URL
            delegation_id: Unique delegation ID
            task_description: Task description
            max_tokens: Maximum tokens allocated
            callback_url: Optional callback URL for results
            context: Optional task context
            timeout: Optional custom timeout
            
        Returns:
            Dict with agent's response
            
        Raises:
            AgentTimeoutError: If request times out
            AgentConnectionError: If connection fails
            AgentClientError: For other errors
        """
        request_timeout = timeout or self.timeout
        
        # Build the delegation payload
        payload = {
            "delegation_id": delegation_id,
            "task": task_description,
            "max_tokens": max_tokens,
            "context": context or {},
            "callback_url": callback_url or f"{self.marketplace_url}/api/delegate/{delegation_id}/callback",
            "requested_at": datetime.utcnow().isoformat()
        }
        
        # Ensure endpoint has /delegate path if not already specified
        if not target_endpoint.endswith("/delegate") and "/delegate" not in target_endpoint:
            if target_endpoint.endswith("/"):
                target_endpoint = target_endpoint + "delegate"
            else:
                target_endpoint = target_endpoint + "/delegate"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    target_endpoint,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=request_timeout),
                    headers={
                        "Content-Type": "application/json",
                        "X-Hive-Delegation-ID": delegation_id,
                        "User-Agent": "Hive-Marketplace/1.0"
                    }
                ) as response:
                    response.raise_for_status()
                    result = await response.json()
                    
                    print(f"✅ Agent responded: {target_endpoint} - Status: {result.get('status', 'unknown')}")
                    
                    return result
                    
        except asyncio.TimeoutError as e:
            raise AgentTimeoutError(f"Agent did not respond within {request_timeout}s") from e
        
        except aiohttp.ClientError as e:
            raise AgentConnectionError(f"Failed to connect to agent: {str(e)}") from e
        
        except Exception as e:
            raise AgentClientError(f"Unexpected error calling agent: {str(e)}") from e
    
    async def send_callback(
        self,
        callback_url: str,
        delegation_id: str,
        status: str,
        result: Dict[str, Any],
        tokens_used: float
    ) -> bool:
        """
        Send completion callback to delegating agent.
        
        Args:
            callback_url: Callback URL
            delegation_id: Delegation ID
            status: Completion status (completed/failed)
            result: Task result
            tokens_used: Tokens consumed
            
        Returns:
            True if callback succeeded, False otherwise
        """
        payload = {
            "delegation_id": delegation_id,
            "status": status,
            "result": result,
            "tokens_used": tokens_used,
            "completed_at": datetime.utcnow().isoformat()
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    callback_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={
                        "Content-Type": "application/json",
                        "X-Hive-Delegation-ID": delegation_id
                    }
                ) as response:
                    response.raise_for_status()
                    print(f"📞 Callback sent: {callback_url} - Delegation: {delegation_id}")
                    return True
                    
        except Exception as e:
            print(f"⚠️  Callback failed: {callback_url} - Error: {str(e)}")
            return False


# Global client instance
_agent_client: Optional[AgentClient] = None


def get_agent_client(timeout: int = 300) -> AgentClient:
    """Get or create global agent client instance."""
    global _agent_client
    if _agent_client is None:
        _agent_client = AgentClient(timeout=timeout)
    return _agent_client
