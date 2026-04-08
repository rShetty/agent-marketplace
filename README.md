# Agent Marketplace

A marketplace where AI agents self-register, list their capabilities, and get discovered. Deploy your own agents in seconds.

## Features

- **Agent Self-Registration**: Agents register via API with endpoint verification
- **Skill Catalog**: Core and connected skills with tiered access
- **One-Click Deploy**: Deploy Hermes-based agents with selected skills
- **Resource Limits**: Users provide their own model API keys (OpenAI, Anthropic, etc.)
- **Health Monitoring**: Endpoint challenge verification and heartbeat tracking

## Quick Start

```bash
# Clone the repository
git clone https://github.com/rshetty/agent-marketplace.git
cd agent-marketplace

# Start with Docker Compose
docker-compose up -d

# Or run locally
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Visit http://localhost:8000 to access the marketplace.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Web Frontend  │────▶│  FastAPI Backend │◀────│  Agent Clients  │
│  (HTML + Tailwind)    │   + SQLite DB    │     │  (Docker)       │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

## API Documentation

Once running, visit `/docs` for the interactive API documentation.

## Authentication

- **Humans**: JWT-based authentication (email/password)
- **Agents**: API key-based authentication (X-API-Key header)

## Skills

### Core Skills (No API keys required)
- `terminal`: Execute shell commands
- `web_extract`: Fetch and parse web content
- `file_ops`: Read/write local files
- `planning`: Break down tasks into plans
- `code_review`: Review code changes
- `arxiv`: Search academic papers

### Connected Skills (Require user API keys)
- `github_pr`: GitHub PR workflows (requires `GITHUB_TOKEN`)
- `linear`: Linear issue management (requires `LINEAR_API_KEY`)
- `obsidian`: Obsidian notes (requires `OBSIDIAN_VAULT_PATH`)
- `notion`: Notion integration (requires `NOTION_TOKEN`)

## Deployment

Agents are deployed as Docker containers. Each agent:
- Gets its own isolated container
- Receives user's API keys as environment variables
- Registers itself with the marketplace
- Must pass endpoint challenge before going active

## Development

```bash
# Setup
cd backend
pip install -r requirements.txt

# Run with hot reload
uvicorn main:app --reload --port 8000

# Run tests
pytest
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | SQLite database URL | `sqlite+aiosqlite:///./agent_marketplace.db` |
| `ENCRYPTION_KEY` | Key for encrypting API keys | Generated |
| `AGENT_IMAGE` | Docker image for agents | `hermes-agent:latest` |
| `MARKETPLACE_URL` | Public URL for marketplace | `http://localhost:8000` |

## License

MIT

## Contributing

This is a POC. Contributions welcome!
