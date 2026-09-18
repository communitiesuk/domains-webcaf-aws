import logging

from authlib.integrations.base_client.errors import OAuthError
from django.shortcuts import redirect
from govuk_onelogin_django.utils import get_oauth_state
from govuk_onelogin_django.views import AuthCallbackView
from requests import RequestException


class OneLoginCallbackView(AuthCallbackView):
    """Handle expected One Login failures without exposing provider details."""

    logger = logging.getLogger("OneLoginCallbackView")

    def get(self, request, *args, **kwargs):
        provider_error = request.GET.get("error")
        if provider_error:
            self.logger.warning("GOV.UK One Login returned an authentication error: %s", provider_error)
            return redirect("authentication-error")

        if not request.GET.get("code") or not get_oauth_state(request):
            self.logger.warning("GOV.UK One Login callback is missing its code or stored state")
            return redirect("authentication-error")

        try:
            response = super().get(request, *args, **kwargs)
        except (KeyError, OAuthError, RequestException, ValueError):
            self.logger.exception("GOV.UK One Login callback failed")
            return redirect("authentication-error")

        if not request.user.is_authenticated:
            self.logger.warning("GOV.UK One Login callback did not authenticate a WebCAF user")
            return redirect("authentication-error")
        return response
