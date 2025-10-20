# jaeronautics

Club membership management system with separate applications for viewing and form submission.

## Project Structure

- **`viewer/`** - Flask app for viewing club members (with login authentication)
- **`membership-form/`** - Flask app for membership form submission (to be created)

## Setup

### Viewer App (Member Database Viewer)

**Simple Deployment (Two Commands Only!):**
```bash
# 1. Clone the repository
cd /opt
git clone https://github.com/angeeinstein/jaeronautics.git
cd jaeronautics/viewer

# 2. Install everything (handles .env creation, dependencies, service setup)
chmod +x install.sh
sudo ./install.sh

# 3. Configure your database credentials
nano .env

# 4. Start the service
sudo systemctl start jaeronautics
```

**Alternative (Configure First):**
```bash
# Same clone step
cd /opt
git clone https://github.com/angeeinstein/jaeronautics.git
cd jaeronautics/viewer

# Configure before install
cp .env.example .env
nano .env  # Configure database credentials

# Install (detects configured .env and starts automatically)
chmod +x install.sh
sudo ./install.sh
```

**Updates (ONE Command!):**
```bash
cd /opt/jaeronautics/viewer
sudo ./install.sh  # Automatically does git pull + updates everything!
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
