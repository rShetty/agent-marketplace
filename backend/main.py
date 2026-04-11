"""Main FastAPI application."""
import os
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from database import init_db
from routers import auth, agents, agent_api, skills, deploy, marketplace, invites, wallet, delegation, reviews
from services.skill_catalog import seed_skills


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    await init_db()
    
    # Seed skills (need a session)
    from database import async_session_maker
    async with async_session_maker() as session:
        await seed_skills(session)
    
    print("🚀 Agent Marketplace started!")
    yield
    # Shutdown
    print("👋 Agent Marketplace shutting down...")


app = FastAPI(
    title="Hive 🐝",
    description="A swarm of AI agents with self-registration and skill discovery",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
_allowed_origins = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router)
app.include_router(agents.router)
app.include_router(agent_api.router)
app.include_router(skills.router)
app.include_router(deploy.router)
app.include_router(marketplace.router)
app.include_router(invites.router)
app.include_router(wallet.router)
app.include_router(delegation.router)
app.include_router(reviews.router)


@app.get("/api/health")
async def health_check():
    """Service health check."""
    return {"status": "healthy", "service": "agent-marketplace"}


# Static files for frontend (in production, serve built files)
# For now, we'll serve from a static directory
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
# In Docker, frontend is at /app/frontend (backend is at /app/backend)
if os.path.exists("/app/frontend"):
    frontend_path = "/app/frontend"
elif os.path.exists(frontend_path):
    pass  # Use relative path for local dev
else:
    frontend_path = None

if frontend_path and os.path.exists(frontend_path):
    app.mount("/static", StaticFiles(directory=frontend_path), name="static")


@app.get("/")
async def root():
    """Serve the main frontend page."""
    frontend_file = os.path.join(frontend_path, "index.html")
    if os.path.exists(frontend_file):
        return FileResponse(frontend_file)
    return {"message": "Agent Marketplace API", "docs": "/docs"}


@app.get("/agents")
async def agents_page():
    """Serve the agents listing page."""
    frontend_file = os.path.join(frontend_path, "agents.html")
    if os.path.exists(frontend_file):
        return FileResponse(frontend_file)
    raise HTTPException(status_code=404, detail="Page not found")


@app.get("/agents/{agent_id}")
async def agent_detail_page_by_id(agent_id: str):
    """Serve the agent detail page."""
    frontend_file = os.path.join(frontend_path, "agent-detail.html")
    if os.path.exists(frontend_file):
        return FileResponse(frontend_file)
    raise HTTPException(status_code=404, detail="Page not found")


@app.get("/agent-detail")
async def agent_detail_page():
    """Serve the agent detail page (query param version)."""
    frontend_file = os.path.join(frontend_path, "agent-detail.html")
    if os.path.exists(frontend_file):
        return FileResponse(frontend_file)
    raise HTTPException(status_code=404, detail="Page not found")


@app.get("/login")
async def login_page():
    """Serve the login page."""
    frontend_file = os.path.join(frontend_path, "login.html")
    if os.path.exists(frontend_file):
        return FileResponse(frontend_file)
    raise HTTPException(status_code=404, detail="Page not found")


@app.get("/signup")
async def signup_page():
    """Serve the signup page."""
    frontend_file = os.path.join(frontend_path, "signup.html")
    if os.path.exists(frontend_file):
        return FileResponse(frontend_file)
    raise HTTPException(status_code=404, detail="Page not found")


@app.get("/deploy")
async def deploy_page():
    """Serve the deploy page."""
    frontend_file = os.path.join(frontend_path, "deploy.html")
    if os.path.exists(frontend_file):
        return FileResponse(frontend_file)
    raise HTTPException(status_code=404, detail="Page not found")


@app.get("/settings")
async def settings_page():
    """Serve the settings page."""
    frontend_file = os.path.join(frontend_path, "settings.html")
    if os.path.exists(frontend_file):
        return FileResponse(frontend_file)
    raise HTTPException(status_code=404, detail="Page not found")


@app.get("/agents/{agent_id}/health")
async def agent_health_check(agent_id: str, token: str, request: Request):
    """
    Health check endpoint for agents.
    This proxies to the agent container or handles directly.
    """
    from sqlalchemy import select
    from database import async_session_maker
    from models.agent import Agent
    
    async with async_session_maker() as session:
        result = await session.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        import hmac as _hmac
        if not _hmac.compare_digest(agent.health_check_token or "", token):
            raise HTTPException(status_code=401, detail="Invalid token")
        
        # Get skills
        from models.agent_skill import AgentSkill
        from models.skill import Skill
        result = await session.execute(
            select(Skill.name)
            .join(AgentSkill)
            .where(AgentSkill.agent_id == agent_id)
        )
        skills = [row[0] for row in result.all()]
        
        return {
            "status": "healthy",
            "token": token,
            "agent_id": agent_id,
            "skills": skills
        }


# Container proxy middleware
@app.api_route("/agents/{agent_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_to_agent(agent_id: str, path: str, request: Request):
    """
    Proxy requests to agent containers.
    Routes /agents/{id}/invoke to the agent's container.
    """
    from sqlalchemy import select
    from database import async_session_maker
    from models.agent import Agent
    import aiohttp
    
    # Don't proxy health checks (handled above)
    if path == "health":
        return await agent_health_check(agent_id, request.query_params.get("token", ""), request)
    
    async with async_session_maker() as session:
        result = await session.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        if agent.status not in ["active", "idle"]:
            raise HTTPException(status_code=503, detail="Agent not available")
        
        # Build target URL
        internal_port = agent.internal_port
        if not internal_port:
            raise HTTPException(status_code=503, detail="Agent not properly configured")
        
        target_url = f"http://localhost:{internal_port}/{path}"
        
        # Forward the request
        method = request.method
        headers = dict(request.headers)
        headers.pop("host", None)
        
        try:
            async with aiohttp.ClientSession() as client_session:
                body = await request.body() if method in ["POST", "PUT", "PATCH"] else None
                
                async with client_session.request(
                    method=method,
                    url=target_url,
                    headers=headers,
                    data=body,
                    params=request.query_params,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    content = await response.read()
                    try:
                        body = json.loads(content) if content else {}
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        body = {"raw": content.decode(errors="replace") if content else ""}
                    return JSONResponse(
                        content=body,
                        status_code=response.status,
                    )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Agent proxy error for %s: %s", agent_id, e)
            raise HTTPException(status_code=502, detail="Agent unreachable")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
