#!/bin/sh
# openclaw_bootstrap.sh — called at container start to set up OpenClaw.
set -e

echo "🐝 Hive: bootstrapping OpenClaw for agent ${AGENT_NAME:-unknown}..."

# Pull the OpenClaw repo if not already present
if [ ! -d /app/openclaw ]; then
    git clone --depth 1 https://github.com/openclaw/openclaw.git /app/openclaw 2>/dev/null || \
        echo "⚠️  OpenClaw repo not available — running in stub mode."
fi

# Install OpenClaw dependencies if the repo was cloned
if [ -f /app/openclaw/requirements.txt ]; then
    pip install --no-cache-dir -r /app/openclaw/requirements.txt
fi

echo "✅ OpenClaw bootstrap complete."
