#!/bin/bash
# Pre-installation check script for Jaeronautics Viewer App
# Run with: ./check_requirements.sh
# This checks if all requirements are met BEFORE running install.sh

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     Jaeronautics Viewer - Requirements Check                  ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

ALL_OK=true

# Check OS
echo -e "${BLUE}[1] Operating System${NC}"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo -e "    ${GREEN}✓${NC} Detected: $NAME $VERSION"
    
    if [[ "$ID" != "ubuntu" ]] && [[ "$ID" != "debian" ]]; then
        echo -e "    ${YELLOW}⚠${NC}  Warning: Recommended OS is Ubuntu/Debian"
        echo -e "       You may need to adjust package installation commands"
    fi
else
    echo -e "    ${YELLOW}⚠${NC}  Could not detect OS"
    ALL_OK=false
fi
echo ""

# Check if running on a server (non-desktop environment is better for services)
if [ -n "$DISPLAY" ]; then
    echo -e "    ${YELLOW}ℹ${NC}  Desktop environment detected (not typical for servers)"
fi

# Check root/sudo access
echo -e "${BLUE}[2] Root/Sudo Access${NC}"
if [ "$EUID" -eq 0 ]; then
    echo -e "    ${YELLOW}⚠${NC}  Running as root (you should use 'sudo ./check_requirements.sh')"
elif sudo -n true 2>/dev/null; then
    echo -e "    ${GREEN}✓${NC} Sudo access available"
else
    echo -e "    ${YELLOW}⚠${NC}  Sudo access required for installation"
    echo -e "       You'll need to run: ${GREEN}sudo ./install.sh${NC}"
fi
echo ""

# Check Python
echo -e "${BLUE}[3] Python${NC}"
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
    PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d'.' -f1)
    PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d'.' -f2)
    
    if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 8 ]; then
        echo -e "    ${GREEN}✓${NC} Python $PYTHON_VERSION installed"
    else
        echo -e "    ${YELLOW}⚠${NC}  Python $PYTHON_VERSION found (3.8+ recommended)"
        echo -e "       Will be updated during installation"
    fi
else
    echo -e "    ${RED}✗${NC} Python3 not found"
    echo -e "       Will be installed automatically"
fi
echo ""

# Check pip
echo -e "${BLUE}[4] pip (Python Package Manager)${NC}"
if command -v pip3 &> /dev/null; then
    PIP_VERSION=$(pip3 --version | cut -d' ' -f2)
    echo -e "    ${GREEN}✓${NC} pip3 $PIP_VERSION installed"
else
    echo -e "    ${RED}✗${NC} pip3 not found"
    echo -e "       Will be installed automatically"
fi
echo ""

# Check venv
echo -e "${BLUE}[5] Python Virtual Environment Support${NC}"
if python3 -m venv --help &> /dev/null 2>&1; then
    echo -e "    ${GREEN}✓${NC} python3-venv is available"
else
    echo -e "    ${RED}✗${NC} python3-venv not found"
    echo -e "       Will be installed automatically"
fi
echo ""

# Check MySQL client
echo -e "${BLUE}[6] MySQL Client${NC}"
if command -v mysql &> /dev/null; then
    MYSQL_VERSION=$(mysql --version | awk '{print $5}' | cut -d',' -f1)
    echo -e "    ${GREEN}✓${NC} MySQL client $MYSQL_VERSION installed"
else
    echo -e "    ${RED}✗${NC} MySQL client not found"
    echo -e "       Will be installed automatically"
fi
echo ""

# Check MySQL server connectivity (if credentials are in .env)
echo -e "${BLUE}[7] MySQL Database Connectivity${NC}"
if [ -f ".env" ]; then
    source .env 2>/dev/null
    if [ -n "$DB_HOST" ] && [ "$DB_HOST" != "localhost" ] && [ "$DB_HOST" != "your_db_user" ]; then
        if command -v mysql &> /dev/null && [ -n "$DB_USER" ] && [ -n "$DB_PASSWORD" ]; then
            if mysql -h "$DB_HOST" -P "${DB_PORT:-3306}" -u "$DB_USER" -p"$DB_PASSWORD" -e "SELECT 1;" &> /dev/null; then
                echo -e "    ${GREEN}✓${NC} Can connect to MySQL database"
                
                # Check if database exists
                if mysql -h "$DB_HOST" -P "${DB_PORT:-3306}" -u "$DB_USER" -p"$DB_PASSWORD" -e "USE $DB_NAME;" &> /dev/null; then
                    echo -e "    ${GREEN}✓${NC} Database '$DB_NAME' exists"
                else
                    echo -e "    ${YELLOW}⚠${NC}  Database '$DB_NAME' not found"
                    echo -e "       You'll need to create it first"
                    ALL_OK=false
                fi
            else
                echo -e "    ${RED}✗${NC} Cannot connect to MySQL database"
                echo -e "       Check .env credentials"
                ALL_OK=false
            fi
        else
            echo -e "    ${YELLOW}⚠${NC}  Cannot test connection (mysql client or credentials missing)"
        fi
    else
        echo -e "    ${YELLOW}⚠${NC}  .env not configured yet"
        echo -e "       Configure it before starting the service"
    fi
else
    echo -e "    ${YELLOW}⚠${NC}  .env file not found"
    echo -e "       Will be created from .env.example during installation"
fi
echo ""

# Check git (for updates)
echo -e "${BLUE}[8] Git (for updates)${NC}"
if command -v git &> /dev/null; then
    GIT_VERSION=$(git --version | awk '{print $3}')
    echo -e "    ${GREEN}✓${NC} git $GIT_VERSION installed"
else
    echo -e "    ${YELLOW}⚠${NC}  git not found"
    echo -e "       Recommended for easy updates (git pull)"
fi
echo ""

# Check disk space
echo -e "${BLUE}[9] Disk Space${NC}"
AVAILABLE_SPACE=$(df -BM . | tail -1 | awk '{print $4}' | sed 's/M//')
if [ "$AVAILABLE_SPACE" -gt 500 ]; then
    echo -e "    ${GREEN}✓${NC} Available space: ${AVAILABLE_SPACE}MB"
else
    echo -e "    ${YELLOW}⚠${NC}  Low disk space: ${AVAILABLE_SPACE}MB"
    echo -e "       Recommended: 500MB+"
fi
echo ""

# Check if port 5000 is available
echo -e "${BLUE}[10] Port Availability${NC}"
if command -v netstat &> /dev/null || command -v ss &> /dev/null; then
    if netstat -tuln 2>/dev/null | grep -q ":5000 " || ss -tuln 2>/dev/null | grep -q ":5000 "; then
        echo -e "    ${RED}✗${NC} Port 5000 is already in use"
        echo -e "       You may need to stop the conflicting service"
        ALL_OK=false
    else
        echo -e "    ${GREEN}✓${NC} Port 5000 is available"
    fi
else
    echo -e "    ${YELLOW}⚠${NC}  Cannot check port availability (netstat/ss not found)"
fi
echo ""

# Check systemd
echo -e "${BLUE}[11] Systemd${NC}"
if command -v systemctl &> /dev/null; then
    echo -e "    ${GREEN}✓${NC} systemd is available"
else
    echo -e "    ${RED}✗${NC} systemd not found"
    echo -e "       This is required for service management"
    ALL_OK=false
fi
echo ""

# Summary
echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                          Summary                               ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

if $ALL_OK; then
    echo -e "${GREEN}✓ All critical requirements are met!${NC}"
    echo ""
    echo -e "${GREEN}Ready to install. Run:${NC}"
    echo -e "    ${BLUE}sudo ./install.sh${NC}"
else
    echo -e "${YELLOW}⚠ Some requirements need attention${NC}"
    echo ""
    echo -e "${YELLOW}You can still proceed with installation:${NC}"
    echo -e "    ${BLUE}sudo ./install.sh${NC}"
    echo ""
    echo -e "${YELLOW}Missing dependencies will be installed automatically.${NC}"
    echo -e "${YELLOW}Address any ${RED}✗ errors${YELLOW} shown above before starting the service.${NC}"
fi

echo ""
echo -e "${BLUE}Documentation:${NC}"
echo -e "    • Installation: ${GREEN}./install.sh${NC}"
echo -e "    • Deployment Guide: ${GREEN}DEPLOYMENT.md${NC}"
echo -e "    • Full Setup: ${GREEN}setup_instructions.md${NC}"
echo ""
