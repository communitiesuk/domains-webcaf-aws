"""Validated LG assignment writes, separate from legacy application permissions."""

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from webcaf.webcaf.models import Organisation, UserProfile, UserProfileRole, UserRole
from webcaf.webcaf.utils.role_combinations import validate_role_combination

ROLE_GROUP_BY_ORGANISATION_TYPE: dict[str, str] = {
    "council": UserRole.Group.COUNCIL,
    "mhclg": UserRole.Group.MHCLG,
    "independent-assurer": UserRole.Group.ASSURER,
}


def validate_role_eligibility(organisation_type: str | None, role_group: str) -> None:
    expected_group = ROLE_GROUP_BY_ORGANISATION_TYPE.get(organisation_type or "")
    if expected_group is None or expected_group != role_group:
        raise ValidationError("This role is not eligible for the profile's organisation type.", code="ineligible_role")


def validate_organisation_role_groups(organisation_id: int, organisation_type: str | None) -> None:
    assignments = UserProfileRole.objects.filter(organisation_id=organisation_id)
    expected_group = ROLE_GROUP_BY_ORGANISATION_TYPE.get(organisation_type or "")
    if expected_group is not None:
        assignments = assignments.exclude(user_role__group=expected_group)
    if assignments.exists():
        raise ValidationError("Remove incompatible role assignments before changing the organisation type.")


def _require_permission(actor: User, permission: str) -> None:
    if not actor or not actor.pk or not actor.is_active or not actor.has_perm(permission):
        raise PermissionDenied("You do not have permission to manage these role assignments.")


def _locked_profile(profile_id: int) -> UserProfile:
    try:
        organisation_id = UserProfile.objects.values_list("organisation_id", flat=True).get(pk=profile_id)
        # Organisation then profile is the common locking order, including type edits.
        organisation = Organisation.objects.select_for_update().get(pk=organisation_id)
        profile = UserProfile.objects.select_for_update().get(pk=profile_id, organisation_id=organisation_id)
    except (UserProfile.DoesNotExist, Organisation.DoesNotExist) as exc:
        raise ValidationError("The profile changed. Reload it before granting or removing roles.") from exc
    profile.organisation = organisation
    return profile


@transaction.atomic
def validate_grant(user_profile_id: int, user_role_id: int, actor: User) -> tuple[UserProfile, UserRole]:
    """Validate under locks; grant_role keeps these locks through persistence."""
    _require_permission(actor, "webcaf.add_userprofilerole")
    profile = _locked_profile(user_profile_id)
    try:
        role = UserRole.objects.get(pk=user_role_id)
    except UserRole.DoesNotExist as exc:
        raise ValidationError("The role no longer exists. Reload the catalogue before granting it.") from exc
    assignments = list(profile.role_assignments.select_related("user_role"))
    if any(assignment.user_role_id == role.pk for assignment in assignments):
        raise ValidationError("This role is already assigned to the profile.", code="duplicate_role")
    # Check the resulting set, including any assignments created outside this API.
    for assigned_role in [*(assignment.user_role for assignment in assignments), role]:
        validate_role_eligibility(profile.organisation.organisation_type, assigned_role.group)
    validate_role_combination([*(assignment.user_role.code for assignment in assignments), role.code])
    return profile, role


@transaction.atomic
def grant_role(user_profile_id: int, user_role_id: int, actor: User) -> UserProfileRole:
    profile, role = validate_grant(user_profile_id, user_role_id, actor)
    assignment = UserProfileRole(
        user_profile=profile, user_role=role, organisation=profile.organisation, granted_by=actor
    )
    assignment._history_user = actor
    assignment.save()
    return assignment


@transaction.atomic
def revoke_role(assignment_id: int, actor: User) -> None:
    _require_permission(actor, "webcaf.delete_userprofilerole")
    try:
        profile_id = UserProfileRole.objects.values_list("user_profile_id", flat=True).get(pk=assignment_id)
        profile = _locked_profile(profile_id)
        assignment = UserProfileRole.objects.get(pk=assignment_id, user_profile=profile)
    except UserProfileRole.DoesNotExist as exc:
        raise ValidationError("This role assignment no longer exists.") from exc
    assignment._history_user = actor
    assignment.delete()
