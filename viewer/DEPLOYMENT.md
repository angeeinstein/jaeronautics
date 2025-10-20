# Deployment Guide

## Quick Start

### First Time Setup

```bash
# 1. Clone and configure
cd /opt
git clone https://github.com/angeeinstein/jaeronautics.git
cd jaeronautics/viewer
cp .env.example .env
nano .env  # Edit with your database credentials

# 2. Install (one command!)
chmod +x install.sh
sudo ./install.sh
```

### Updates After Changes

```bash
cd /opt/jaeronautics/viewer
git pull
sudo ./install.sh  # Same script!
```

**That's it!** The script handles everything automatically.

---

## What install.sh Does

The script is **intelligent** and works for both fresh installations and updates:

### Auto-Detection
- Checks if service exists → **UPDATE mode** (stops, updates, restarts)
- No service found → **INSTALL mode** (full setup)
- Checks .env configuration → Starts service if ready

### Fresh Install
1. ✓ Checks system requirements (OS, Python, MySQL)
2. ✓ Installs missing packages (only what's needed)
3. ✓ Creates application user (`jaeronautics`)
4. ✓ Sets up Python virtual environment
5. ✓ Installs Python packages
6. ✓ Creates log directory
7. ✓ Creates .env from template (if missing)
8. ✓ Installs systemd service
9. ✓ Starts service (if .env configured)

### Update Mode
1. ✓ Stops the running service
2. ✓ Updates Python dependencies
3. ✓ Updates service configuration
4. ✓ Restarts the service

**Safe to run multiple times!**

---

## Common Tasks

### Service Management
```bash
sudo systemctl start jaeronautics      # Start
sudo systemctl stop jaeronautics       # Stop
sudo systemctl restart jaeronautics    # Restart
sudo systemctl status jaeronautics     # Check status
```

### View Logs
```bash
sudo journalctl -u jaeronautics -f                    # Follow (live)
sudo journalctl -u jaeronautics -n 50                 # Last 50 lines
sudo journalctl -u jaeronautics --since "1 hour ago"  # Last hour
```

### Configuration
```bash
nano /opt/jaeronautics/viewer/.env     # Edit configuration
sudo systemctl restart jaeronautics    # Apply changes
```

### Pre-Installation Check (Optional)
```bash
./check_requirements.sh                # Check system requirements
```

---

## Network Access

The application is configured to be accessible from:

- **Localhost:** `http://localhost:5000` or `http://127.0.0.1:5000`
- **LAN Access:** `http://192.168.1.244:5000` (replace with your server's IP)
- **Internet:** Via Cloudflare Tunnel (see setup_instructions.md)

**Note:** The service binds to `0.0.0.0:5000` which allows access from any IP address on your network.

---

## .env Configuration

Required values in your `.env` file:

```bash
# Database
DB_HOST=localhost
DB_PORT=3306
DB_NAME=membership_db
DB_USER=your_user
DB_PASSWORD=your_password

# Flask
SECRET_KEY=generate_random_key    # Use: python3 -c 'import secrets; print(secrets.token_hex(32))'

# Login
ADMIN_USERNAME=admin
ADMIN_PASSWORD=secure_password
```

---

## File Locations

| What | Where |
|------|-------|
| Application | `/opt/jaeronautics/viewer/` |
| Configuration | `/opt/jaeronautics/viewer/.env` |
| Systemd service | `/etc/systemd/system/jaeronautics.service` |
| Logs | `/var/log/jaeronautics/` |
| Virtual env | `/opt/jaeronautics/viewer/venv/` |

---

## Troubleshooting

### Service Won't Start
```bash
# Check logs for errors
sudo journalctl -u jaeronautics -n 50

# Verify .env configuration
cat /opt/jaeronautics/viewer/.env

# Re-run install script
sudo ./install.sh
```

### Database Connection Failed
```bash
# Test database connection
mysql -h DB_HOST -u DB_USER -p DB_NAME

# Fix credentials in .env
nano /opt/jaeronautics/viewer/.env
sudo systemctl restart jaeronautics
```

### Port Already in Use
```bash
# Find what's using port 5000
sudo netstat -tlnp | grep 5000
# or
sudo ss -tlnp | grep 5000
```

### Permission Issues
```bash
cd /opt/jaeronautics/viewer
sudo chown -R jaeronautics:jaeronautics .
sudo chmod 600 .env
sudo systemctl restart jaeronautics
```

---

## Uninstalling

```bash
cd /opt/jaeronautics/viewer
sudo ./uninstall.sh
# Optionally remove application directory:
# sudo rm -rf /opt/jaeronautics
```

---

## Additional Resources

- **QUICK_REFERENCE.md** - Command cheat sheet
- **setup_instructions.md** - Detailed manual setup guide
- **check_requirements.sh** - Pre-installation system check
