#!/bin/bash
# Organism AI — PostgreSQL restore
# Usage: ./scripts/restore.sh backup_file.sql.gz

if [ -z "$1" ]; then
    echo "Usage: ./scripts/restore.sh <backup_file.sql.gz>"
    exit 1
fi

echo "Restoring from $1..."
gunzip -c "$1" | docker exec -i organism_postgres psql -U organism organism_ai
echo "Restore complete"
