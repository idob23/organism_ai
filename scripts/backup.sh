#!/bin/bash
# Organism AI — PostgreSQL backup
# Usage: ./scripts/backup.sh [backup_dir]
# Default: ./backups/

BACKUP_DIR="${1:-./backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="organism_backup_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "Backing up PostgreSQL..."
docker exec organism_postgres pg_dump -U organism organism_ai | gzip > "${BACKUP_DIR}/${FILENAME}"

if [ $? -eq 0 ]; then
    echo "Backup saved: ${BACKUP_DIR}/${FILENAME}"
    # Remove backups older than 30 days
    find "$BACKUP_DIR" -name "organism_backup_*.sql.gz" -mtime +30 -delete
    echo "Old backups cleaned up"
else
    echo "ERROR: Backup failed!"
    exit 1
fi
