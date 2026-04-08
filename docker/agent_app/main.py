"""Simple agent application for marketplace containers."""
from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import List, Dict
import os

app = FastAPI(title="Marketplace Agent")

AGENT_ID = os.getenv("AGENT_ID", "unknown")
AGENT_NAME = os.getenv("AGENT_NAME", "Unknown Agent")
SKILLS = os.getenv("SKILLS", "").split(",") if os.getenv("SKILLS") else []


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
    # Placeholder - actual implementation would process tasks
    return {
        "status": "success",
        "agent_id": AGENT_ID,
        "result": "Task processed"
    }
