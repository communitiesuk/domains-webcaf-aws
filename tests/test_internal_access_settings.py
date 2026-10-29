import os
import subprocess
import sys

import pytest


def run_settings(env_overrides, *, use_default=False):
    env = os.environ.copy()
    env.update(env_overrides)
    if use_default:
        env.pop("SSO_MODE", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from unittest.mock import patch; "
                "patch('environ.Env.read_env').start(); "
                "patch('dotenv.load_dotenv').start(); "
                "import webcaf.settings as s; "
                "print('|'.join(str(x) for x in ["
                "s.SSO_MODE,"
                "s.AUTHENTICATION_BACKENDS,"
                "s.OIDC_RP_CLIENT_ID,"
                "s.OIDC_RP_CLIENT_SECRET,"
                "s.OIDC_OP_AUTHORIZATION_ENDPOINT,"
                "s.OIDC_OP_TOKEN_ENDPOINT,"
                "s.OIDC_OP_USER_ENDPOINT,"
                "s.OIDC_OP_JWKS_ENDPOINT,"
                "s.OIDC_OP_LOGOUT_ENDPOINT,"
                "s.OIDC_STORE_ID_TOKEN,"
                "s.OIDC_STORE_ACCESS_TOKEN,"
                "s.LOGOUT_REDIRECT_URL,"
                "s.ENABLED_2FA,"
                "s.OIDC_CALLBACK_CLASS"
                "]))"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    return result.stdout.strip().split("|")


@pytest.mark.parametrize("use_default", [False, True])
def test_internal_access_settings(use_default):
    settings = run_settings(
        {
            "SSO_MODE": "internal-access",
            "ENVIRONMENT": "local",
            "INTERNAL_ACCESS_CLIENT_ID": "test-client-id",
            "INTERNAL_ACCESS_CLIENT_SECRET": "test-client-secret",  # pragma: allowlist secret
            "INTERNAL_ACCESS_AUTHORIZATION_ENDPOINT": "https://example.test/authorize",
            "INTERNAL_ACCESS_TOKEN_ENDPOINT": "https://example.test/token",
            "INTERNAL_ACCESS_USERINFO_ENDPOINT": "https://example.test/userinfo",
            "INTERNAL_ACCESS_JWKS_ENDPOINT": "https://example.test/jwks",
            "INTERNAL_ACCESS_LOGOUT_ENDPOINT": "https://example.test/logout",
            "LOGOUT_REDIRECT_URL": "http://localhost:8010/",
        },
        use_default=use_default,
    )

    assert settings == [
        "internal-access",
        (
            "('webcaf.auth.OIDCBackend', "
            "'axes.backends.AxesStandaloneBackend', "
            "'django.contrib.auth.backends.ModelBackend')"
        ),
        "test-client-id",
        "test-client-secret",
        "https://example.test/authorize",
        "https://example.test/token",
        "https://example.test/userinfo",
        "https://example.test/jwks",
        "https://example.test/logout",
        "True",
        "True",
        "http://localhost:8010/",
        "False",
        "webcaf.webcaf.views.internal_access.InternalAccessCallbackView",
    ]


@pytest.mark.parametrize("environment", ["dev", "test", "staging", "prod"])
def test_internal_access_disables_2fa_in_all_environments(environment):
    settings = run_settings(
        {
            "SSO_MODE": "internal-access",
            "ENVIRONMENT": environment,
            "INTERNAL_ACCESS_CLIENT_ID": "test-client-id",
            "INTERNAL_ACCESS_CLIENT_SECRET": "test-client-secret",  # pragma: allowlist secret
            "INTERNAL_ACCESS_AUTHORIZATION_ENDPOINT": "https://example.test/authorize",
            "INTERNAL_ACCESS_TOKEN_ENDPOINT": "https://example.test/token",
            "INTERNAL_ACCESS_USERINFO_ENDPOINT": "https://example.test/userinfo",
            "INTERNAL_ACCESS_JWKS_ENDPOINT": "https://example.test/jwks",
            "INTERNAL_ACCESS_LOGOUT_ENDPOINT": "https://example.test/logout",
            "LOGOUT_REDIRECT_URL": "http://localhost:8010/",
        }
    )

    assert settings[-2] == "False"
