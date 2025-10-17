#!/bin/bash
# Update script for Jaeronautics Viewer App
# Run with: sudo ./update.sh

set -e

# Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== Jaeronautics Viewer Update ===${NC}"

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo -e "${YELLOW}Stopping service...${NC}"
systemctl stop jaeronautics

echo -e "${YELLOW}Updating dependencies...${NC}"
source "$SCRIPT_DIR/venv/bin/activate"
pip install --upgrade pip
pip install -r "$SCRIPT_DIR/requirements.txt"
deactivate

echo -e "${YELLOW}Setting permissions...${NC}"
chown -R jaeronautics:jaeronautics "$SCRIPT_DIR"
chmod 600 "$SCRIPT_DIR/.env" 2>/dev/null || true

echo -e "${YELLOW}Reloading systemd configuration...${NC}"
systemctl daemon-reload

echo -e "${YELLOW}Starting service...${NC}"
systemctl start jaeronautics

echo -e "${GREEN}Update complete!${NC}"
echo ""
echo "Check status with: sudo systemctl status jaeronautics"
