import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from webcaf.webcaf.models import Assessment, UserProfile


class SessionUtil:
    logger: logging.Logger = logging.getLogger("SessionUtil")

    @staticmethod
    def get_current_user_profile(request) -> Optional["UserProfile"]:
        """
        Retrieve the current user's profile based on the session information.

        This method accesses the session to extract the current user's
        profile ID and attempts to fetch the user profile from the database.
        If the profile cannot be retrieved, an error is logged, and the method
        returns None.

        :param request: The HTTP request object containing the session with the
            "current_profile_id" key.
        :type request: HttpRequest
        :return: The UserProfile object corresponding to the current user, or None
            if the profile could not be retrieved.
        :rtype: Optional[UserProfile]
        """
        from webcaf.webcaf.models import UserProfile

        if hasattr(request, "current_profile"):
            return request.current_profile

        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return None

        user_profile_id = request.session.get("current_profile_id")
        if not user_profile_id:
            return None

        try:
            profile = UserProfile.objects.select_related("organisation").get(id=user_profile_id, user=user)
        except (TypeError, ValueError, UserProfile.DoesNotExist):
            SessionUtil.logger.warning("Unable to retrieve user profile with id %s", user_profile_id)
            request.session.pop("current_profile_id", None)
            return None

        request.current_profile = profile
        return profile

    @staticmethod
    def resolve_current_user_profile(request) -> Optional["UserProfile"]:
        """Select an authenticated user's active profile from validated session or cookie state."""
        from webcaf.webcaf.models import UserProfile

        if hasattr(request, "current_profile"):
            return request.current_profile

        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            request.current_profile = None
            return None

        profiles = list(UserProfile.objects.filter(user=user).select_related("organisation").order_by("id"))
        profiles_by_id = {str(profile.id): profile for profile in profiles}
        selected_profile = profiles_by_id.get(str(request.session.get("current_profile_id", "")))

        if selected_profile is None:
            selected_profile = profiles_by_id.get(request.COOKIES.get("last_org", ""))
        if selected_profile is None and profiles:
            selected_profile = profiles[0]

        if selected_profile is None:
            request.session.pop("current_profile_id", None)
        elif request.session.get("current_profile_id") != selected_profile.id:
            request.session["current_profile_id"] = selected_profile.id

        if request.session.get("profile_count") != len(profiles):
            request.session["profile_count"] = len(profiles)

        request.current_profile = selected_profile
        return selected_profile

    @staticmethod
    def get_current_assessment(request, status_to_get: str | None = "draft") -> Optional["Assessment"]:
        """
        Retrieve the current assessment for the user based on session data and the given status.

        This function fetches the assessment linked to the user's profile
        and organisation, using the `assessment_id` and `current_profile_id`
        stored in the session. It ensures the assessment belongs to the user's
        organisation and is in the 'status_to_get' state.

        :param status_to_get: The status of the assessment to retrieve. Defaults to 'draft'.
        :param request: HTTP request object containing session data used to
            identify the assessment and user profile.
        :return: Assessment object matching the specified session data.
        :rtype: Assessment
        """
        from webcaf.webcaf.models import Assessment

        id_: int | None = None
        # We will have the assessment in the session only if the user is logged in and
        # working on an assessment.
        if "assessment_id" in request.session.get("draft_assessment", {}):
            try:
                id_ = int(request.session["draft_assessment"]["assessment_id"])
                user_profile = SessionUtil.get_current_user_profile(request)
                if user_profile and user_profile.organisation:
                    assessment = Assessment.objects.get(
                        status=status_to_get, id=id_, system__organisation_id=user_profile.organisation.id
                    )
                    return assessment
            except Exception:  # type: ignore[catching-any]
                SessionUtil.logger.warning(f"Unable to retrieve assessment with id {id_} for user {request.user.pk}")
        return None
