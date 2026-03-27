# Gunicorn configuration for production
bind = "127.0.0.1:5000"
workers = 3
threads = 4  # Handle 4 concurrent requests without extra memory per worker
worker_class = "gthread"
timeout = 120
keepalive = 5
errorlog = "/opt/sweetshelves/logs/gunicorn-error.log"
accesslog = "/opt/sweetshelves/logs/gunicorn-access.log"
loglevel = "info"

# Memory optimization
preload_app = True  # Load app before forking to share memory
worker_tmp_dir = '/dev/shm'  # Use RAM disk for worker temp files
