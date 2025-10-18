# gunicorn.conf.py
# This file is used by Gunicorn to configure the server.

# Monkey-patching is essential for gevent to work with standard Python libraries.
from gevent import monkey
monkey.patch_all()

# Server settings
bind = "0.0.0.0:5000"
# Use the gevent worker class for asynchronous handling of requests
worker_class = 'geventwebsocket.gunicorn.workers.GeventWebSocketWorker'
workers = 1 # Keep workers=1 on Render free tier
threads = 4
worker_connections = 1000
timeout = 120
accesslog = '-'
errorlog = '-'