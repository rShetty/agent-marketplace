"""Main FastAPI application."""
import os
import json
from contextlib import asynccontextmanager

# Load .env from project root (two levels up from this file) if present.
# This is a no-op when the variables are already set in the environment,
# so production deployments can use real env vars without a .env file.
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    load_dotenv(_env_path, override=False)
except ImportError:
    pass

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from database import init_db
from routers import auth, agents, agent_api, skills, deploy, marketplace, invites, wallet, delegation, reviews
from services.skill_catalog import seed_skills
from middleware.rate_limit import limiter, rate_limit_exceeded_handler
from middleware.monitoring import MonitoringMiddleware, metrics


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

# Add rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Add monitoring middleware
app.add_middleware(MonitoringMiddleware)

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


@app.get("/.well-known/agent.json")
async def well_known_agent_card():
    """
    Platform-level A2A AgentCard for the Hive marketplace itself.

    External orchestrators can discover the marketplace via this well-known URL
    and learn how to register agents or delegate tasks.
    """
    marketplace_url = os.getenv("MARKETPLACE_URL", "http://localhost:8000")
    return {
        "name": "Hive Marketplace",
        "description": (
            "An AI agent marketplace where agents self-register, discover each other, "
            "and delegate work using a token economy."
        ),
        "url": marketplace_url,
        "version": "1.0.0",
        "authentication": {"schemes": ["Bearer"]},
        "capabilities": {
            "streaming": True,
            "pushNotifications": True,
            "stateTransitionHistory": False,
        },
        "skills": [
            {
                "id": "agent-registration",
                "name": "Agent Registration",
                "description": "Register a new agent (BYOA or managed) in the marketplace.",
                "tags": ["registration", "onboarding"],
            },
            {
                "id": "agent-delegation",
                "name": "Task Delegation",
                "description": "Delegate a task to a marketplace agent with token payment.",
                "tags": ["delegation", "task"],
            },
            {
                "id": "agent-discovery",
                "name": "Agent Discovery",
                "description": "Browse and search public agents by skill, tag, or rating.",
                "tags": ["discovery", "marketplace"],
            },
        ],
        "x-hive": {
            "docs": f"{marketplace_url}/docs",
            "registration_endpoint": f"{marketplace_url}/api/agent/register",
            "invite_endpoint": f"{marketplace_url}/api/agent/invite",
            "marketplace_endpoint": f"{marketplace_url}/api/marketplace/agents",
            "delegation_endpoint": f"{marketplace_url}/api/delegate",
        },
    }


@app.get("/.well-known/jwks.json")
async def well_known_jwks():
    """
    JWKS endpoint — advertises the signing algorithm used by Hive JWTs.

    Hive uses HS256 (symmetric HMAC-SHA256) for access tokens.  HS256 keys are
    shared secrets and cannot be published; this endpoint therefore returns an
    empty key set and documents the algorithm for A2A-aware clients.

    Agents that need to verify Hive tokens should use the shared
    HIVE_SIGNING_SECRET environment variable out-of-band.
    """
    return {
        "keys": [],
        "_comment": (
            "Hive uses HS256 (symmetric). Public keys are not applicable. "
            "Token verification requires the shared HIVE_SIGNING_SECRET."
        ),
    }


@app.get("/api/metrics")
async def get_metrics():
    """Get service metrics (for monitoring/admin)."""
    return metrics.get_summary()


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
    # Also mount specific paths for frontend assets
    if os.path.exists(os.path.join(frontend_path, "js")):
        app.mount("/js", StaticFiles(directory=os.path.join(frontend_path, "js")), name="js")


def _serve_frontend(filename: str):
    """Return the given frontend HTML file, or raise 404."""
    if not frontend_path:
        raise HTTPException(status_code=404, detail="Frontend not available")
    path = os.path.join(frontend_path, filename)
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(status_code=404, detail="Page not found")


@app.get("/")
async def root():
    """Serve the main frontend page."""
    if not frontend_path:
        return {"message": "Hive Agent Marketplace API", "docs": "/docs"}
    path = os.path.join(frontend_path, "index.html")
    if os.path.exists(path):
        return FileResponse(path)
    return {"message": "Hive Agent Marketplace API", "docs": "/docs"}


@app.get("/agents")
async def agents_page():
    return _serve_frontend("agents.html")


@app.get("/agents/{agent_id}")
async def agent_detail_page_by_id(agent_id: str):
    return _serve_frontend("agent-detail.html")


@app.get("/agent-detail")
async def agent_detail_page():
    return _serve_frontend("agent-detail.html")


@app.get("/login")
async def login_page():
    return _serve_frontend("login.html")


@app.get("/signup")
async def signup_page():
    return _serve_frontend("signup.html")


@app.get("/deploy")
async def deploy_page():
    return _serve_frontend("deploy.html")


@app.get("/settings")
async def settings_page():
    return _serve_frontend("settings.html")


@app.get("/delegate")
async def delegate_page():
    return _serve_frontend("delegate.html")


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
    import os
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
