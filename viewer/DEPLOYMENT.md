# Quick Deployment Guide

## First Time Setup

1. **Clone the repository on your server:**
   ```bash
   cd /opt  # or wherever you want to install
   git clone https://github.com/angeeinstein/jaeronautics.git
   cd jaeronautics/viewer
   ```

2. **Configure environment variables:**
   ```bash
   cp .env.example .env
   nano .env
   # Fill in your database credentials and secrets
   ```

3. **Run the installation script:**
   ```bash
   sudo ./install.sh
   ```

4. **Start the service:**
   ```bash
   sudo systemctl start jaeronautics
   sudo systemctl status jaeronautics
   ```

## Updating After Git Pull

When you make changes and push to GitHub:

```bash
# On your server
cd /opt/jaeronautics/viewer
git pull
sudo ./update.sh
```

The update script will:
- Stop the service
- Update Python dependencies
- Fix permissions
- Restart the service

## Manual Commands

### View logs:
```bash
sudo journalctl -u jaeronautics -f
```

### Restart service:
```bash
sudo systemctl restart jaeronautics
```

### Check service status:
```bash
sudo systemctl status jaeronautics
```

### Stop service:
```bash
sudo systemctl stop jaeronautics
```

## File Locations After Installation

- **Application files:** `/opt/jaeronautics/viewer/` (or wherever you cloned)
- **Systemd service:** `/etc/systemd/system/jaeronautics.service`
- **Logs:** `/var/log/jaeronautics/`
- **Application user:** `jaeronautics`

## Uninstalling

```bash
cd /opt/jaeronautics/viewer
sudo ./uninstall.sh
# Then manually remove application directory if desired:
# sudo rm -rf /opt/jaeronautics
```

## Troubleshooting

### Service won't start
```bash
sudo journalctl -u jaeronautics -n 50
```

### Check if port is in use
```bash
sudo netstat -tlnp | grep 5000
```

### Permission issues
```bash
cd /opt/jaeronautics/viewer
sudo chown -R jaeronautics:jaeronautics .
sudo chmod 600 .env
```

### Database connection issues
```bash
# Test database connection
mysql -h DB_HOST -u DB_USER -p DB_NAME
```
