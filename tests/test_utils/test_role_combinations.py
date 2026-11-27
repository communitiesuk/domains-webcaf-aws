from itertools import combinations, permutations

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from webcaf.webcaf.utils.role_combinations import SUPPORTED_ROLE_CODES, validate_role_combination

# Keep expected policy independent from the production allow-list so the tests
# detect accidental additions to, or removals from, that allow-list.
ROLE_CODES = (
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
)
APPROVED_PAIRS = (
    ("caf_lead", "collaborator"),
    ("caf_lead", "systems_mapper"),
    ("collaborator", "systems_mapper"),
)


class RoleCombinationValidatorTest(SimpleTestCase):
    def test_supported_codes_match_the_agreed_catalogue_without_claimant(self):
        self.assertEqual(SUPPORTED_ROLE_CODES, frozenset(ROLE_CODES))
        self.assertNotIn("council_claimant", SUPPORTED_ROLE_CODES)

    def test_empty_role_set_is_valid(self):
        self.assertIsNone(validate_role_combination([]))

    def test_each_supported_role_is_valid_on_its_own(self):
        for role in ROLE_CODES:
            with self.subTest(role=role):
                self.assertIsNone(validate_role_combination([role]))

    def test_approved_pairs_are_valid_in_either_grant_order(self):
        for pair in APPROVED_PAIRS:
            for roles in permutations(pair):
                with self.subTest(roles=roles):
                    self.assertIsNone(validate_role_combination(roles))

    def test_every_ordered_pair_is_refused_unless_explicitly_approved(self):
        approved_pairs = {frozenset(pair) for pair in APPROVED_PAIRS}
        for roles in permutations(ROLE_CODES, 2):
            with self.subTest(roles=roles):
                if frozenset(roles) in approved_pairs:
                    self.assertIsNone(validate_role_combination(roles))
                else:
                    with self.assertRaises(ValidationError):
                        validate_role_combination(roles)

    def test_approver_is_exclusive_in_both_grant_orders_for_every_other_role(self):
        for role in ROLE_CODES:
            if role == "approver":
                continue
            for roles in permutations(["approver", role]):
                with self.subTest(roles=roles):
                    with self.assertRaisesMessage(
                        ValidationError, "Approver must be the only role assigned to a profile."
                    ) as raised:
                        validate_role_combination(roles)
                    self.assertEqual(raised.exception.code, "exclusive_approver")

    def test_conditional_pairs_are_refused_in_both_orders_until_conditions_are_approved(self):
        conditional_pairs = [
            ("caf_lead", "quality_assurer"),
            ("systems_mapper", "quality_assurer"),
            ("mhclg_super_user", "mhclg_assurance_lead"),
        ]
        for pair in conditional_pairs:
            for roles in permutations(pair):
                with self.subTest(roles=roles):
                    with self.assertRaises(ValidationError) as raised:
                        validate_role_combination(roles)
                    self.assertEqual(raised.exception.code, "role_combination_not_permitted")

    def test_independent_assurer_roles_must_remain_separate(self):
        roles = ("independent_assurer_lead", "independent_assurer", "independent_assurer_qa")
        for pair in permutations(roles, 2):
            with self.subTest(pair=pair):
                with self.assertRaises(ValidationError):
                    validate_role_combination(pair)

    def test_deputy_pairings_are_refused_pending_a_decision(self):
        for role in ROLE_CODES:
            if role == "deputy_caf_lead":
                continue
            for roles in permutations(["deputy_caf_lead", role]):
                with self.subTest(roles=roles):
                    with self.assertRaises(ValidationError):
                        validate_role_combination(roles)

    def test_unlisted_council_pair_is_refused(self):
        for roles in permutations(["collaborator", "quality_assurer"]):
            with self.subTest(roles=roles):
                with self.assertRaises(ValidationError) as raised:
                    validate_role_combination(roles)
                self.assertEqual(raised.exception.code, "role_combination_not_permitted")

    def test_role_pairs_from_different_groups_are_unlisted_and_refused(self):
        pairs = [
            ("caf_lead", "mhclg_super_user"),
            ("collaborator", "independent_assurer"),
            ("mhclg_assurance_lead", "independent_assurer_qa"),
        ]
        for pair in pairs:
            for roles in permutations(pair):
                with self.subTest(roles=roles):
                    with self.assertRaises(ValidationError):
                        validate_role_combination(roles)

    def test_approved_council_triple_is_valid_in_every_grant_order(self):
        # The ticket says to apply approved pair rules to every existing role;
        # all three constituent pairs of this combination are approved.
        for roles in permutations(["caf_lead", "collaborator", "systems_mapper"]):
            with self.subTest(roles=roles):
                self.assertIsNone(validate_role_combination(roles))

    def test_larger_role_sets_do_not_bypass_checks_by_containing_an_approved_pair(self):
        for extra_role in ["quality_assurer", "deputy_caf_lead", "approver", "mhclg_super_user"]:
            for roles in permutations(["caf_lead", "collaborator", extra_role]):
                with self.subTest(roles=roles):
                    with self.assertRaises(ValidationError):
                        validate_role_combination(roles)

    def test_every_supported_role_subset_matches_the_approved_pair_policy(self):
        valid_sets = {
            frozenset(),
            frozenset({"caf_lead", "collaborator", "systems_mapper"}),
        }
        valid_sets.update(frozenset({role}) for role in ROLE_CODES)
        valid_sets.update(frozenset(pair) for pair in APPROVED_PAIRS)
        for size in range(len(ROLE_CODES) + 1):
            for roles in combinations(ROLE_CODES, size):
                with self.subTest(roles=roles):
                    if frozenset(roles) in valid_sets:
                        self.assertIsNone(validate_role_combination(roles))
                    else:
                        with self.assertRaises(ValidationError):
                            validate_role_combination(roles)

    def test_unknown_claimant_and_legacy_role_codes_fail_closed(self):
        unsupported_codes = [
            "council_claimant",
            "organisation_lead",
            "organisation_user",
            "cyber_advisor",
            "reviewer",
            "assessor",
            "future_role",
            "CAF Lead",
            "CAF_LEAD",
            " caf_lead ",
            "",
        ]
        for code in unsupported_codes:
            for roles in [[code], [code, "caf_lead"], ["caf_lead", code], ["approver", code]]:
                with self.subTest(roles=roles):
                    with self.assertRaises(ValidationError) as raised:
                        validate_role_combination(roles)
                    self.assertEqual(raised.exception.code, "unsupported_role_code")

    def test_iterables_are_supported_without_modifying_caller_input(self):
        roles = ["systems_mapper", "caf_lead", "collaborator"]
        original = roles.copy()

        self.assertIsNone(validate_role_combination(roles))
        self.assertIsNone(validate_role_combination(iter(roles)))
        self.assertIsNone(validate_role_combination(set(roles)))
        self.assertEqual(roles, original)

    def test_bare_strings_are_not_mistaken_for_a_collection_of_codes(self):
        for value in ["", "caf_lead", "caf_lead,collaborator"]:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError) as raised:
                    validate_role_combination(value)
                self.assertEqual(raised.exception.code, "invalid_role_codes")

    def test_malformed_collections_are_validation_errors(self):
        for value in [None, 123, b"", b"caf_lead", bytearray(), [["caf_lead"]], [{"caf_lead"}]]:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError) as raised:
                    validate_role_combination(value)
                self.assertEqual(raised.exception.code, "invalid_role_codes")

    def test_collection_elements_must_be_strings(self):
        for value in [None, 123, b"caf_lead"]:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError) as raised:
                    validate_role_combination(iter(["caf_lead", value]))
                self.assertEqual(raised.exception.code, "invalid_role_codes")

    def test_duplicate_codes_cannot_hide_a_disallowed_combination(self):
        with self.assertRaises(ValidationError) as raised:
            validate_role_combination(["caf_lead", "caf_lead", "approver"])

        self.assertEqual(raised.exception.code, "exclusive_approver")

    def test_reported_pair_conflict_is_independent_of_input_order(self):
        errors = []
        for roles in permutations(["quality_assurer", "caf_lead", "collaborator"]):
            with self.assertRaises(ValidationError) as raised:
                validate_role_combination(roles)
            errors.append(raised.exception.messages)

        self.assertTrue(all(error == errors[0] for error in errors))
