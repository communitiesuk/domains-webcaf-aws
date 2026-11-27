"""Microsoft Entra sign-in for Django Admin."""

import logging
import time
from functools import lru_cache
from uuid import UUID

import jwt
from django.conf import settings
from django.contrib.admin import AdminSite
from django.contrib.auth import BACKEND_SESSION_KEY, login
from django.contrib.auth.backends import ModelBackend
from django.http import HttpResponse, HttpResponseNotFound
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from requests import RequestException

from webcaf.webcaf.models import EntraAdminIdentity

logger = logging.getLogger(__name__)
BACKEND = "webcaf.entra_admin.EntraAdminBackend"
FLOW_SESSION_KEY = "entra_admin_flow"
IDENTITY_SESSION_KEY = "entra_admin_identity"


class EntraAdminBackend(ModelBackend):
    """Restore Entra Admin sessions without accepting password credentials."""

    def authenticate(self, request, **kwargs):
        return None


def entra_client():
    from msal import ConfidentialClientApplication

    return ConfidentialClientApplication(
        settings.ENTRA_ADMIN_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{settings.ENTRA_ADMIN_TENANT_ID}",
        client_credential=settings.ENTRA_ADMIN_CLIENT_SECRET,
        timeout=10,
    )


@lru_cache(maxsize=1)
def entra_signing_keys(tenant_id):
    # Resolve keys from the configured tenant, never from a URL in the token.
    return jwt.PyJWKClient(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys", timeout=10)


def validate_id_token(token):
    tenant_id = settings.ENTRA_ADMIN_TENANT_ID
    signing_key = entra_signing_keys(tenant_id).get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        audience=settings.ENTRA_ADMIN_CLIENT_ID,
        options={"require": ["iss", "aud", "exp", "sub", "tid", "oid"], "strict_aud": True},
    )


class WebcafAdminSite(AdminSite):
    def has_permission(self, request):
        if not super().has_permission(request):
            return False
        if not settings.ENTRA_ADMIN_ENABLED:
            return True
        return (
            request.session.get(BACKEND_SESSION_KEY) == BACKEND
            and EntraAdminIdentity.objects.filter(
                pk=request.session.get(IDENTITY_SESSION_KEY),
                user=request.user,
                tenant_id=settings.ENTRA_ADMIN_TENANT_ID,
            ).exists()
        )

    @method_decorator(never_cache)
    def login(self, request, extra_context=None):
        if not settings.ENTRA_ADMIN_ENABLED:
            return super().login(request, extra_context)
        if request.method != "GET":
            return HttpResponse(status=405, headers={"Allow": "GET"})
        if self.has_permission(request):
            return redirect("admin:index")
        request.session.pop(FLOW_SESSION_KEY, None)
        try:
            flow = entra_client().initiate_auth_code_flow(scopes=[], redirect_uri=settings.ENTRA_ADMIN_REDIRECT_URI)
        except (RequestException, ValueError):
            return authentication_failed()
        if not flow.get("auth_uri"):
            return authentication_failed()
        request.session[FLOW_SESSION_KEY] = flow
        return redirect(flow["auth_uri"])


def authentication_failed():
    logger.warning("Entra Admin authentication failed")
    return HttpResponse("Microsoft sign-in failed. Return to Django Admin to try again.", status=403)


@never_cache
@require_GET
def entra_admin_callback(request):
    if not settings.ENTRA_ADMIN_ENABLED:
        return HttpResponseNotFound()
    flow = request.session.pop(FLOW_SESSION_KEY, None)
    if not flow:
        return authentication_failed()
    try:
        result = entra_client().acquire_token_by_auth_code_flow(flow, request.GET.dict())
    except (RequestException, RuntimeError, ValueError):
        return authentication_failed()
    token = result.get("id_token")
    if "error" in result or not isinstance(token, str):
        return authentication_failed()
    try:
        claims = validate_id_token(token)
    except (jwt.PyJWTError, ValueError):
        return authentication_failed()
    if claims["tid"] != settings.ENTRA_ADMIN_TENANT_ID:
        return authentication_failed()
    try:
        object_id = UUID(claims["oid"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return authentication_failed()

    identity = (
        EntraAdminIdentity.objects.select_related("user")
        .filter(
            tenant_id=settings.ENTRA_ADMIN_TENANT_ID,
            object_id=object_id,
            user__is_active=True,
            user__is_staff=True,
        )
        .first()
    )
    if identity is None:
        logger.warning("Entra Admin access denied for an identity without an active staff link")
        return HttpResponse("Your Microsoft account does not have Django Admin access.", status=403)

    if request.user.is_authenticated and request.user.pk == identity.user_id:
        request.session.cycle_key()
    login(request, identity.user, backend=BACKEND)
    request.session[IDENTITY_SESSION_KEY] = identity.pk
    request.session["last_activity"] = time.time()
    logger.info("Entra Admin sign-in succeeded for WebCAF user %s", identity.user_id)
    response = redirect("admin:index")
    response["Referrer-Policy"] = "no-referrer"
    return response
