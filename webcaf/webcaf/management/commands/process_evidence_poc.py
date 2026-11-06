import json

from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from webcaf.webcaf.evidence_poc import EvidenceCredentialError, process_pending, require_feature, verified_aws_session


class Command(BaseCommand):
    help = "Process pending CS-627 evidence uploads once using the dedicated processor role"

    def add_arguments(self, parser):
        parser.add_argument(
            "--retry-failures",
            action="store_true",
            help="Retry records in the processing-failure state as well as pending records",
        )
        parser.add_argument(
            "--identity-only",
            action="store_true",
            help="Verify the configured processor identity without reading or writing evidence",
        )

    def handle(self, *args, **options):
        try:
            require_feature()
            identity = verified_aws_session("processor").client("sts").get_caller_identity()
            self.stdout.write(self.style.SUCCESS(f"Verified processor identity for AWS account {identity['Account']}"))
            if options["identity_only"]:
                return
            counts = process_pending(retry_failures=options["retry_failures"])
        except (EvidenceCredentialError, PermissionDenied) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Processing complete: {json.dumps(counts, sort_keys=True)}"))
