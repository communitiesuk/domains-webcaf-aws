"""AC5 combination policy used by the validated LG assignment service."""

from collections.abc import Iterable
from itertools import combinations

from django.core.exceptions import ValidationError

# Codes match the agreed AC2 catalogue, seeded separately by migration 0047.
# Application permission reads switch separately. Council Claimant is onboarding state.
SUPPORTED_ROLE_CODES = frozenset(
    {
        "caf_lead",
        "deputy_caf_lead",
        "collaborator",
        "systems_mapper",
        "approver",
        "quality_assurer",
        "mhclg_super_user",
        "mhclg_assurance_lead",
        "independent_assurer_lead",
        "independent_assurer",
        "independent_assurer_qa",
    }
)

# Only explicitly approved pairs are permitted, in either order. Conditional,
# undecided and "must remain separate" pairs have no exception or override yet.
APPROVED_ROLE_PAIRS = frozenset(
    {
        frozenset({"caf_lead", "collaborator"}),
        frozenset({"caf_lead", "systems_mapper"}),
        frozenset({"collaborator", "systems_mapper"}),
    }
)


def validate_role_combination(role_codes: Iterable[str]) -> None:
    """Validate the complete proposed role set for a single organisation profile.

    Return None on success or raise ValidationError. No data is read or written.
    Organisation eligibility, grant authority and duplicate assignment handling
    are separate concerns. Callers must validate and persist under
    the same profile lock; this function alone is not a concurrency-safe grant.
    """
    invalid_input_message = "Provide an iterable containing only role-code strings."
    if isinstance(role_codes, (str, bytes, bytearray)):
        raise ValidationError(invalid_input_message, code="invalid_role_codes")

    try:
        codes = tuple(role_codes)
    except TypeError as exc:
        raise ValidationError(invalid_input_message, code="invalid_role_codes") from exc
    if any(not isinstance(code, str) for code in codes):
        raise ValidationError(invalid_input_message, code="invalid_role_codes")

    roles = frozenset(codes)
    if not roles.issubset(SUPPORTED_ROLE_CODES):
        raise ValidationError("The role combination contains an unsupported role code.", code="unsupported_role_code")

    if "approver" in roles and len(roles) > 1:
        raise ValidationError("Approver must be the only role assigned to a profile.", code="exclusive_approver")

    # Validate every pair in the resulting set, not just the first existing role.
    # Sorting also makes the reported conflict independent of input/grant order.
    for first_role, second_role in combinations(sorted(roles), 2):
        if frozenset({first_role, second_role}) not in APPROVED_ROLE_PAIRS:
            raise ValidationError(
                "Roles %(first_role)s and %(second_role)s cannot be assigned to the same profile.",
                code="role_combination_not_permitted",
                params={"first_role": first_role, "second_role": second_role},
            )
