"""Disposable host ingress; fixed TLS/UDP destinations, no credentials or egress proxy."""
import selectors
import socket
import socketserver
import threading
import time


def tcp(port, host):
    class Forward(socketserver.BaseRequestHandler):
        def handle(self):
            with socket.create_connection((host, 8443), timeout=10) as upstream:
                upstream.settimeout(10)
                self.request.settimeout(10)
                with selectors.DefaultSelector() as events:
                    events.register(self.request, selectors.EVENT_READ, upstream)
                    events.register(upstream, selectors.EVENT_READ, self.request)
                    while True:
                        ready = events.select(timeout=180)
                        if not ready:
                            return
                        for key, _ in ready:
                            data = key.fileobj.recv(65536)
                            if not data:
                                return
                            key.data.sendall(data)
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True
    Server(('0.0.0.0', port), Forward).serve_forever()


def udp():
    destination = socket.getaddrinfo('coturn', 3478, family=socket.AF_INET, type=socket.SOCK_DGRAM)[0][4]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener, selectors.DefaultSelector() as events:
        listener.bind(('0.0.0.0', 3478))
        listener.setblocking(False)
        events.register(listener, selectors.EVENT_READ)
        clients = {}
        while True:
            now = time.monotonic()
            for key, _ in events.select(timeout=1):
                if key.fileobj is listener:
                    data, address = listener.recvfrom(65536)
                    if address not in clients:
                        upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        upstream.connect(destination)
                        upstream.setblocking(False)
                        clients[address] = [upstream, now]
                        events.register(upstream, selectors.EVENT_READ, address)
                    upstream, _ = clients[address]
                    clients[address][1] = now
                    upstream.send(data)
                else:
                    try:
                        data = key.fileobj.recv(65536)
                        listener.sendto(data, key.data)
                        clients[key.data][1] = now
                    except (ConnectionError, OSError):
                        pass
            for address, (upstream, seen) in list(clients.items()):
                if now - seen > 180:
                    events.unregister(upstream)
                    upstream.close()
                    del clients[address]


if __name__ == '__main__':
    for port, host in ((8443, 'alice'), (8444, 'bob')):
        threading.Thread(target=tcp, args=(port, host), daemon=True).start()
    udp()
