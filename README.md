# jaeronautics

Club membership management system with separate applications for viewing and form submission.

## Project Structure

- **`viewer/`** - Flask app for viewing club members (with login authentication)
- **`membership-form/`** - Flask app for membership form submission (to be created)

## Setup

### Viewer App (Member Database Viewer)

**Production Deployment:**
```bash
# Clone the repository
cd /opt
git clone https://github.com/angeeinstein/jaeronautics.git
cd jaeronautics/viewer

# Install (creates .env automatically if missing)
sudo ./install.sh

# Configure your database credentials
nano .env

# Start the service
sudo systemctl start jaeronautics
```

**Or configure .env first, then install:**
```bash
cd /opt
git clone https://github.com/angeeinstein/jaeronautics.git
cd jaeronautics/viewer
cp .env.example .env
nano .env  # Configure now
sudo ./install.sh  # Detects configured .env and starts automatically
```

**Updates:**
```bash
cd /opt/jaeronautics/viewer
git pull
sudo ./install.sh  # Same script handles updates!
```

**Documentation:**
- [DEPLOYMENT.md](viewer/DEPLOYMENT.md) - Complete deployment guide
- [QUICK_REFERENCE.md](viewer/QUICK_REFERENCE.md) - Command cheat sheet
- [setup_instructions.md](viewer/setup_instructions.md) - Detailed manual setup

**Local Development:**
```bash
cd viewer
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
nano .env  # Configure database
python app.py
```

### Membership Form
To be implemented in the `membership-form/` directory.

## Deployment

Both applications can run as separate systemd services and be exposed through Cloudflare tunnels.
