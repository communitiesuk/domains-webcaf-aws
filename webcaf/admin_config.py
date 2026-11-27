from django.contrib.admin.apps import AdminConfig


class WebcafAdminConfig(AdminConfig):
    default_site = "webcaf.entra_admin.WebcafAdminSite"
