# gunicorn.conf.py
# This file is used by Gunicorn to configure the server.

# Monkey-patching is essential for gevent to work with standard Python libraries.
# This makes standard I/O operations (like network calls) non-blocking.
from gevent import monkey
monkey.patch_all()

# Server settings
bind = "0.0.0.0:5000"
# Use the gevent worker class for asynchronous handling of requests
worker_class = 'geventwebsocket.gunicorn.workers.GeventWebSocketWorker'
# The number of worker processes
workers = 1
# The number of threads per worker
threads = 4
# The maximum number of simultaneous clients
worker_connections = 1000
# The timeout for workers in seconds
timeout = 120
# Log to stdout
accesslog = '-'
errorlog = '-'