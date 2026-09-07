# Gunicorn configuration for production
bind = "127.0.0.1:5000"
workers = 1  # Jobs, scan state, and caches are process-local; share them across threads.
threads = 4  # Handle 4 concurrent requests without extra memory per worker
worker_class = "gthread"
timeout = 120
keepalive = 5
errorlog = "/opt/sweetshelves/logs/gunicorn-error.log"
accesslog = "/opt/sweetshelves/logs/gunicorn-access.log"
loglevel = "info"

# Memory optimization
preload_app = False  # Start application background threads in the worker, after fork.
worker_tmp_dir = '/dev/shm'  # Use RAM disk for worker temp files
