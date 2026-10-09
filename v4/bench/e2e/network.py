"""Accidental-IP-network tripwire (AF_UNIX/stdio remain usable)."""
import socket


def deny_ip_network() -> None:
    original = socket.socket

    class OfflineSocket(original):
        def __init__(self, family=socket.AF_INET, *args, **kwargs):
            if family in {socket.AF_INET, socket.AF_INET6}:
                raise RuntimeError("Evaluation forbids IP sockets, including loopback")
            super().__init__(family, *args, **kwargs)

    socket.socket = OfflineSocket

    def no_dns(*args, **kwargs):
        raise RuntimeError("Evaluation forbids DNS")

    socket.getaddrinfo = no_dns
