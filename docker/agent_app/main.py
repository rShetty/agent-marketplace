"""Simple agent application for marketplace containers."""
import asyncio
import os
from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import List, Dict
import httpx

app = FastAPI(title="Marketplace Agent")

AGENT_ID = os.getenv("AGENT_ID", "unknown")
AGENT_NAME = os.getenv("AGENT_NAME", "Unknown Agent")
SKILLS = os.getenv("SKILLS", "").split(",") if os.getenv("SKILLS") else []
HIVE_URL = os.getenv("HIVE_URL", "")
HIVE_API_KEY = os.getenv("HIVE_API_KEY", "")


class HealthResponse(BaseModel):
    status: str
    token: str
    agent_id: str
    skills: List[str]


@app.get("/health")
async def health_check(token: str):
    """Health check endpoint for marketplace verification."""
    return HealthResponse(
        status="healthy",
        token=token,
        agent_id=AGENT_ID,
        skills=SKILLS
    )


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "agent_id": AGENT_ID,
        "name": AGENT_NAME,
        "skills": SKILLS,
        "status": "running"
    }


@app.get("/skills")
async def list_skills():
    """List available skills."""
    return {"skills": SKILLS}


@app.post("/invoke")
async def invoke(request: Dict):
    """Invoke the agent with a task."""
    return {
        "status": "success",
        "agent_id": AGENT_ID,
        "result": "Task processed"
    }


async def send_heartbeat():
    """Send heartbeat to Hive."""
    if not HIVE_URL or not HIVE_API_KEY:
        return
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{HIVE_URL}/api/agent/heartbeat",
                headers={"X-API-Key": HIVE_API_KEY},
                timeout=10.0
            )
            if response.status_code == 200:
                print(f"Heartbeat sent to Hive: {AGENT_NAME}")
            else:
                print(f"Heartbeat failed: {response.status_code}")
    except Exception as e:
        print(f"Heartbeat error: {e}")


@app.on_event("startup")
async def startup_event():
    """Start heartbeat loop."""
    if HIVE_URL and HIVE_API_KEY:
        asyncio.create_task(heartbeat_loop())


async def heartbeat_loop():
    """Send heartbeat every 60 seconds."""
    while True:
        await send_heartbeat()
        await asyncio.sleep(60)
