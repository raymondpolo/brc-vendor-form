from gevent import monkey
# Monkey-patching is crucial for gevent to work with standard Python libraries.
# It makes standard I/O operations (like network calls) non-blocking.
monkey.patch_all()

from app import create_app, socketio
from gevent.pywsgi import WSGIServer
from geventwebsocket.handler import WebSocketHandler

app = create_app()

if __name__ == '__main__':
    # Use gevent's WSGIServer for production, with a WebSocketHandler
    # to properly handle the WebSocket upgrade requests from Socket.IO.
    http_server = WSGIServer(('', 5000), app, handler_class=WebSocketHandler)
    print("Starting gevent server with WebSocket support...")
    http_server.serve_forever()

