# jaeronautics

Club membership management system with separate applications for viewing and form submission.

## Project Structure

- **`viewer/`** - Flask app for viewing club members (with login authentication)
- **`membership-form/`** - Flask app for membership form submission (to be created)

## Setup

### Viewer App (Member Database Viewer)

**Quick Production Deployment:**
```bash
cd /opt
git clone https://github.com/angeeinstein/jaeronautics.git
cd jaeronautics/viewer
cp .env.example .env
nano .env  # Configure your database credentials
sudo ./install.sh
```

See documentation:
- [DEPLOYMENT.md](viewer/DEPLOYMENT.md) - Quick deployment guide
- [DEPLOYMENT_WORKFLOW.md](viewer/DEPLOYMENT_WORKFLOW.md) - How deployment works
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
