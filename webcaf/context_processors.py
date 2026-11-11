import os
import re

GA4_MEASUREMENT_ID_PATTERN = re.compile(r"G-[A-Z0-9]{10}", re.IGNORECASE)
GTM_CONTAINER_ID_PATTERN = re.compile(r"GTM-[A-Z0-9]+", re.IGNORECASE)


def variable_page_content(request):
    """
    Provide environment-dependent values for rendering pages
    (e.g. header text for the phase banner)
    :param request: current HTTP request
    :return:
    """
    phase = os.getenv("ENVIRONMENT")
    if phase not in ["prod", "staging"]:
        context = {
            "PHASE_CLASS": "govuk-tag--pink",
            "PHASE_HEADER": "Prototype",
            "PHASE_CONTENT": "This is not a full service. It is not production code and you might experience problems.",
        }
    else:
        context = {
            "PHASE_CLASS": "",
            "PHASE_HEADER": "Beta",
            "PHASE_CONTENT": "<div class='govuk-phase-banner__text'>Help us improve this service by <a class='govuk-link' href='https://forms.office.com/e/34eLTYNqnL' target='_blank'>reporting a problem or giving your feedback (opens in new tab)</a>.</div>",
        }

    google_analytics_id = os.getenv("GOOGLE_ANALYTICS_ID", "").strip()
    if GA4_MEASUREMENT_ID_PATTERN.fullmatch(google_analytics_id):
        context["GOOGLE_ANALYTICS_ID"] = google_analytics_id.upper()
        context["GOOGLE_ANALYTICS_TYPE"] = "ga4"
    elif GTM_CONTAINER_ID_PATTERN.fullmatch(google_analytics_id):
        context["GOOGLE_ANALYTICS_ID"] = google_analytics_id.upper()
        context["GOOGLE_ANALYTICS_TYPE"] = "gtm"
    else:
        context["GOOGLE_ANALYTICS_ID"] = ""
        context["GOOGLE_ANALYTICS_TYPE"] = ""

    resolver_match = getattr(request, "resolver_match", None)
    context["GOOGLE_ANALYTICS_PAGE_ID"] = getattr(resolver_match, "view_name", "") or "unmapped-page"

    return context
