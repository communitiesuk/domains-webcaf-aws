import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.forms import ModelForm
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.generic import FormView, TemplateView

from webcaf.webcaf.models import Organisation, UserProfile


class OrganisationForm(ModelForm):
    """
    Update all the information of an organisation
    """

    class Meta:
        model = Organisation
        fields = "__all__"


class OrganisationView(LoginRequiredMixin, FormView):
    """
    OrganisationView is responsible for handling the organisation form view for a
    logged-in user. It enforces authentication requirements and ensures that a user
    can only access and modify their own associated organisation.

    This view includes methods to retrieve user-specific context data, fetch the
    associated organisation object, and handle instance binding for forms. The
    primary use case revolves around ensuring secure and restricted access to
    profile and organisation data, enabling user-specific actions in a controlled
    manner.

    :ivar login_url: The URL used to redirect unauthenticated users for login.
    :type login_url: str
    """

    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)
        profile_id = self.kwargs.get("id")
        # Ensure that the claimed profile belongs to teh current user
        profile = UserProfile.objects.get(user=self.request.user, id=profile_id)
        data["breadcrumbs"] = [{"url": reverse("my-account"), "text": "Back", "class": "govuk-back-link"}]
        data["profile"] = profile
        return data

    def get_object(self):
        """
        Get the object to be updated based on the current user profile and the selected
        profile id. This makes sure that the given user can only modify allowed profiles.
        :return:
        """
        return UserProfile.objects.get(user=self.request.user, id=self.kwargs.get("id")).organisation

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_object()
        return kwargs

    def form_valid(self, form):
        form.save()
        return super().form_valid(form)


class MyOrganisationView(OrganisationView):
    """
    Present the read-only view of the organisation.
    """

    template_name = "user-pages/my-organisation.html"
    form_class = OrganisationForm


class ChangeActiveProfileView(LoginRequiredMixin, TemplateView):
    """
    Organisation change screen.
    """

    template_name = "user-pages/change-organisation.html"
    logger = logging.getLogger("ChangeActiveProfileView")

    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)
        # Using select_related to optimize the query for related organisation
        profiles = (
            UserProfile.objects.filter(user=self.request.user)
            .select_related("organisation")
            .order_by("organisation__name")
        )
        data["breadcrumbs"] = [{"url": reverse("my-account"), "text": "Back", "class": "govuk-back-link"}]
        data["profiles"] = profiles
        return data

    def post(self, request, *args, **kwargs):
        """
        Set the current profile to the one selected in the form.
        Since the profile is attached to the organisation, this dictates what the current organisation is.
        :param request:
        :param args:
        :param kwargs:
        :return:
        """
        profile_id = request.POST.get("profile_id")
        if profile_id:
            self.logger.info(f"The user {self.request.user.id} switching to  profile_id: {profile_id}")
            profile = UserProfile.objects.filter(user=self.request.user, id=profile_id).first()
            if profile:
                self.request.session["current_profile_id"] = profile.id
                self.request.current_profile = profile
            else:
                self.logger.error(f"The user {self.request.user.id} could not switch profile as not found")
                return render(request, "user-pages/no-profile-setup.html", status=403)
        return redirect(reverse("my-account"))
