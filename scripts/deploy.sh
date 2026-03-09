#!/bin/bash
# Organism AI — production update script
# Usage: ./scripts/deploy.sh

set -e

echo "=== Organism AI Deploy ==="
echo "Pulling latest code..."
git pull origin master

echo "Building new image..."
docker-compose build bot

echo "Restarting bot (zero-downtime for stateless service)..."
docker-compose up -d --no-deps bot

echo "Waiting for health check..."
sleep 10
if docker-compose ps bot | grep -q "healthy\|Up"; then
    echo "Deploy successful!"
else
    echo "WARNING: Bot may not be healthy yet. Check: docker-compose logs bot"
fi
