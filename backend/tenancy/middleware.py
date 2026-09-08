"""Establishes the database-level org context for each request."""
import logging

from rest_framework.authtoken.models import Token

from .models import OrgMembership
from .rls import org_context

logger = logging.getLogger(__name__)


def _user_from_token(request):
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header.lower().startswith("token "):
        return None
    key = header.split(" ", 1)[1].strip()
    if not key:
        return None
    # auth_user and authtoken_token are global, not tenant tables, so this
    # lookup works before any org context is established.
    token = Token.objects.filter(key=key).select_related("user").first()
    return token.user if token else None


def resolve_org_id(request):
    user = _user_from_token(request)
    if user is None:
        session_user = getattr(request, "user", None)
        if session_user is not None and session_user.is_authenticated:
            user = session_user
    if user is None:
        return None
    membership = OrgMembership.objects.filter(user=user).first()
    return membership.org_id if membership else None


class OrgContextMiddleware:
    """Pins the connection to the caller's org for the whole request.

    An anonymous or org-less caller gets no context at all, which means the
    database returns nothing for tenant tables. Failing closed is the point.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        org_id = resolve_org_id(request)
        request.org_id = org_id
        if org_id is None:
            return self.get_response(request)

        with org_context(org_id):
            response = self.get_response(request)
            # DRF returns a lazily-rendered response. Rendering can evaluate
            # querysets, and it normally happens after the middleware chain has
            # unwound, i.e. after the org context is gone. Force it here so
            # every query runs inside the context that authorised it.
            if hasattr(response, "render") and callable(response.render):
                if not getattr(response, "is_rendered", True):
                    response.render()
            return response
