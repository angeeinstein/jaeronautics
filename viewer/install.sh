#!/bin/bash
# Installation/Update script for Jaeronautics Viewer App
# Run with: sudo ./install.sh
# 
# This script is idempotent - safe to run multiple times
# Use for both initial installation AND updates after git pull

set -e  # Exit on error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Detect if this is an update or fresh install
if systemctl list-unit-files | grep -q "^jaeronautics.service"; then
    INSTALL_TYPE="update"
    echo -e "${BLUE}=== Jaeronautics Viewer Update ===${NC}"
else
    INSTALL_TYPE="install"
    echo -e "${GREEN}=== Jaeronautics Viewer Installation ===${NC}"
fi

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Please run as root (use sudo)${NC}"
    exit 1
fi

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
APP_DIR="$SCRIPT_DIR"

echo -e "${YELLOW}Installing from: $APP_DIR${NC}"

# 0. Check system requirements
echo -e "${GREEN}Step 0: Checking system requirements...${NC}"

# Check OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo "Detected OS: $NAME $VERSION"
    if [[ "$ID" != "ubuntu" ]] && [[ "$ID" != "debian" ]]; then
        echo -e "${YELLOW}Warning: This script is designed for Ubuntu/Debian. You may need to adjust package names.${NC}"
    fi
else
    echo -e "${YELLOW}Warning: Could not detect OS version${NC}"
fi

# Check if apt is available (Debian/Ubuntu)
if ! command -v apt-get &> /dev/null; then
    echo -e "${RED}Error: apt-get not found. This script requires Debian/Ubuntu.${NC}"
    exit 1
fi

# Check Python version
echo "Checking Python..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
    PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d'.' -f1)
    PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d'.' -f2)
    echo "  Found Python $PYTHON_VERSION"
    
    if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]); then
        echo -e "${YELLOW}  Warning: Python 3.8+ recommended, you have $PYTHON_VERSION${NC}"
    fi
else
    echo "  Python3 not found, will install"
fi

# Check pip
if command -v pip3 &> /dev/null; then
    echo "  Found pip3 $(pip3 --version | cut -d' ' -f2)"
else
    echo "  pip3 not found, will install"
fi

# Check MySQL client
if command -v mysql &> /dev/null; then
    echo "  Found MySQL client $(mysql --version | awk '{print $5}' | cut -d',' -f1)"
else
    echo "  MySQL client not found, will install"
fi

# Check git
if command -v git &> /dev/null; then
    echo "  Found git $(git --version | awk '{print $3}')"
else
    echo -e "${YELLOW}  Warning: git not found. You may need it for updates.${NC}"
fi

# 1. Install system dependencies
echo -e "${GREEN}Step 1: Installing system dependencies...${NC}"
apt-get update

# Install only what's needed
PACKAGES_TO_INSTALL=""

if ! command -v python3 &> /dev/null; then
    PACKAGES_TO_INSTALL="$PACKAGES_TO_INSTALL python3"
fi

if ! command -v pip3 &> /dev/null; then
    PACKAGES_TO_INSTALL="$PACKAGES_TO_INSTALL python3-pip"
fi

if ! python3 -m venv --help &> /dev/null; then
    PACKAGES_TO_INSTALL="$PACKAGES_TO_INSTALL python3-venv"
fi

if ! command -v mysql &> /dev/null; then
    PACKAGES_TO_INSTALL="$PACKAGES_TO_INSTALL mysql-client"
fi

# Install libmysqlclient-dev for PyMySQL compilation if needed
if ! dpkg -l | grep -q libmysqlclient-dev; then
    PACKAGES_TO_INSTALL="$PACKAGES_TO_INSTALL libmysqlclient-dev"
fi

# Install build essentials for Python packages that need compilation
if ! dpkg -l | grep -q build-essential; then
    PACKAGES_TO_INSTALL="$PACKAGES_TO_INSTALL build-essential"
fi

if [ -n "$PACKAGES_TO_INSTALL" ]; then
    echo "Installing:$PACKAGES_TO_INSTALL"
    apt-get install -y $PACKAGES_TO_INSTALL
else
    echo "All required packages already installed"
fi

# 2. Create application user if doesn't exist
echo -e "${GREEN}Step 2: Setting up application user...${NC}"
if ! id "jaeronautics" &>/dev/null; then
    useradd -r -s /bin/bash -d /opt/jaeronautics jaeronautics
    echo "Created user: jaeronautics"
else
    echo "User jaeronautics already exists - skipping"
fi

# 3. Setup virtual environment
echo -e "${GREEN}Step 3: Setting up Python virtual environment...${NC}"

# Stop service if running (for updates)
if [ "$INSTALL_TYPE" = "update" ]; then
    echo "Stopping service for update..."
    systemctl stop jaeronautics 2>/dev/null || true
fi

if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
    echo "Virtual environment created"
else
    echo "Virtual environment exists - will update packages"
fi

# Activate and install requirements
source "$APP_DIR/venv/bin/activate"
echo "Upgrading pip..."
pip install --upgrade pip -q
echo "Installing/updating Python packages from requirements.txt..."
pip install -r "$APP_DIR/requirements.txt" -q
deactivate
echo "Python packages installed successfully"

# 4. Create log directory
echo -e "${GREEN}Step 4: Creating log directory...${NC}"
if [ ! -d /var/log/jaeronautics ]; then
    mkdir -p /var/log/jaeronautics
    echo "Log directory created"
fi
chown jaeronautics:jaeronautics /var/log/jaeronautics
echo "Log directory permissions set"

# 5. Set permissions
echo -e "${GREEN}Step 5: Setting permissions...${NC}"
chown -R jaeronautics:jaeronautics "$APP_DIR"

# Check if .env exists and handle it
ENV_NEEDS_CONFIGURATION=false
if [ -f "$APP_DIR/.env" ]; then
    chmod 600 "$APP_DIR/.env"
    chown jaeronautics:jaeronautics "$APP_DIR/.env"
    echo ".env file secured with 600 permissions"
    
    # Basic validation of .env file
    if grep -q "your_db_user" "$APP_DIR/.env" || grep -q "your_db_password" "$APP_DIR/.env" || grep -q "change-this" "$APP_DIR/.env"; then
        echo -e "${YELLOW}  Warning: .env file contains default values!${NC}"
        ENV_NEEDS_CONFIGURATION=true
    else
        echo -e "${GREEN}  .env appears to be configured${NC}"
    fi
else
    echo -e "${YELLOW}  .env file not found!${NC}"
    if [ -f "$APP_DIR/.env.example" ]; then
        echo "  Creating .env from .env.example..."
        cp "$APP_DIR/.env.example" "$APP_DIR/.env"
        chmod 600 "$APP_DIR/.env"
        chown jaeronautics:jaeronautics "$APP_DIR/.env"
        echo -e "${GREEN}  .env file created${NC}"
        ENV_NEEDS_CONFIGURATION=true
    else
        echo -e "${RED}  ERROR: .env.example not found!${NC}"
        exit 1
    fi
fi

# 6. Install systemd service
echo -e "${GREEN}Step 6: Configuring systemd service...${NC}"
# Update the service file with actual paths
sed "s|/path/to/jaeronautics-1/viewer|$APP_DIR|g" "$APP_DIR/jaeronautics.service" > /tmp/jaeronautics.service.tmp
sed -i "s|User=www-data|User=jaeronautics|g" /tmp/jaeronautics.service.tmp
sed -i "s|Group=www-data|Group=jaeronautics|g" /tmp/jaeronautics.service.tmp

# Copy to systemd directory
cp /tmp/jaeronautics.service.tmp /etc/systemd/system/jaeronautics.service
rm /tmp/jaeronautics.service.tmp
echo "Service file installed to /etc/systemd/system/"

# 7. Reload systemd
echo -e "${GREEN}Step 7: Reloading systemd daemon...${NC}"
systemctl daemon-reload

# 8. Enable service (if not already enabled)
echo -e "${GREEN}Step 8: Enabling service...${NC}"
if systemctl is-enabled jaeronautics &>/dev/null; then
    echo "Service already enabled"
else
    systemctl enable jaeronautics
    echo "Service enabled to start on boot"
fi

# 9. Start or restart service if .env is configured
echo -e "${GREEN}Step 9: Service management...${NC}"
SERVICE_SHOULD_START=false

if [ "$ENV_NEEDS_CONFIGURATION" = false ]; then
    echo ".env is configured - service can be started"
    SERVICE_SHOULD_START=true
else
    echo -e "${YELLOW}Service NOT started - .env needs configuration${NC}"
fi

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
if [ "$INSTALL_TYPE" = "update" ]; then
    echo -e "${GREEN}║              Update Complete Successfully!                     ║${NC}"
else
    echo -e "${GREEN}║           Installation Complete Successfully!                  ║${NC}"
fi
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Configuration status
if [ "$ENV_NEEDS_CONFIGURATION" = true ]; then
    echo -e "${RED}⚠️  ACTION REQUIRED: Configure .env file${NC}"
    echo ""
    echo -e "${YELLOW}Edit the configuration file:${NC}"
    echo "   ${GREEN}nano $APP_DIR/.env${NC}"
    echo ""
    echo -e "${YELLOW}Update these required values:${NC}"
    echo "   • DB_HOST, DB_PORT, DB_NAME"
    echo "   • DB_USER, DB_PASSWORD"
    echo "   • SECRET_KEY (generate: ${BLUE}python3 -c 'import secrets; print(secrets.token_hex(32))'${NC})"
    echo "   • ADMIN_USERNAME, ADMIN_PASSWORD"
    echo ""
    echo -e "${YELLOW}Then start the service:${NC}"
    echo "   ${GREEN}sudo systemctl start jaeronautics${NC}"
    echo ""
else
    echo -e "${GREEN}✓ Configuration appears complete${NC}"
    echo ""
    
    if [ "$SERVICE_SHOULD_START" = true ]; then
        if [ "$INSTALL_TYPE" = "update" ]; then
            echo -e "${YELLOW}Restarting service...${NC}"
            systemctl restart jaeronautics
            echo -e "${GREEN}✓ Service restarted${NC}"
        else
            echo -e "${YELLOW}Starting service...${NC}"
            systemctl start jaeronautics
            echo -e "${GREEN}✓ Service started${NC}"
        fi
        echo ""
        
        # Give service a moment to start
        sleep 2
        
        # Check service status
        if systemctl is-active --quiet jaeronautics; then
            echo -e "${GREEN}✓ Service is running${NC}"
        else
            echo -e "${RED}✗ Service failed to start${NC}"
            echo -e "${YELLOW}Check logs with: ${GREEN}sudo journalctl -u jaeronautics -n 50${NC}"
        fi
    fi
fi

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}                      Useful Commands                           ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}                      Useful Commands                           ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${YELLOW}Service Management:${NC}"
echo "   sudo systemctl start jaeronautics      ${BLUE}# Start service${NC}"
echo "   sudo systemctl stop jaeronautics       ${BLUE}# Stop service${NC}"
echo "   sudo systemctl restart jaeronautics    ${BLUE}# Restart service${NC}"
echo "   sudo systemctl status jaeronautics     ${BLUE}# Check status${NC}"
echo ""
echo -e "${YELLOW}Logs:${NC}"
echo "   sudo journalctl -u jaeronautics -f     ${BLUE}# Follow logs (live)${NC}"
echo "   sudo journalctl -u jaeronautics -n 50  ${BLUE}# Last 50 lines${NC}"
echo ""
echo -e "${YELLOW}Updates:${NC}"
echo "   cd $APP_DIR"
echo "   git pull                               ${BLUE}# Get latest changes${NC}"
echo "   sudo ./install.sh                      ${BLUE}# Apply updates${NC}"
echo ""
echo -e "${YELLOW}Configuration:${NC}"
echo "   nano $APP_DIR/.env                     ${BLUE}# Edit config${NC}"
echo ""
echo -e "${YELLOW}Installation Details:${NC}"
echo "   • Application: $APP_DIR"
echo "   • Virtual env: $APP_DIR/venv"
echo "   • Service: /etc/systemd/system/jaeronautics.service"
echo "   • Logs: /var/log/jaeronautics/"
echo "   • Running as: jaeronautics user"
echo "   • Listening: http://127.0.0.1:5000 (for Cloudflare Tunnel)"
echo ""

if [ "$INSTALL_TYPE" = "install" ]; then
    echo -e "${BLUE}Next: Setup Cloudflare Tunnel${NC}"
    echo "   See: $APP_DIR/setup_instructions.md (Section 6)"
    echo ""
fi
