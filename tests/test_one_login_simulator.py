import os
from html.parser import HTMLParser
from unittest import skipUnless

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import LiveServerTestCase, override_settings


class SimulatorFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = ""
        self.fields = {}
        self._textarea_name = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.action = attributes["action"]
        elif tag == "input" and attributes.get("name"):
            if attributes.get("type") == "checkbox" and "checked" not in attributes:
                return
            self.fields[attributes["name"]] = attributes.get("value", "")
        elif tag == "textarea":
            self._textarea_name = attributes["name"]
            self.fields[self._textarea_name] = ""

    def handle_data(self, data):
        if self._textarea_name:
            self.fields[self._textarea_name] += data

    def handle_endtag(self, tag):
        if tag == "textarea":
            self._textarea_name = None


@skipUnless(
    os.environ.get("RUN_ONE_LOGIN_SIMULATOR_TESTS") == "true",
    "requires the local GOV.UK One Login simulator",
)
@override_settings(SESSION_COOKIE_SECURE=False)
class OneLoginSimulatorTest(LiveServerTestCase):
    host = "localhost"

    def test_authenticates_verified_user(self):
        get_user_model().objects.create_user(username="alice@example.gov.uk", email="alice@example.gov.uk")
        simulator_url = settings.GOV_UK_ONE_LOGIN_OPENID_CONFIG_URL.removesuffix("/.well-known/openid-configuration")

        session = requests.Session()
        simulator_config = session.get(f"{simulator_url}/config", timeout=10).json()
        original_redirect_urls = simulator_config["clientConfiguration"]["redirectUrls"]
        original_logout_urls = simulator_config["clientConfiguration"]["postLogoutRedirectUrls"]
        original_response_configuration = simulator_config["responseConfiguration"]
        original_error_configuration = simulator_config["errorConfiguration"]
        config_response = session.post(
            f"{simulator_url}/config",
            json={
                "clientConfiguration": {
                    "redirectUrls": [f"{self.live_server_url}/one-login/callback/"],
                    "postLogoutRedirectUrls": [f"{self.live_server_url}/"],
                },
                "responseConfiguration": {
                    "sub": "urn:webcaf:test:alice",
                    "email": "alice@example.gov.uk",
                    "emailVerified": True,
                },
                "errorConfiguration": {},
            },
            timeout=10,
        )
        config_response.raise_for_status()

        try:
            response = session.get(f"{self.live_server_url}/my-account/", timeout=10)
            if response.url.startswith(f"{simulator_url}/authorize"):
                parser = SimulatorFormParser()
                parser.feed(response.text)
                self.assertEqual(parser.action, f"{simulator_url}/form-submit")
                response = session.post(parser.action, data=parser.fields, timeout=10)

            self.assertEqual(response.url, f"{self.live_server_url}/my-account/")
            self.assertEqual(response.status_code, 403)
            self.assertIn("You do not have a profile set up", response.text)
        finally:
            session.post(
                f"{simulator_url}/config",
                json={
                    "clientConfiguration": {
                        "redirectUrls": original_redirect_urls,
                        "postLogoutRedirectUrls": original_logout_urls,
                    },
                    "responseConfiguration": original_response_configuration,
                    "errorConfiguration": original_error_configuration,
                },
                timeout=10,
            ).raise_for_status()
