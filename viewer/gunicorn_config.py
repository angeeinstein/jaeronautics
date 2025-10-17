"""Gunicorn configuration file"""
import multiprocessing

# Bind to localhost only (Cloudflare tunnel will connect to this)
bind = "127.0.0.1:5000"

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000
timeout = 30
keepalive = 2

# Logging
accesslog = "/var/log/jaeronautics/access.log"
errorlog = "/var/log/jaeronautics/error.log"
loglevel = "info"

# Process naming
proc_name = "jaeronautics"

# Server mechanics
daemon = False
pidfile = "/tmp/jaeronautics.pid"
