#!/usr/bin/env python3
"""Deny and record Python network attempts during the offline CLI review."""
import runpy
import socket
import sys
attempted = False
def deny(*args, **kwargs):
    global attempted
    attempted = True
    raise RuntimeError('network forbidden during offline review')
socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.create_connection = deny
socket.getaddrinfo = deny
sys.argv = sys.argv[1:]
try:
    runpy.run_path(sys.argv[0], run_name='__main__')
finally:
    if attempted:
        raise RuntimeError('offline review attempted network access')
