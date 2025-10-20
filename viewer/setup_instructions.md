# Setup Instructions for Jaeronautics Club Membership App

## Prerequisites
- Linux server (Ubuntu/Debian recommended)
- Python 3.8 or higher
- MySQL/MariaDB database
- Cloudflare account for tunnel setup

## Quick Start (Recommended)

For the easiest deployment, use the automated installation script:

```bash
# Clone the repository
cd /opt
git clone https://github.com/angeeinstein/jaeronautics.git
cd jaeronautics/viewer

# Configure your environment
cp .env.example .env
nano .env  # Fill in your database credentials

# Run installation script
sudo ./install.sh

# Start the service
sudo systemctl start jaeronautics
```

**See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed deployment and update instructions.**

---

## Manual Installation Steps

If you prefer manual installation or need more control:

### 1. Clone and Setup the Application

```bash
# Navigate to your project directory
cd /path/to/jaeronautics-1/viewer

# Create a virtual environment
python3 -m venv venv

# Activate the virtual environment
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Edit the `.env` file and update it with your actual database credentials:

```bash
nano .env
```

Update the following values:
- `DB_HOST`: Your database host (e.g., localhost or IP address)
- `DB_PORT`: Database port (default: 3306)
- `DB_NAME`: Your database name
- `DB_USER`: Database username
- `DB_PASSWORD`: Database password
- `SECRET_KEY`: Generate a random secret key (use: `python -c "import secrets; print(secrets.token_hex(32))"`)
- `ADMIN_USERNAME`: Your admin username
- `ADMIN_PASSWORD`: Your admin password

### 3. Database Setup

Make sure your MySQL database has a table named `members`. Example schema:

```sql
CREATE DATABASE IF NOT EXISTS membership_db;
USE membership_db;

CREATE TABLE members (
    id INT AUTO_INCREMENT PRIMARY KEY,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    phone VARCHAR(20),
    join_date DATE,
    membership_status ENUM('active', 'inactive', 'pending') DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

Adjust the table structure based on your actual database schema.

### 4. Test the Application

```bash
# Activate virtual environment if not already activated
source venv/bin/activate

# Run the app in development mode
python app.py
```

Test the application:
- **Locally:** `http://localhost:5000` or `http://127.0.0.1:5000`
- **From LAN:** `http://192.168.1.244:5000` (replace with your server's IP)

### 5. Setup Systemd Service

```bash
# Create log directory
sudo mkdir -p /var/log/jaeronautics
sudo chown www-data:www-data /var/log/jaeronautics

# Edit the service file with actual paths
sudo nano jaeronautics.service
```

Update the following in `jaeronautics.service`:
- `User` and `Group`: Change to your user or keep as www-data
- `WorkingDirectory`: Set to full path (e.g., `/home/user/jaeronautics-1/viewer`)
- `Environment`: Set to full path of venv (e.g., `/home/user/jaeronautics-1/viewer/venv/bin`)
- `ExecStart`: Set to full path (e.g., `/home/user/jaeronautics-1/viewer/venv/bin/gunicorn`)

```bash
# Copy service file to systemd
sudo cp jaeronautics.service /etc/systemd/system/

# Reload systemd daemon
sudo systemctl daemon-reload

# Enable the service to start on boot
sudo systemctl enable jaeronautics

# Start the service
sudo systemctl start jaeronautics

# Check status
sudo systemctl status jaeronautics
```

### 6. Setup Cloudflare Tunnel

```bash
# Install cloudflared
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb

# Login to Cloudflare
cloudflared tunnel login

# Create a tunnel
cloudflared tunnel create jaeronautics

# Configure the tunnel
nano ~/.cloudflared/config.yml
```

Add the following configuration:

```yaml
tunnel: YOUR_TUNNEL_ID
credentials-file: /home/YOUR_USER/.cloudflared/YOUR_TUNNEL_ID.json

ingress:
  - hostname: your-domain.com
    service: http://127.0.0.1:5000  # Cloudflare connects locally
  - service: http_status:404
```

```bash
# Route DNS
cloudflared tunnel route dns jaeronautics your-domain.com

# Run the tunnel as a service
sudo cloudflared service install
sudo systemctl start cloudflared
sudo systemctl enable cloudflared
```

### 7. Useful Commands

```bash
# View application logs
sudo journalctl -u jaeronautics -f

# Restart the application
sudo systemctl restart jaeronautics

# Stop the application
sudo systemctl stop jaeronautics

# View Cloudflare tunnel logs
sudo journalctl -u cloudflared -f
```

## Security Notes

1. **Never commit `.env` file to version control** - It's already in `.gitignore`
2. **Use strong passwords** for database and admin login
3. **Generate a strong SECRET_KEY** using the command provided above
4. **Consider using environment variables** stored securely on the server
5. **Implement HTTPS** through Cloudflare (automatic with tunnel)
6. **Regular backups** of your database
7. **Consider implementing rate limiting** for login attempts

## Troubleshooting

### Database Connection Issues
- Verify database credentials in `.env`
- Check if MySQL service is running: `sudo systemctl status mysql`
- Test database connection: `mysql -u DB_USER -p -h DB_HOST DB_NAME`

### Service Won't Start
- Check logs: `sudo journalctl -u jaeronautics -n 50`
- Verify file permissions
- Ensure virtual environment is properly set up
- Check if port 5000 is available

### Cloudflare Tunnel Issues
- Check tunnel status: `cloudflared tunnel list`
- View logs: `sudo journalctl -u cloudflared -f`
- Verify DNS settings in Cloudflare dashboard

## Customization

### Modify Database Query
Edit `app.py` in the `members()` function to match your actual table structure:

```python
cursor.execute("SELECT * FROM your_table_name ORDER BY your_column")
```

### Change Login System
For a more robust authentication system, consider implementing:
- Flask-Login for user session management
- Password hashing with werkzeug.security
- User database table for multiple users
- Role-based access control

## Support

For issues or questions, refer to the documentation:
- Flask: https://flask.palletsprojects.com/
- Gunicorn: https://gunicorn.org/
- Cloudflare Tunnel: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/
