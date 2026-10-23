import os
import subprocess
import sys


def test_notify_configuration_is_independent_of_sso_mode():
    env = os.environ.copy()
    env.update(
        {
            "DEBUG": "true",
            "SECRET_KEY": "test-secret-key",  # pragma: allowlist secret
            "SSO_MODE": "none",
            "NOTIFY_API_KEY": "test-notify-key",  # pragma: allowlist secret
            "NOTIFY_TEST_TEMPLATE_ID": "test-template-id",
            "NOTIFY_API_TIMEOUT_SECONDS": "4.5",
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import webcaf.settings as settings; "
                "assert settings.NOTIFY_API_KEY == 'test-notify-key'; "  # pragma: allowlist secret
                "assert settings.NOTIFY_TEST_TEMPLATE_ID == 'test-template-id'; "
                "assert settings.NOTIFY_API_TIMEOUT_SECONDS == 4.5"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
