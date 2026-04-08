# Deploy to hive.rajeev.me

## Prerequisites
- Docker & Docker Compose installed
- Port 80 and 443 open
- DNS A record pointing to your VPS IP

## Deployment Steps

### 1. On Your VPS, clone the repo
```bash
git clone https://github.com/rShetty/agent-marketplace.git
cd agent-marketplace
```

### 2. Create environment file
```bash
# Generate secure keys
ENCRYPTION_KEY=$(openssl rand -base64 32)
SECRET_KEY=$(openssl rand -hex 32)

cat > .env << EOF
ENCRYPTION_KEY=$ENCRYPTION_KEY
SECRET_KEY=$SECRET_KEY
EOF
```

### 3. Build the agent image
```bash
docker build -t agent-marketplace-agent:latest -f docker/Dockerfile.agent docker/
```

### 4. Start the services
```bash
docker-compose -f docker-compose.prod.yml up -d
```

### 5. Verify it's running
```bash
# Check logs
docker-compose -f docker-compose.prod.yml logs -f marketplace

# Test health endpoint
curl https://hive.rajeev.me/api/health
```

### 6. DNS Setup
Make sure you have an A record:
- Name: `hive`
- Type: `A`
- Value: `YOUR_VPS_IP`

## Management

### View logs
```bash
docker-compose -f docker-compose.prod.yml logs -f
```

### Restart
```bash
docker-compose -f docker-compose.prod.yml restart
```

### Update
```bash
git pull
docker-compose -f docker-compose.prod.yml down
docker-compose -f docker-compose.prod.yml up -d --build
```

## SSL Certificate
Traefik automatically handles SSL via Let's Encrypt. The first request may take a few seconds as the certificate is issued.

## Access Traefik Dashboard
Visit: https://traefik.hive.rajeev.me
- Username: admin
- Password: admin (change in docker-compose.prod.yml)
