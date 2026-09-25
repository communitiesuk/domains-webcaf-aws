import argparse
import base64
import hashlib
import os

import requests

SIMULATOR_URL = os.environ.get("ONE_LOGIN_SIMULATOR_URL", "http://localhost:3000")
USER_PRESETS = {
    "alice": ("alice@example.gov.uk", True),
    "admin": ("admin@example.gov.uk", True),
    "organisation_user": ("organisation_user@example.gov.uk", True),
    "organisation_lead": ("organisation_lead@example.gov.uk", True),
    "cyber_advisor": ("cyber_advisor@example.gov.uk", True),
    "assessor": ("test_assessor@example.com", True),
    "unverified": ("alice@example.gov.uk", False),
}


def subject_for_email(email: str) -> str:
    digest = hashlib.sha256(f"webcaf-one-login:{email.lower()}".encode()).digest()
    identifier = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return f"urn:fdc:gov.uk:2022:{identifier}"


def configure_identity(email: str, email_verified: bool, simulator_url: str = SIMULATOR_URL) -> None:
    response = requests.post(
        f"{simulator_url}/config",
        json={
            "responseConfiguration": {
                "sub": subject_for_email(email),
                "email": email,
                "emailVerified": email_verified,
            },
            "errorConfiguration": {},
        },
        timeout=10,
    )
    response.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description="Select the identity returned by the local One Login simulator")
    parser.add_argument("preset", choices=USER_PRESETS)
    parser.add_argument("--simulator-url", default=SIMULATOR_URL)
    args = parser.parse_args()

    email, email_verified = USER_PRESETS[args.preset]
    configure_identity(email, email_verified, args.simulator_url)
    print(f"Configured One Login simulator preset: {args.preset}")


if __name__ == "__main__":
    main()
