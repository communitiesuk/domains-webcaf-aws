import os
import subprocess
import sys


def run_settings(timeout="4.5"):
    env = os.environ.copy()
    env.update(
        {
            "DEBUG": "true",
            "SECRET_KEY": "test-secret-key",  # pragma: allowlist secret
            "SSO_MODE": "none",
            "NOTIFY_API_KEY": "test-notify-key",  # pragma: allowlist secret
            "NOTIFY_TEST_TEMPLATE_ID": "test-template-id",
            "NOTIFY_API_TIMEOUT_SECONDS": timeout,
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
                "print(settings.NOTIFY_API_TIMEOUT_SECONDS)"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    return result


def test_notify_configuration_is_independent_of_sso_mode():
    result = run_settings()

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "4.5"


def test_notify_timeout_must_be_finite_positive_and_bounded():
    for timeout in ("0", "-1", "nan", "inf", "31"):
        result = run_settings(timeout)

        assert result.returncode != 0
        assert "NOTIFY_API_TIMEOUT_SECONDS must be a finite number greater than 0 and no more than 30" in result.stderr
