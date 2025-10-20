# 🚀 Quick Reference Card

## Installation & Updates - ONE COMMAND

```bash
chmod +x install.sh && sudo ./install.sh
```

That's it! Use it for:
- ✅ Fresh installation  
- ✅ Updates (automatically does git pull)
- ✅ Fixing broken installations
- ✅ Reconfiguring services

---

## Complete Workflows

### First Time Setup
```bash
# 1. Clone
git clone https://github.com/angeeinstein/jaeronautics.git
cd jaeronautics/viewer

# 2. Install (handles everything!)
chmod +x install.sh
sudo ./install.sh

# 3. Configure
nano .env
```

### Updates
```bash
sudo ./install.sh  # Automatically pulls latest code!
```

---

## Common Commands

### Service Management
```bash
sudo systemctl start jaeronautics      # Start
sudo systemctl stop jaeronautics       # Stop
sudo systemctl restart jaeronautics    # Restart
sudo systemctl status jaeronautics     # Status
```

### Logs
```bash
sudo journalctl -u jaeronautics -f        # Follow (live)
sudo journalctl -u jaeronautics -n 50     # Last 50 lines
sudo journalctl -u jaeronautics --since "1 hour ago"
```

### Configuration
```bash
nano /opt/jaeronautics/viewer/.env        # Edit config
sudo systemctl restart jaeronautics       # Apply changes
```

---

## File Locations

| What | Where |
|------|-------|
| **App Code** | `/opt/jaeronautics/viewer/` |
| **Config** | `/opt/jaeronautics/viewer/.env` |
| **Service** | `/etc/systemd/system/jaeronautics.service` |
| **Logs** | `/var/log/jaeronautics/` |
| **Virtual Env** | `/opt/jaeronautics/viewer/venv/` |

---

## Troubleshooting

### Service Won't Start
```bash
sudo journalctl -u jaeronautics -n 50     # Check logs
cat /opt/jaeronautics/viewer/.env         # Verify config
sudo ./install.sh                         # Re-run install
```

### Database Connection Failed
```bash
mysql -h DB_HOST -u DB_USER -p DB_NAME    # Test connection
nano /opt/jaeronautics/viewer/.env        # Fix credentials
sudo systemctl restart jaeronautics       # Restart
```

### Port Already in Use
```bash
sudo netstat -tlnp | grep 5000            # Find what's using it
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

## .env Configuration

Required values:
```bash
DB_HOST=localhost              # Database server
DB_PORT=3306                   # MySQL port
DB_NAME=membership_db          # Database name
DB_USER=your_user              # Database user
DB_PASSWORD=your_password      # Database password

SECRET_KEY=your_secret_key     # Generate with:
                               # python3 -c 'import secrets; print(secrets.token_hex(32))'

ADMIN_USERNAME=admin           # Login username
ADMIN_PASSWORD=secure_pass     # Login password
```

---

## Documentation

- **SIMPLIFIED_WORKFLOW.md** - Complete workflow guide
- **DEPLOYMENT.md** - Quick deployment reference
- **DEPLOYMENT_WORKFLOW.md** - How it all works
- **setup_instructions.md** - Detailed manual setup
- **check_requirements.sh** - Pre-install system check

---

## Pro Tips

💡 **Always backup .env before updates**
```bash
cp .env .env.backup
```

💡 **Check service status after changes**
```bash
sudo systemctl status jaeronautics
```

💡 **Monitor logs during testing**
```bash
sudo journalctl -u jaeronautics -f
```

💡 **Test database connection first**
```bash
mysql -h $DB_HOST -u $DB_USER -p $DB_NAME
```

---

## Getting Help

1. Check logs: `sudo journalctl -u jaeronautics -n 100`
2. Verify config: `cat /opt/jaeronautics/viewer/.env`
3. Re-run install: `sudo ./install.sh`
4. Check documentation in `viewer/` directory

---

**Remember:** `git pull && sudo ./install.sh` handles everything! 🎯
