# Deployment Workflow

## How It Works

### System Files Location Strategy

Instead of manually copying files to `/etc/`, we use **installation scripts** that:

1. Keep configuration files in your Git repository (easy version control)
2. Automatically copy them to system directories during installation
3. Update paths and settings automatically

### Benefits

✅ **Version Control**: All configs stay in Git  
✅ **Easy Updates**: Just `git pull` + `./update.sh`  
✅ **Automated**: No manual file copying  
✅ **Reproducible**: Same process on any server  
✅ **Safe**: Original configs in `/etc/` are generated, not directly edited  

## Workflow

### Initial Deployment
```bash
# On your server
git clone https://github.com/angeeinstein/jaeronautics.git
cd jaeronautics/viewer
cp .env.example .env
nano .env  # Configure
sudo ./install.sh
```

### Making Changes
```bash
# On your development machine (Windows)
# Edit files, commit, push to GitHub
git add .
git commit -m "Updated configuration"
git push
```

### Updating Server
```bash
# On your server
cd /opt/jaeronautics/viewer
git pull
sudo ./update.sh  # Automatically handles everything
```

## What Each Script Does

### `install.sh` (First-time setup)
- Installs system dependencies
- Creates application user `jaeronautics`
- Sets up Python virtual environment
- Creates log directory in `/var/log/jaeronautics/`
- **Copies** `jaeronautics.service` → `/etc/systemd/system/`
- Sets correct permissions
- Enables service to start on boot

### `update.sh` (After git pull)
- Stops the service
- Updates Python dependencies
- Fixes file permissions
- Reloads systemd (picks up any service file changes)
- Restarts the service

### `uninstall.sh` (Removal)
- Stops and disables service
- Removes service file from `/etc/systemd/system/`
- Cleans up logs and user

## File Locations

| File in Git | Deployed To | Why |
|-------------|-------------|-----|
| `jaeronautics.service` | `/etc/systemd/system/` | Systemd requires it here |
| `.env` | Stays in app dir | Never committed to Git |
| `app.py`, etc. | App directory | Application files |
| Logs | `/var/log/jaeronautics/` | Standard Linux location |

## Alternative Approaches

### Option 1: Symlinks (Not Recommended)
```bash
ln -s /opt/jaeronautics/viewer/jaeronautics.service /etc/systemd/system/
```
❌ Issues: Systemd doesn't always follow symlinks well, permission issues

### Option 2: Configuration Management Tools
- **Ansible**: Good for multiple servers
- **Docker**: Good for containerization
- **Our approach**: Simple, works great for small deployments

## Security Notes

- Service runs as dedicated `jaeronautics` user (not root)
- `.env` file has restricted permissions (600)
- Logs in standard location with proper ownership
- Systemd provides process isolation

## Nginx Configuration (Optional)

If you want to use Nginx instead of/alongside Cloudflare Tunnel:

```nginx
# /etc/nginx/sites-available/jaeronautics
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable it:
```bash
sudo ln -s /etc/nginx/sites-available/jaeronautics /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

This would also be handled by an install script if needed!
