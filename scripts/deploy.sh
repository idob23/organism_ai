#!/bin/bash
set -e

echo "=== Organism AI Deploy ==="

# 1. Validate .env
if [ ! -f .env ]; then
    echo "ERROR: .env file not found. Copy from .env.production.example and fill values."
    exit 1
fi

if grep -q "organism_secret" .env 2>/dev/null; then
    echo "ERROR: Default POSTGRES_PASSWORD detected. Change it in .env"
    exit 1
fi

if grep -q "your-.*-here" .env 2>/dev/null; then
    echo "ERROR: Placeholder values found in .env. Fill all required fields."
    exit 1
fi

# 2. Pre-deploy backup
if docker ps | grep -q organism_postgres; then
    echo "Backing up database..."
    ./scripts/backup.sh ./backups/pre-deploy
fi

# 3. Pull + Build
echo "Pulling latest code..."
git pull origin master

echo "Building images..."
docker-compose build

# 4. Restart
echo "Restarting services..."
docker-compose up -d

# 5. Health check
echo "Waiting for health check (90s start period)..."
sleep 15

for i in {1..6}; do
    if docker inspect --format='{{.State.Health.Status}}' organism_bot 2>/dev/null | grep -q "healthy"; then
        echo "Deploy successful! Bot is healthy."
        exit 0
    fi
    echo "  Waiting... ($((i*15))s)"
    sleep 15
done

echo "WARNING: Bot health check not yet passing. Check logs:"
echo "  docker-compose logs --tail=50 bot"
