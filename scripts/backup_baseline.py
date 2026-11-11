#!/usr/bin/env python
"""Create baseline backup of Jalora data."""
import shutil
import tarfile
from datetime import datetime
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
BACKUP_DIR = PROJECT_ROOT / "backups"

# Create backup directory
BACKUP_DIR.mkdir(exist_ok=True)

# Timestamp for backup name
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_name = f"jalora_backup_{timestamp}"

# Files to exclude from backup
EXCLUDE_ITEMS = {
    ".git", "__pycache__", ".pytest_cache", ".ruff_cache", 
    "node_modules", "*.pyc", "*.pyo", "*.log", ".env"
}

# Files to include in backup
INCLUDE_ITEMS = {
    "backend/app", "backend/requirements.txt", "backend/README.md",
    "backend/.env.example", "backend/Dockerfile", "backend/docker-compose.yml",
    "frontend", "README.md", "scripts", "reports", "plans",
    "data/golden_problems.json", "data/periodic_table.json", 
    "data/physics_constants.json"
}

def create_backup():
    """Create a tar.gz backup of the project."""
    backup_file = BACKUP_DIR / f"{backup_name}.tar.gz"
    
    with tarfile.open(backup_file, "w:gz") as tar:
        for item in INCLUDE_ITEMS:
            item_path = PROJECT_ROOT / item
            if item_path.exists():
                tar.add(item_path, arcname=item)
    
    print(f"Backup created: {backup_file}")
    
    # Also create a database backup separately
    db_src = DATA_DIR / "jalora.db"
    if db_src.exists():
        db_backup = BACKUP_DIR / f"jalora_db_{timestamp}.db"
        shutil.copy2(db_src, db_backup)
        print(f"Database backup: {db_backup}")
    
    return backup_file

if __name__ == "__main__":
    create_backup()