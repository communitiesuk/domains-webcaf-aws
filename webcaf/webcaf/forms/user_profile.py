import logging

from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from webcaf.webcaf.models import UserProfile
from webcaf.webcaf.utils import mask_email
from webcaf.webcaf.utils.role_assignments import lock_profile


class UserProfileForm(forms.ModelForm):
    first_name = forms.CharField(max_length=150, required=True)
    last_name = forms.CharField(max_length=150, required=True)
    email = forms.EmailField(
        max_length=150,
        required=True,
        error_messages={
            "required": "You must add a valid email address",
            "invalid": "You must add a valid email address",
        },
    )
    role = forms.ChoiceField(choices=UserProfile.ROLE_CHOICES, required=True)
    # By default we do not pass the action field in the initial form, which makes the validation failure
    # and we capture that ant and redirect the user to the confirmation page.
    action = forms.ChoiceField(choices=[("change", "Change"), ("confirm", "Confirm")], required=True)

    logger = logging.getLogger("UserProfileForm")
    duplicate_membership_message = "This user already has a profile in this organisation. Edit the existing profile."
    ambiguous_email_message = "More than one account uses this email address. Contact support before continuing."
    username_conflict_message = (
        "This email address conflicts with an existing account. Contact support before continuing."
    )

    class Meta:
        model = UserProfile
        fields = ["first_name", "last_name", "email", "role"]

    def __init__(self, *args, **kwargs):
        """
        :param instance: Instance of userprofile object containing user information.
        :type instance: UserInstance

        :param initial: Initial data dictionary to be used for initialization.
        :type initial: dict
        """
        organisation = kwargs.pop("organisation", None)
        instance = kwargs.get("instance")
        if instance:
            # Set the initial values for the form fields
            initial = kwargs.setdefault("initial", {})
            initial["first_name"] = instance.user.first_name
            initial["last_name"] = instance.user.last_name
            initial["email"] = instance.user.email
        super().__init__(*args, **kwargs)
        if organisation is not None:
            self.instance.organisation = organisation

    def clean_role(self):
        role = self.cleaned_data["role"]
        if role == "cyber_advisor":
            raise PermissionDenied("You are not allowed to change this role")
        return role

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        matches = list(User.objects.filter(email__iexact=email)[:2])
        if len(matches) > 1:
            raise ValidationError(self.ambiguous_email_message, code="ambiguous_email")
        if not matches and User.objects.filter(username=email).exists():
            raise ValidationError(self.username_conflict_message, code="username_conflict")
        self.instance.validate_identity_change(matches[0].pk if matches else None, self.instance.organisation_id)
        if len(matches) == 1:
            self._check_membership(matches[0])
        return email

    def clean(self):
        data = super().clean()
        if not self.instance.organisation_id:
            raise ValidationError("An organisation is required for a user profile.")
        return data

    def _check_membership(self, user):
        if (
            UserProfile.objects.filter(user=user, organisation_id=self.instance.organisation_id)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise ValidationError(self.duplicate_membership_message, code="duplicate_membership")

    @transaction.atomic
    def save(self, commit=True):
        """
        Summary
        -------
        Method to save role and related user details.

        Details
        -------
        This method saves the current instance of a Role model to the UserProfile
        and updates the associated User model with the submitted name only for a new
        account or an explicit profile edit. Adding a profile for an existing account
        preserves its names. Only new accounts receive a lowercased email and username;
        existing email addresses and usernames are preserved. As before, `commit`
        controls saving the profile, not the related user.

        :param commit: Optional boolean indicating whether to save the changes to the database. Default is True.
        :return: UserProfile object representing the saved user profile.
        """

        if self.instance.pk:
            # Serialise account rebinding with grants and removals. Revalidate
            # below after resolving the submitted account, while holding locks.
            lock_profile(self.instance.pk)

        user_email = self.cleaned_data["email"]
        try:
            # Recheck at save time rather than trusting the match from validation.
            # Concurrent form submissions use the same lowercase username, whose
            # unique constraint allows get_or_create to retry the email lookup.
            user, created = User.objects.get_or_create(
                email__iexact=user_email,
                defaults={"email": user_email, "username": user_email},
            )
        except User.MultipleObjectsReturned as exc:
            raise ValidationError({"email": self.ambiguous_email_message}) from exc
        except IntegrityError as exc:
            raise ValidationError({"email": self.username_conflict_message}) from exc

        try:
            self._check_membership(user)
            self.instance.validate_identity_change(user.pk, self.instance.organisation_id)
        except ValidationError as exc:
            raise ValidationError({"email": exc}) from exc

        # Names belong to the shared account, not its organisation membership.
        if created or self.instance.pk is not None:
            user.first_name = self.cleaned_data["first_name"]
            user.last_name = self.cleaned_data["last_name"]
            user.save(update_fields=["first_name", "last_name"])
        self.instance.user = user

        log_message = (
            f"Created new user {user_email} {user.id} for profile {self.instance.id if self.instance.id else 'new profile'}"
            if created
            else f"User {user_email} {user.id} already exists, associating with the profile {self.instance.id if self.instance.id else 'new profile'}"
        )
        self.logger.info(mask_email(log_message))
        try:
            with transaction.atomic():
                return super().save(commit)
        except IntegrityError as exc:
            if (
                getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None)
                == "unique_user_organisation_profile"
            ):
                raise ValidationError({"email": self.duplicate_membership_message}) from exc
            raise
