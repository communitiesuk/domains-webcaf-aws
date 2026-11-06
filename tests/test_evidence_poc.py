import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from pypdf import PdfWriter

from webcaf.webcaf.evidence_poc import (
    EvidenceCredentialError,
    EvidencePromotionConflict,
    EvidenceValidationError,
    create_upload,
    presigned_upload,
    process_upload,
    promote,
    sanitise_display_filename,
    validate_pdf,
    verified_aws_session,
    verified_s3_client,
)
from webcaf.webcaf.models import Assessment, EvidencePocUpload, Organisation, System, UserProfile

POC_SETTINGS = {
    "EVIDENCE_POC_ENABLED": True,
    "EVIDENCE_POC_AWS_REGION": "eu-west-2",
    "EVIDENCE_POC_INTAKE_BUCKET": "intake-test",
    "EVIDENCE_POC_ACCEPTED_BUCKET": "accepted-test",
    "EVIDENCE_POC_KMS_KEY_ARN": "arn:aws:kms:eu-west-2:111122223333:key/test",
    "EVIDENCE_POC_APP_ROLE_ARN": "arn:aws:iam::111122223333:role/app-role",
    "EVIDENCE_POC_PROCESSOR_ROLE_ARN": "arn:aws:iam::111122223333:role/processor-role",
    "EVIDENCE_POC_APP_AWS_PROFILE": "app-profile",
    "EVIDENCE_POC_PROCESSOR_AWS_PROFILE": "processor-profile",
    "EVIDENCE_POC_UPLOAD_EXPIRY_SECONDS": 600,
    "EVIDENCE_POC_DOWNLOAD_EXPIRY_SECONDS": 60,
    "EVIDENCE_POC_MAX_BYTES": 26_214_400,
}

POC_REQUIRED_ENVIRONMENT = [
    "EVIDENCE_POC_AWS_REGION",
    "EVIDENCE_POC_INTAKE_BUCKET",
    "EVIDENCE_POC_ACCEPTED_BUCKET",
    "EVIDENCE_POC_KMS_KEY_ARN",
    "EVIDENCE_POC_APP_ROLE_ARN",
    "EVIDENCE_POC_PROCESSOR_ROLE_ARN",
    "EVIDENCE_POC_APP_AWS_PROFILE",
    "EVIDENCE_POC_PROCESSOR_AWS_PROFILE",
]

POC_SETTINGS_PROBE = (
    """
import json
from django.conf import settings

print(json.dumps({name: getattr(settings, name) for name in %r}))
"""
    % POC_REQUIRED_ENVIRONMENT
)


def pdf_bytes(*, encrypted=False):
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    if encrypted:
        writer.encrypt("test-password")
    writer.write(output)
    return output.getvalue()


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **_kwargs):
        return self.pages


class FakeS3:
    def __init__(self, upload, data=None, versions=None, tags=None):
        self.upload = upload
        self.data = data if data is not None else pdf_bytes()
        self.versions = versions or []
        self.tags = tags or {}
        self.put_calls = []
        self.get_calls = []
        self.put_error = None
        self.existing_data = None
        self.existing_metadata = None

    def get_paginator(self, name):
        assert name == "list_object_versions"
        return FakePaginator([{"Versions": self.versions}])

    def get_object_tagging(self, **kwargs):
        status = self.tags.get(kwargs["VersionId"], "")
        return {"TagSet": [{"Key": "GuardDutyMalwareScanStatus", "Value": status}] if status else []}

    def get_object(self, **kwargs):
        self.get_calls.append(kwargs)
        if kwargs["Bucket"] == "accepted-test":
            return {
                "Body": BytesIO(self.existing_data),
                "Metadata": self.existing_metadata,
                "VersionId": "accepted-existing-version",
            }
        return {
            "Body": BytesIO(self.data),
            "ContentLength": len(self.data),
            "ContentType": "application/pdf",
            "ETag": '"source-etag"',
            "VersionId": kwargs["VersionId"],
        }

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        if self.put_error:
            raise self.put_error
        return {"VersionId": "accepted-version"}


@override_settings(**POC_SETTINGS)
class EvidencePocAdminTests(TestCase):
    def setUp(self):
        self.org_a = Organisation.objects.create(name="Council A")
        self.org_b = Organisation.objects.create(name="Council B")
        self.system_a = System.objects.create(name="System A", organisation=self.org_a)
        self.system_b = System.objects.create(name="System B", organisation=self.org_b)
        self.assessment_a = Assessment.objects.create(system=self.system_a, assessment_period="2026-a", status="draft")
        self.assessment_b = Assessment.objects.create(system=self.system_b, assessment_period="2026-b", status="draft")
        self.user_a = User.objects.create_user("staff-a", is_staff=True)
        self.user_b = User.objects.create_user("staff-b", is_staff=True)
        self.profile_a = UserProfile.objects.create(user=self.user_a, organisation=self.org_a, role="organisation_lead")
        self.profile_b = UserProfile.objects.create(user=self.user_b, organisation=self.org_b, role="organisation_lead")
        self.upload_a = create_upload(
            self.profile_a, self.assessment_a, self.user_a, "Council A evidence.pdf", "application/pdf"
        )
        self.upload_b = create_upload(
            self.profile_b, self.assessment_b, self.user_b, "Council B evidence.pdf", "application/pdf"
        )

    def login_with_profile(self, user, profile):
        self.client.force_login(user)
        session = self.client.session
        session["current_profile_id"] = profile.id
        session.save()

    def test_list_is_scoped_to_active_council(self):
        self.login_with_profile(self.user_a, self.profile_a)
        response = self.client.get(reverse("admin:assessment_evidence_poc"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Council-A-evidence.pdf")
        self.assertNotContains(response, "Council-B-evidence.pdf")
        self.assertContains(response, "System A")
        self.assertNotContains(response, "System B")

    @patch("webcaf.webcaf.admin.presigned_upload")
    def test_initiation_rejects_changed_assessment_identifier(self, signed_upload):
        self.login_with_profile(self.user_a, self.profile_a)
        response = self.client.post(
            reverse("admin:assessment_evidence_poc_initiate"),
            {
                "assessment_id": self.assessment_b.id,
                "display_filename": "evidence.pdf",
                "declared_size": "100",
                "content_type": "application/pdf",
                "enhanced": "1",
            },
        )
        self.assertEqual(response.status_code, 403)
        signed_upload.assert_not_called()

    def test_cross_council_status_callback_and_download_are_not_found(self):
        self.login_with_profile(self.user_a, self.profile_a)
        for name, method in [
            ("admin:assessment_evidence_poc_status", self.client.get),
            ("admin:assessment_evidence_poc_received", self.client.post),
            ("admin:assessment_evidence_poc_return", self.client.get),
            ("admin:assessment_evidence_poc_download", self.client.get),
        ]:
            with self.subTest(name=name):
                response = method(reverse(name, args=[self.upload_b.id]))
                self.assertEqual(response.status_code, 404)

    def test_profile_switch_immediately_changes_scope(self):
        second_profile = UserProfile.objects.create(user=self.user_a, organisation=self.org_b, role="organisation_lead")
        self.login_with_profile(self.user_a, self.profile_a)
        self.assertEqual(
            self.client.get(reverse("admin:assessment_evidence_poc_status", args=[self.upload_a.id])).status_code,
            200,
        )
        session = self.client.session
        session["current_profile_id"] = second_profile.id
        session.save()
        self.assertEqual(
            self.client.get(reverse("admin:assessment_evidence_poc_status", args=[self.upload_a.id])).status_code,
            404,
        )

    def test_received_callback_replay_is_idempotent(self):
        self.login_with_profile(self.user_a, self.profile_a)
        url = reverse("admin:assessment_evidence_poc_received", args=[self.upload_a.id])
        self.assertEqual(self.client.post(url).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 200)
        self.upload_a.refresh_from_db()
        self.assertEqual(self.upload_a.state, EvidencePocUpload.State.RECEIVED)

    def test_missing_wrong_role_and_null_organisation_profiles_are_rejected(self):
        cases = [
            User.objects.create_user("missing", is_staff=True),
            User.objects.create_user("reviewer", is_staff=True),
            User.objects.create_user("no-org", is_staff=True),
        ]
        UserProfile.objects.create(user=cases[1], organisation=self.org_a, role="reviewer")
        UserProfile.objects.create(user=cases[2], organisation=None, role="organisation_lead")
        for user in cases:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                profiles = list(user.profiles.all())
                if profiles:
                    session = self.client.session
                    session["current_profile_id"] = profiles[0].id
                    session.save()
                response = self.client.get(reverse("admin:assessment_evidence_poc"))
                self.assertEqual(response.status_code, 403)

    @patch("webcaf.webcaf.admin.presigned_upload")
    def test_submitted_assessment_cannot_be_used_for_upload(self, signed_upload):
        self.assessment_a.status = "submitted"
        self.assessment_a.save(update_fields=["status"])
        self.login_with_profile(self.user_a, self.profile_a)
        response = self.client.post(
            reverse("admin:assessment_evidence_poc_initiate"),
            {"assessment_id": self.assessment_a.id, "display_filename": "evidence.pdf"},
        )
        self.assertEqual(response.status_code, 403)
        signed_upload.assert_not_called()

    def test_superuser_does_not_bypass_profile_ownership(self):
        superuser = User.objects.create_superuser("root")
        profile = UserProfile.objects.create(user=superuser, organisation=self.org_b, role="organisation_lead")
        self.login_with_profile(superuser, profile)
        response = self.client.get(reverse("admin:assessment_evidence_poc_status", args=[self.upload_a.id]))
        self.assertEqual(response.status_code, 404)

    @override_settings(EVIDENCE_POC_ENABLED=False)
    def test_feature_disabled_denies_direct_access_and_hides_entry_point(self):
        self.login_with_profile(self.user_a, self.profile_a)
        response = self.client.get(reverse("admin:assessment_evidence_poc"))
        self.assertEqual(response.status_code, 403)
        superuser = User.objects.create_superuser("feature-check")
        UserProfile.objects.create(user=superuser, organisation=self.org_a, role="organisation_lead")
        self.client.force_login(superuser)
        assessment_list = self.client.get(reverse("admin:webcaf_assessment_changelist"))
        self.assertNotContains(assessment_list, "Evidence upload POC")

    @patch("webcaf.webcaf.admin.verified_s3_client")
    def test_download_is_version_pinned_with_safe_headers(self, s3_client):
        self.upload_a.state = EvidencePocUpload.State.AVAILABLE
        self.upload_a.accepted_version_id = "accepted-v1"
        self.upload_a.save(update_fields=["state", "accepted_version_id"])
        client = s3_client.return_value
        client.generate_presigned_url.return_value = "https://accepted.example/signed"
        self.login_with_profile(self.user_a, self.profile_a)
        response = self.client.get(reverse("admin:assessment_evidence_poc_download", args=[self.upload_a.id]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("no-store", response["Cache-Control"])
        params = client.generate_presigned_url.call_args.kwargs["Params"]
        self.assertEqual(params["VersionId"], "accepted-v1")
        self.assertEqual(params["ResponseContentType"], "application/octet-stream")
        self.assertNotIn("intake", response["Location"])


class EvidencePocValidationTests(SimpleTestCase):
    def upload(self, filename="report.pdf", content_type="application/pdf", size=None):
        return Mock(display_filename=filename, declared_content_type=content_type, size=size)

    def test_safe_filename_removes_paths_controls_and_misleading_unicode(self):
        self.assertEqual(sanitise_display_filename("../folder\\Quarterly\u202ereport.PDF"), "Quarterlyreport.pdf")
        self.assertEqual(sanitise_display_filename("\x00 .. "), "evidence.pdf")

    @override_settings(EVIDENCE_POC_MAX_BYTES=2_000)
    def test_pdf_size_boundaries_and_mismatches(self):
        data = pdf_bytes()
        validate_pdf(data, self.upload(size=len(data)))
        with self.assertRaisesRegex(EvidenceValidationError, "invalid_size"):
            validate_pdf(b"", self.upload(size=0))
        with self.assertRaisesRegex(EvidenceValidationError, "invalid_size"):
            validate_pdf(b"x" * 2_001, self.upload(size=2_001))
        with self.assertRaisesRegex(EvidenceValidationError, "size_mismatch"):
            validate_pdf(data, self.upload(size=len(data) + 1))

    def test_mismatched_malformed_and_encrypted_pdfs_are_rejected(self):
        cases = [
            (pdf_bytes(), self.upload(filename="report.txt", size=len(pdf_bytes())), "extension_mismatch"),
            (b"not a pdf", self.upload(size=9), "signature_mismatch"),
            (b"%PDF-malformed", self.upload(size=14), "malformed_pdf"),
            (pdf_bytes(encrypted=True), self.upload(size=len(pdf_bytes(encrypted=True))), "encrypted_pdf"),
        ]
        for data, upload, reason in cases:
            upload.size = len(data)
            with self.subTest(reason=reason), self.assertRaisesRegex(EvidenceValidationError, reason):
                validate_pdf(data, upload)

    def test_declared_mime_is_only_a_hint_when_bytes_are_a_valid_pdf(self):
        data = pdf_bytes()
        validate_pdf(data, self.upload(content_type="application/octet-stream", size=len(data)))


@override_settings(**POC_SETTINGS)
class EvidencePocProcessorTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Processor Council")
        self.system = System.objects.create(name="Processor System", organisation=self.org)
        self.assessment = Assessment.objects.create(system=self.system, assessment_period="2026", status="draft")
        self.user = User.objects.create_user("processor-uploader", is_staff=True)
        self.profile = UserProfile.objects.create(user=self.user, organisation=self.org, role="organisation_lead")
        self.upload = create_upload(self.profile, self.assessment, self.user, "synthetic.pdf", "application/pdf")
        self.data = pdf_bytes()
        self.now = datetime.now(timezone.utc)

    def version(self, version_id="source-v1", when=None, key=None, size=None):
        return {
            "Key": key or self.upload.intake_key,
            "VersionId": version_id,
            "LastModified": when or self.now,
            "Size": len(self.data) if size is None else size,
            "ETag": '"source-etag"',
        }

    def test_pending_and_unsafe_scan_results_fail_closed(self):
        cases = [
            ("", EvidencePocUpload.State.SCANNING, ""),
            ("THREATS_FOUND", EvidencePocUpload.State.REJECTED, "security_scan_rejected"),
            ("UNSUPPORTED", EvidencePocUpload.State.REJECTED, "scan_unsupported"),
            ("FAILED", EvidencePocUpload.State.FAILED, "scan_failed"),
            ("ACCESS_DENIED", EvidencePocUpload.State.FAILED, "scan_failed"),
        ]
        for index, (tag, state, reason) in enumerate(cases):
            upload = create_upload(self.profile, self.assessment, self.user, f"case-{index}.pdf", "application/pdf")
            s3 = FakeS3(upload, self.data, [self.version(key=upload.intake_key)], {"source-v1": tag})
            with self.subTest(tag=tag):
                self.assertEqual(process_upload(s3, upload.id), state)
                upload.refresh_from_db()
                self.assertEqual(upload.failure_reason, reason)
                self.assertFalse(s3.get_calls)
                self.assertFalse(s3.put_calls)

    def test_clean_tag_on_replayed_version_cannot_approve_bound_first_version(self):
        versions = [
            self.version("replay-clean", when=self.now + timedelta(seconds=1)),
            self.version("first-pending", when=self.now),
            self.version("prefix-neighbour", key=f"{self.upload.intake_key}-other"),
        ]
        s3 = FakeS3(self.upload, self.data, versions, {"replay-clean": "NO_THREATS_FOUND"})
        self.assertEqual(process_upload(s3, self.upload.id), EvidencePocUpload.State.SCANNING)
        self.upload.refresh_from_db()
        self.assertEqual(self.upload.source_version_id, "first-pending")
        self.assertFalse(s3.get_calls)

    def test_clean_valid_file_promotes_exact_version_and_repeated_run_is_safe(self):
        s3 = FakeS3(
            self.upload,
            self.data,
            [self.version()],
            {"source-v1": "NO_THREATS_FOUND"},
        )
        self.assertEqual(process_upload(s3, self.upload.id), EvidencePocUpload.State.AVAILABLE)
        self.upload.refresh_from_db()
        self.assertEqual(self.upload.accepted_version_id, "accepted-version")
        self.assertEqual(self.upload.sha256, hashlib.sha256(self.data).hexdigest())
        self.assertEqual(s3.get_calls[0]["VersionId"], "source-v1")
        put = s3.put_calls[0]
        self.assertEqual(put["IfNoneMatch"], "*")
        self.assertEqual(put["ServerSideEncryption"], "aws:kms")
        self.assertEqual(put["ChecksumAlgorithm"], "SHA256")
        self.assertEqual(process_upload(s3, self.upload.id), EvidencePocUpload.State.AVAILABLE)
        self.assertEqual(len(s3.put_calls), 1)

    def test_permission_change_during_processing_prevents_availability(self):
        self.assessment.status = "submitted"
        self.assessment.save(update_fields=["status"])
        s3 = FakeS3(self.upload, self.data, [self.version()], {"source-v1": "NO_THREATS_FOUND"})
        self.assertEqual(process_upload(s3, self.upload.id), EvidencePocUpload.State.REJECTED)
        self.upload.refresh_from_db()
        self.assertEqual(self.upload.failure_reason, "association_no_longer_authorised")

    def test_promotion_recovers_after_destination_write(self):
        self.upload.source_version_id = "source-v1"
        digest = hashlib.sha256(self.data).hexdigest()
        conflict = ClientError(
            {"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}},
            "PutObject",
        )
        s3 = FakeS3(self.upload, self.data)
        s3.put_error = conflict
        s3.existing_data = self.data
        s3.existing_metadata = {
            "evidence-id": str(self.upload.id),
            "source-version": "source-v1",
            "sha256": digest,
        }
        self.assertEqual(promote(s3, self.upload, self.data, digest), "accepted-existing-version")

    def test_promotion_conflict_with_other_bytes_fails_safely(self):
        self.upload.source_version_id = "source-v1"
        digest = hashlib.sha256(self.data).hexdigest()
        s3 = FakeS3(self.upload, self.data)
        s3.put_error = ClientError(
            {"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}},
            "PutObject",
        )
        s3.existing_data = b"different"
        s3.existing_metadata = {"evidence-id": str(self.upload.id), "source-version": "source-v1"}
        with self.assertRaises(EvidencePromotionConflict):
            promote(s3, self.upload, self.data, digest)


@override_settings(**POC_SETTINGS)
class EvidencePocCredentialTests(SimpleTestCase):
    @patch("webcaf.webcaf.evidence_poc.verified_aws_session")
    def test_s3_client_uses_exact_regional_endpoint(self, aws_session):
        verified_s3_client("app")
        call = aws_session.return_value.client.call_args
        self.assertEqual(call.args, ("s3",))
        self.assertEqual(call.kwargs["endpoint_url"], "https://s3.eu-west-2.amazonaws.com")
        self.assertEqual(call.kwargs["config"].region_name, "eu-west-2")
        self.assertEqual(call.kwargs["config"].s3["addressing_style"], "virtual")

    @patch("webcaf.webcaf.evidence_poc.verified_s3_client")
    def test_presigned_post_fixes_key_size_encryption_and_success_status(self, s3_client):
        upload = Mock(intake_key="intake/random-server-key")
        presigned_upload(upload, {"success_action_status": "204"})
        call = s3_client.return_value.generate_presigned_post.call_args
        self.assertEqual(call.kwargs["Key"], "intake/random-server-key")
        self.assertEqual(call.kwargs["ExpiresIn"], 600)
        self.assertIn(["content-length-range", 1, 26_214_400], call.kwargs["Conditions"])
        self.assertEqual(call.kwargs["Fields"]["x-amz-server-side-encryption"], "aws:kms")
        self.assertEqual(
            call.kwargs["Fields"]["x-amz-server-side-encryption-aws-kms-key-id"],
            POC_SETTINGS["EVIDENCE_POC_KMS_KEY_ARN"],
        )
        self.assertEqual(call.kwargs["Fields"]["success_action_status"], "204")

    @patch("boto3.Session")
    def test_profile_failure_does_not_fall_back(self, session):
        session.side_effect = RuntimeError("profile unavailable")
        with self.assertRaisesRegex(EvidenceCredentialError, "configured app role"):
            verified_aws_session("app")
        session.assert_called_once_with(profile_name="app-profile", region_name="eu-west-2")

    @patch("boto3.Session")
    def test_wrong_caller_identity_is_rejected(self, session):
        sts = session.return_value.client.return_value
        sts.get_caller_identity.return_value = {
            "Account": "111122223333",
            "Arn": "arn:aws:sts::111122223333:assumed-role/developer/session",
        }
        with self.assertRaisesRegex(EvidenceCredentialError, "not the configured processor role"):
            verified_aws_session("processor")


class EvidencePocConfigurationTests(SimpleTestCase):
    @staticmethod
    def load_settings(*, enabled, values=None):
        environment = os.environ.copy()
        environment.update(
            {
                "DEBUG": "True",
                "DJANGO_SETTINGS_MODULE": "webcaf.settings",
                "SECRET_KEY": "unneeded",  # pragma: allowlist secret
                "SSO_MODE": "none",
                "EVIDENCE_POC_ENABLED": str(enabled),
                **{name: "" for name in POC_REQUIRED_ENVIRONMENT},
                **(values or {}),
            }
        )
        return subprocess.run(
            [sys.executable, "-c", POC_SETTINGS_PROBE],
            capture_output=True,
            env=environment,
            text=True,
        )

    def test_disabled_feature_has_no_live_resource_defaults(self):
        result = self.load_settings(enabled=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {name: "" for name in POC_REQUIRED_ENVIRONMENT})

    def test_enabled_feature_requires_every_environment_value(self):
        result = self.load_settings(enabled=True)
        self.assertNotEqual(result.returncode, 0)
        for name in POC_REQUIRED_ENVIRONMENT:
            with self.subTest(name=name):
                self.assertIn(name, result.stderr)

    def test_enabled_feature_accepts_complete_explicit_configuration(self):
        values = {
            "EVIDENCE_POC_AWS_REGION": "test-region-1",
            "EVIDENCE_POC_INTAKE_BUCKET": "test-intake",
            "EVIDENCE_POC_ACCEPTED_BUCKET": "test-accepted",
            "EVIDENCE_POC_KMS_KEY_ARN": "arn:aws:kms:test-region-1:111122223333:key/test",
            "EVIDENCE_POC_APP_ROLE_ARN": "arn:aws:iam::111122223333:role/test-app",
            "EVIDENCE_POC_PROCESSOR_ROLE_ARN": "arn:aws:iam::111122223333:role/test-processor",
            "EVIDENCE_POC_APP_AWS_PROFILE": "test-app-profile",
            "EVIDENCE_POC_PROCESSOR_AWS_PROFILE": "test-processor-profile",
        }
        result = self.load_settings(enabled=True, values=values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), values)
