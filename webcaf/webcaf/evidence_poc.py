import base64
import hashlib
import io
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from webcaf.webcaf.models import Assessment, EvidencePocUpload, System, UserProfile
from webcaf.webcaf.utils.session import SessionUtil

ALLOWED_UPLOAD_ROLES = {"organisation_lead", "organisation_user"}
GUARDDUTY_TAG = "GuardDutyMalwareScanStatus"
FINAL_STATES = {EvidencePocUpload.State.AVAILABLE, EvidencePocUpload.State.REJECTED}
_BIDI_CONTROLS = re.compile("[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")


class EvidencePocError(Exception):
    pass


class EvidenceCredentialError(EvidencePocError):
    pass


class EvidenceValidationError(EvidencePocError):
    pass


class EvidencePromotionConflict(EvidencePocError):
    pass


@dataclass(frozen=True)
class BoundVersion:
    version_id: str
    etag: str
    size: int


def require_feature() -> None:
    if not settings.EVIDENCE_POC_ENABLED:
        raise PermissionDenied("The evidence proof of concept is disabled")


def current_upload_profile(request) -> UserProfile:
    profile = SessionUtil.get_current_user_profile(request)
    if (
        profile is None
        or profile.organisation_id is None
        or profile.role not in ALLOWED_UPLOAD_ROLES
        or profile.user_id != request.user.id
    ):
        raise PermissionDenied("A council lead or council user profile is required")
    return profile


def editable_assessment(profile: UserProfile, assessment_id) -> Assessment:
    organisation_id = profile.organisation_id
    if organisation_id is None:
        raise PermissionDenied("The active profile has no council")
    try:
        return Assessment.objects.select_related("system__organisation").get(
            id=assessment_id,
            status="draft",
            system__organisation_id=organisation_id,
        )
    except (TypeError, ValueError, Assessment.DoesNotExist) as exc:
        raise PermissionDenied("The selected assessment is not editable by this profile") from exc


def sanitise_display_filename(filename: str) -> str:
    from slugify import slugify

    filename = unicodedata.normalize("NFKC", filename or "").replace("\\", "/")
    filename = PurePath(filename).name
    filename = _BIDI_CONTROLS.sub("", filename)
    filename = "".join(character for character in filename if unicodedata.category(character) != "Cc")
    filename = " ".join(filename.split()).strip(" .")
    if not filename:
        filename = "evidence.pdf"
    stem, separator, suffix = filename.rpartition(".")
    if not separator:
        stem, suffix = filename, ""
    safe_stem = slugify(stem, lowercase=False, max_length=220) or "evidence"
    safe_suffix = slugify(suffix, lowercase=True, max_length=10)
    return f"{safe_stem}.{safe_suffix}" if safe_suffix else safe_stem


def safe_pdf_download_filename(filename: str) -> str:
    from slugify import slugify

    stem = filename.rsplit(".", 1)[0]
    return f"{slugify(stem, lowercase=False, max_length=220) or 'evidence'}.pdf"


def _role_matches(caller_arn: str, expected_role_arn: str, account: str) -> bool:
    expected_account = expected_role_arn.split(":", 5)[4]
    expected_role_name = expected_role_arn.rsplit("/", 1)[-1]
    return account == expected_account and (
        caller_arn == expected_role_arn or f":assumed-role/{expected_role_name}/" in caller_arn
    )


def verified_aws_session(kind: str):
    import boto3

    if kind == "app":
        profile_name = settings.EVIDENCE_POC_APP_AWS_PROFILE
        expected_role_arn = settings.EVIDENCE_POC_APP_ROLE_ARN
    elif kind == "processor":
        profile_name = settings.EVIDENCE_POC_PROCESSOR_AWS_PROFILE
        expected_role_arn = settings.EVIDENCE_POC_PROCESSOR_ROLE_ARN
    else:
        raise ValueError("Unknown evidence AWS identity")

    try:
        session = boto3.Session(profile_name=profile_name or None, region_name=settings.EVIDENCE_POC_AWS_REGION)
        identity = session.client("sts").get_caller_identity()
    except Exception as exc:
        raise EvidenceCredentialError(f"Unable to obtain the configured {kind} role credentials") from exc

    if not _role_matches(identity.get("Arn", ""), expected_role_arn, identity.get("Account", "")):
        raise EvidenceCredentialError(f"AWS credentials are not the configured {kind} role")
    return session


def verified_s3_client(kind: str):
    from botocore.config import Config

    region = settings.EVIDENCE_POC_AWS_REGION
    return verified_aws_session(kind).client(
        "s3",
        endpoint_url=f"https://s3.{region}.amazonaws.com",
        config=Config(region_name=region, signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def create_upload(profile: UserProfile, assessment: Assessment, user, display_filename: str, content_type: str):
    organisation_id = profile.organisation_id
    if organisation_id is None:
        raise PermissionDenied("The active profile has no council")
    upload_id = uuid.uuid4()
    return EvidencePocUpload.objects.create(
        id=upload_id,
        organisation_id=organisation_id,
        assessment=assessment,
        uploaded_by=user,
        uploaded_with_profile=profile,
        intake_key=f"intake/{uuid.uuid4()}",
        accepted_key=f"evidence/{uuid.uuid4()}",
        display_filename=sanitise_display_filename(display_filename),
        declared_content_type=content_type[:255],
    )


def presigned_upload(upload: EvidencePocUpload, success_action: dict[str, str]):
    fields = {
        "Content-Type": "application/pdf",
        "x-amz-server-side-encryption": "aws:kms",
        "x-amz-server-side-encryption-aws-kms-key-id": settings.EVIDENCE_POC_KMS_KEY_ARN,
        **success_action,
    }
    conditions = [
        {"Content-Type": "application/pdf"},
        {"x-amz-server-side-encryption": "aws:kms"},
        {"x-amz-server-side-encryption-aws-kms-key-id": settings.EVIDENCE_POC_KMS_KEY_ARN},
        *({key: value} for key, value in success_action.items()),
        ["content-length-range", 1, settings.EVIDENCE_POC_MAX_BYTES],
    ]
    return verified_s3_client("app").generate_presigned_post(
        Bucket=settings.EVIDENCE_POC_INTAKE_BUCKET,
        Key=upload.intake_key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=settings.EVIDENCE_POC_UPLOAD_EXPIRY_SECONDS,
    )


def _first_exact_version(s3, upload: EvidencePocUpload) -> BoundVersion | None:
    versions: list[dict[str, Any]] = []
    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=settings.EVIDENCE_POC_INTAKE_BUCKET, Prefix=upload.intake_key):
        versions.extend(version for version in page.get("Versions", []) if version.get("Key") == upload.intake_key)
    if not versions:
        return None
    # S3 returns versions for one key newest first. Prefer the later list item
    # when timestamps tie so a replay cannot displace the first upload.
    _, first = min(enumerate(versions), key=lambda pair: (pair[1]["LastModified"], -pair[0]))
    return BoundVersion(version_id=first["VersionId"], etag=first.get("ETag", ""), size=first["Size"])


def bind_source_version(s3, upload_id) -> EvidencePocUpload | None:
    with transaction.atomic():
        upload = EvidencePocUpload.objects.select_for_update().get(id=upload_id)
        if upload.source_version_id:
            return upload
        version = _first_exact_version(s3, upload)
        if version is None:
            return None
        upload.source_version_id = version.version_id
        upload.source_etag = version.etag
        upload.size = version.size
        upload.state = EvidencePocUpload.State.SCANNING
        upload.save(update_fields=["source_version_id", "source_etag", "size", "state", "updated_at"])
        return upload


def _scan_status(s3, upload: EvidencePocUpload) -> str:
    response = s3.get_object_tagging(
        Bucket=settings.EVIDENCE_POC_INTAKE_BUCKET,
        Key=upload.intake_key,
        VersionId=upload.source_version_id,
    )
    return next((tag["Value"] for tag in response.get("TagSet", []) if tag["Key"] == GUARDDUTY_TAG), "")


def validate_pdf(data: bytes, upload: EvidencePocUpload) -> str:
    from pypdf import PdfReader

    if not data or len(data) > settings.EVIDENCE_POC_MAX_BYTES:
        raise EvidenceValidationError("invalid_size")
    if upload.size is not None and len(data) != upload.size:
        raise EvidenceValidationError("size_mismatch")
    if not upload.display_filename.lower().endswith(".pdf"):
        raise EvidenceValidationError("extension_mismatch")
    if not data.startswith(b"%PDF-"):
        raise EvidenceValidationError("signature_mismatch")
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise EvidenceValidationError("encrypted_pdf")
        if reader.trailer.get("/Root") is None:
            raise EvidenceValidationError("malformed_pdf")
        len(reader.pages)
    except EvidenceValidationError:
        raise
    except Exception as exc:
        raise EvidenceValidationError("malformed_pdf") from exc
    return hashlib.sha256(data).hexdigest()


def _read_bound_source(s3, upload: EvidencePocUpload) -> bytes:
    response = s3.get_object(
        Bucket=settings.EVIDENCE_POC_INTAKE_BUCKET,
        Key=upload.intake_key,
        VersionId=upload.source_version_id,
    )
    if response.get("VersionId") != upload.source_version_id:
        raise EvidenceValidationError("source_version_mismatch")
    if response.get("ContentType") != "application/pdf":
        raise EvidenceValidationError("content_type_mismatch")
    if upload.source_etag and response.get("ETag") != upload.source_etag:
        raise EvidenceValidationError("source_identity_mismatch")
    data = response["Body"].read(settings.EVIDENCE_POC_MAX_BYTES + 1)
    if response.get("ContentLength") != len(data):
        raise EvidenceValidationError("size_mismatch")
    return data


def _verify_existing_destination(s3, upload: EvidencePocUpload, expected_digest: str) -> str:
    response = s3.get_object(Bucket=settings.EVIDENCE_POC_ACCEPTED_BUCKET, Key=upload.accepted_key)
    data = response["Body"].read(settings.EVIDENCE_POC_MAX_BYTES + 1)
    metadata = response.get("Metadata", {})
    if (
        hashlib.sha256(data).hexdigest() != expected_digest
        or metadata.get("evidence-id") != str(upload.id)
        or metadata.get("source-version") != upload.source_version_id
    ):
        raise EvidencePromotionConflict("The accepted destination is occupied by different bytes")
    if not response.get("VersionId"):
        raise EvidencePromotionConflict("The accepted object has no version identifier")
    return response["VersionId"]


def promote(s3, upload: EvidencePocUpload, data: bytes, digest: str) -> str:
    from botocore.exceptions import ClientError

    try:
        response = s3.put_object(
            Bucket=settings.EVIDENCE_POC_ACCEPTED_BUCKET,
            Key=upload.accepted_key,
            Body=data,
            ContentLength=len(data),
            ContentType="application/pdf",
            Metadata={
                "evidence-id": str(upload.id),
                "source-version": upload.source_version_id,
                "sha256": digest,
            },
            IfNoneMatch="*",
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=settings.EVIDENCE_POC_KMS_KEY_ARN,
            ChecksumAlgorithm="SHA256",
            ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode("ascii"),
        )
    except ClientError as exc:
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
            raise
        return _verify_existing_destination(s3, upload, digest)
    if not response.get("VersionId"):
        raise EvidencePocError("S3 did not return an accepted object version")
    return response["VersionId"]


def _still_authorised(upload: EvidencePocUpload) -> bool:
    return (
        upload.assessment.status == "draft"
        and upload.assessment.system.organisation_id == upload.organisation_id
        and upload.uploaded_by is not None
        and upload.uploaded_by.is_staff
        and upload.uploaded_with_profile is not None
        and upload.uploaded_with_profile.user_id == upload.uploaded_by_id
        and upload.uploaded_with_profile.organisation_id == upload.organisation_id
        and upload.uploaded_with_profile.role in ALLOWED_UPLOAD_ROLES
    )


def _set_outcome(upload_id, state: str, *, reason: str = "", scan_status: str = "") -> None:
    EvidencePocUpload.objects.filter(id=upload_id).exclude(state__in=FINAL_STATES).update(
        state=state,
        failure_reason=reason,
        scan_status=scan_status,
        updated_at=timezone.now(),
    )


def process_upload(s3, upload_id) -> str:
    upload = bind_source_version(s3, upload_id)
    if upload is None:
        return EvidencePocUpload.State.UPLOADING
    if upload.state in FINAL_STATES:
        return upload.state
    if not upload.size or upload.size > settings.EVIDENCE_POC_MAX_BYTES:
        _set_outcome(upload.id, EvidencePocUpload.State.REJECTED, reason="invalid_size")
        return EvidencePocUpload.State.REJECTED

    scan_status = _scan_status(s3, upload)
    if not scan_status:
        _set_outcome(upload.id, EvidencePocUpload.State.SCANNING)
        return EvidencePocUpload.State.SCANNING
    if scan_status == "THREATS_FOUND":
        _set_outcome(
            upload.id, EvidencePocUpload.State.REJECTED, reason="security_scan_rejected", scan_status=scan_status
        )
        return EvidencePocUpload.State.REJECTED
    if scan_status == "UNSUPPORTED":
        _set_outcome(upload.id, EvidencePocUpload.State.REJECTED, reason="scan_unsupported", scan_status=scan_status)
        return EvidencePocUpload.State.REJECTED
    if scan_status in {"ACCESS_DENIED", "FAILED"}:
        _set_outcome(upload.id, EvidencePocUpload.State.FAILED, reason="scan_failed", scan_status=scan_status)
        return EvidencePocUpload.State.FAILED
    if scan_status != "NO_THREATS_FOUND":
        _set_outcome(upload.id, EvidencePocUpload.State.SCANNING, scan_status=scan_status)
        return EvidencePocUpload.State.SCANNING

    _set_outcome(upload.id, EvidencePocUpload.State.VALIDATING, scan_status=scan_status)
    upload.refresh_from_db()
    try:
        data = _read_bound_source(s3, upload)
        digest = validate_pdf(data, upload)
    except EvidenceValidationError as exc:
        _set_outcome(upload.id, EvidencePocUpload.State.REJECTED, reason=str(exc), scan_status=scan_status)
        return EvidencePocUpload.State.REJECTED

    accepted_version = promote(s3, upload, data, digest)
    with transaction.atomic():
        locked = EvidencePocUpload.objects.select_for_update().get(id=upload.id)
        assessment = Assessment.objects.select_for_update().select_related("system").get(id=locked.assessment_id)
        # Lock the mutable authorisation records so the availability decision
        # describes one database state at commit time.
        assessment.system = System.objects.select_for_update().get(id=assessment.system_id)
        locked.assessment = assessment
        if locked.uploaded_by_id:
            locked.uploaded_by = User.objects.select_for_update().get(id=locked.uploaded_by_id)
        if locked.uploaded_with_profile_id:
            locked.uploaded_with_profile = UserProfile.objects.select_for_update().get(
                id=locked.uploaded_with_profile_id
            )
        if locked.state == EvidencePocUpload.State.AVAILABLE:
            return locked.state
        if locked.source_version_id != upload.source_version_id or not _still_authorised(locked):
            locked.state = EvidencePocUpload.State.REJECTED
            locked.failure_reason = "association_no_longer_authorised"
            locked.save(update_fields=["state", "failure_reason", "updated_at"])
            return locked.state
        locked.accepted_version_id = accepted_version
        locked.sha256 = digest
        locked.size = len(data)
        locked.state = EvidencePocUpload.State.AVAILABLE
        locked.failure_reason = ""
        locked.save(update_fields=["accepted_version_id", "sha256", "size", "state", "failure_reason", "updated_at"])
        return locked.state


def process_pending(*, retry_failures: bool = False) -> dict[str, int]:
    require_feature()
    s3 = verified_s3_client("processor")
    states = [
        EvidencePocUpload.State.UPLOADING,
        EvidencePocUpload.State.RECEIVED,
        EvidencePocUpload.State.SCANNING,
        EvidencePocUpload.State.VALIDATING,
    ]
    if retry_failures:
        states.append(EvidencePocUpload.State.FAILED)
    counts: dict[str, int] = {}
    for upload_id in EvidencePocUpload.objects.filter(state__in=states).values_list("id", flat=True):
        try:
            outcome = process_upload(s3, upload_id)
        except Exception:
            _set_outcome(upload_id, EvidencePocUpload.State.FAILED, reason="processing_error")
            outcome = EvidencePocUpload.State.FAILED
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def validate_initiation(display_filename: str, declared_size: str, content_type: str, enhanced: bool) -> None:
    if not display_filename:
        raise ValidationError("Enter a display filename.")
    if not sanitise_display_filename(display_filename).lower().endswith(".pdf"):
        raise ValidationError("Choose a PDF file.")
    if enhanced:
        try:
            size = int(declared_size)
        except (TypeError, ValueError) as exc:
            raise ValidationError("The selected file size is invalid.") from exc
        if not 1 <= size <= settings.EVIDENCE_POC_MAX_BYTES:
            raise ValidationError("The PDF must be between 1 byte and 25 MiB.")
