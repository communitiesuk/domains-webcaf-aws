import os
import subprocess
import sys


def run_settings(env_overrides):
    env = os.environ.copy()
    env.update(env_overrides)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
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
                "s.ENABLED_2FA"
                "]))"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    return result.stdout.strip().split("|")


def test_internal_access_settings():
    settings = run_settings(
        {
            "SSO_MODE": "internal-access",
            "INTERNAL_ACCESS_CLIENT_ID": "test-client-id",
            "INTERNAL_ACCESS_CLIENT_SECRET": "test-client-secret",  # pragma: allowlist secret
            "LOGOUT_REDIRECT_URL": "http://localhost:8010/",
        }
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
        "https://sso.service.security.gov.uk/oauth2/authorization",
        "https://sso.service.security.gov.uk/oauth2/token",
        "https://sso.service.security.gov.uk/oauth2/userinfo",
        "https://sso.service.security.gov.uk/.well-known/jwks.json",
        "https://sso.service.security.gov.uk/sign-out",
        "True",
        "True",
        "http://localhost:8010/",
        "False",
    ]
