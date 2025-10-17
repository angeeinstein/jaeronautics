#!/bin/bash
# Uninstallation script for Jaeronautics Viewer App
# Run with: sudo ./uninstall.sh

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${RED}=== Jaeronautics Viewer Uninstallation ===${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Please run as root (use sudo)${NC}"
    exit 1
fi

# Stop and disable service
echo -e "${YELLOW}Stopping and disabling service...${NC}"
systemctl stop jaeronautics 2>/dev/null || true
systemctl disable jaeronautics 2>/dev/null || true

# Remove service file
echo -e "${YELLOW}Removing service file...${NC}"
rm -f /etc/systemd/system/jaeronautics.service

# Reload systemd
systemctl daemon-reload

# Remove logs (optional - comment out if you want to keep logs)
echo -e "${YELLOW}Removing logs...${NC}"
rm -rf /var/log/jaeronautics

# Remove user (optional - comment out if you want to keep the user)
echo -e "${YELLOW}Removing application user...${NC}"
userdel jaeronautics 2>/dev/null || true

echo -e "${GREEN}Uninstallation complete!${NC}"
echo ""
echo -e "${YELLOW}Note:${NC} Application files in $(pwd) were NOT removed."
echo "Remove them manually if needed: rm -rf $(pwd)"
