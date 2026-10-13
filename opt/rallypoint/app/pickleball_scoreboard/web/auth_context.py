"""Request-only host access, validated by the shared WSGI dispatcher."""
from flask import request, session


def host_identity():
    return request.environ.get("rallypoint.host_admin")


def effective_role():
    return "ADMIN" if host_identity() else session.get("role")
