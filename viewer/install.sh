#!/bin/bash
# Installation script for Jaeronautics Viewer App
# Run with: sudo ./install.sh

set -e  # Exit on error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Jaeronautics Viewer Installation ===${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Please run as root (use sudo)${NC}"
    exit 1
fi

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
APP_DIR="$SCRIPT_DIR"

echo -e "${YELLOW}Installing from: $APP_DIR${NC}"

# 1. Install system dependencies
echo -e "${GREEN}Step 1: Installing system dependencies...${NC}"
apt-get update
apt-get install -y python3 python3-pip python3-venv mysql-client

# 2. Create application user if doesn't exist
echo -e "${GREEN}Step 2: Setting up application user...${NC}"
if ! id "jaeronautics" &>/dev/null; then
    useradd -r -s /bin/bash -d /opt/jaeronautics jaeronautics
    echo "Created user: jaeronautics"
else
    echo "User jaeronautics already exists"
fi

# 3. Setup virtual environment
echo -e "${GREEN}Step 3: Setting up Python virtual environment...${NC}"
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
    echo "Virtual environment created"
fi

# Activate and install requirements
source "$APP_DIR/venv/bin/activate"
pip install --upgrade pip
pip install -r "$APP_DIR/requirements.txt"
deactivate

# 4. Create log directory
echo -e "${GREEN}Step 4: Creating log directory...${NC}"
mkdir -p /var/log/jaeronautics
chown jaeronautics:jaeronautics /var/log/jaeronautics

# 5. Set permissions
echo -e "${GREEN}Step 5: Setting permissions...${NC}"
chown -R jaeronautics:jaeronautics "$APP_DIR"
chmod 600 "$APP_DIR/.env" 2>/dev/null || echo "Warning: .env file not found, remember to create it!"

# 6. Install systemd service
echo -e "${GREEN}Step 6: Installing systemd service...${NC}"
# Update the service file with actual paths
sed "s|/path/to/jaeronautics-1/viewer|$APP_DIR|g" "$APP_DIR/jaeronautics.service" > /tmp/jaeronautics.service.tmp
sed -i "s|User=www-data|User=jaeronautics|g" /tmp/jaeronautics.service.tmp
sed -i "s|Group=www-data|Group=jaeronautics|g" /tmp/jaeronautics.service.tmp

# Copy to systemd directory
cp /tmp/jaeronautics.service.tmp /etc/systemd/system/jaeronautics.service
rm /tmp/jaeronautics.service.tmp

# 7. Reload systemd
echo -e "${GREEN}Step 7: Reloading systemd...${NC}"
systemctl daemon-reload

# 8. Enable service
echo -e "${GREEN}Step 8: Enabling service...${NC}"
systemctl enable jaeronautics

echo -e "${GREEN}=== Installation Complete! ===${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Edit the .env file with your database credentials:"
echo "   nano $APP_DIR/.env"
echo ""
echo "2. Start the service:"
echo "   sudo systemctl start jaeronautics"
echo ""
echo "3. Check the status:"
echo "   sudo systemctl status jaeronautics"
echo ""
echo "4. View logs:"
echo "   sudo journalctl -u jaeronautics -f"
echo ""
echo -e "${YELLOW}Optional: Setup Cloudflare Tunnel${NC}"
echo "Follow the instructions in setup_instructions.md"
