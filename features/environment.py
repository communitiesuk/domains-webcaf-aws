import base64
import html
import os
import re
from datetime import datetime
from pathlib import Path

import django
from behave.model_type import Status
from django.conf import settings
from django.db.models import F, Value
from django.db.models.functions import Lower, Replace
from playwright.sync_api import Page, sync_playwright

from features.one_login_simulator import configure_identity
from features.util import ORM_EXECUTOR, run_async_orm


def _repo_root() -> Path:
    return Path(__file__).parent.parent


def _safe_artifact_name(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")
    return slug or "scenario"


def _failed_step_details(scenario) -> tuple[str, str]:
    for step in scenario.steps:
        if step.status in [Status.failed, Status.error]:
            return step.name, getattr(step, "error_message", "") or ""
    return "", ""


def _write_behave_summary_report(context):
    reports_path = _repo_root() / "reports"
    reports_path.mkdir(exist_ok=True)
    report_path = reports_path / "behave-summary.html"
    results = getattr(context, "scenario_results", [])
    passed = sum(1 for result in results if result["status"] == "passed")
    failed = sum(1 for result in results if result["status"] in {"failed", "error"})
    skipped = sum(1 for result in results if result["status"] not in {"passed", "failed", "error"})
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows = []
    for result in results:
        screenshot_html = ""
        if result["screenshot_base64"]:
            screenshot_html = (
                '<details class="screenshot"><summary>Failure screenshot</summary>'
                f'<img src="data:image/png;base64,{result["screenshot_base64"]}" '
                f'alt="Failure screenshot for {html.escape(result["scenario"])}">'
                "</details>"
            )
        error_html = ""
        if result["failed_step"] or result["error"]:
            error_html = (
                f'<div class="failed-step">{html.escape(result["failed_step"])}</div>'
                f'<pre>{html.escape(result["error"])}</pre>'
            )
        rows.append(
            "<tr>"
            f'<td>{html.escape(result["feature"])}</td>'
            f'<td>{html.escape(result["scenario"])}</td>'
            f'<td><span class="status {html.escape(result["status"])}">{html.escape(result["status"])}</span></td>'
            f'<td>{result["duration"]:.2f}s</td>'
            f"<td>{error_html}{screenshot_html}</td>"
            "</tr>"
        )

    report_path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Behave Test Report</title>
  <style>
    body {{ color: #17202a; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; }}
    h1 {{ margin-bottom: 8px; }}
    .meta {{ color: #52616b; margin-bottom: 24px; }}
    .summary {{ display: flex; gap: 12px; margin-bottom: 24px; }}
    .summary div {{ border: 1px solid #d5dde5; border-radius: 6px; padding: 12px 16px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #d5dde5; padding: 10px; text-align: left; vertical-align: top; }}
    th {{ background: #f3f5f7; }}
    .status {{ border-radius: 999px; color: white; display: inline-block; font-weight: 700; padding: 3px 10px; }}
    .passed {{ background: #00703c; }}
    .failed, .error {{ background: #d4351c; }}
    .skipped, .untested {{ background: #505a5f; }}
    .failed-step {{ color: #d4351c; font-weight: 700; margin-bottom: 8px; }}
    pre {{ background: #f7f2f1; border-left: 4px solid #d4351c; overflow: auto; padding: 12px; white-space: pre-wrap; }}
    details {{ margin-top: 12px; }}
    img {{ border: 1px solid #d5dde5; max-width: 100%; }}
  </style>
</head>
<body>
  <h1>Behave Test Report</h1>
  <div class="meta">Generated at {html.escape(generated_at)}</div>
  <div class="summary">
    <div><strong>{len(results)}</strong> scenarios</div>
    <div><strong>{passed}</strong> passed</div>
    <div><strong>{failed}</strong> failed</div>
    <div><strong>{skipped}</strong> other</div>
  </div>
  <table>
    <thead>
      <tr>
        <th>Feature</th>
        <th>Test</th>
        <th>Outcome</th>
        <th>Duration</th>
        <th>Failure details</th>
      </tr>
    </thead>
    <tbody>
      {"".join(rows)}
    </tbody>
  </table>
</body>
</html>
""",
        encoding="utf-8",
    )
    print(f"Behave summary report written to {report_path}")


def before_all(context):
    print("Starting Django")
    context.scenario_results = []
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webcaf.settings")
    django.setup()

    # Start Playwright
    context.playwright = sync_playwright().start()
    headless_testing = context.config.userdata.get("headless_testing", "True").lower() == "true"
    slow_mo = int(context.config.userdata.get("slow_mo", "0"))
    print(f"Starting Playwright Chromium with headless={headless_testing}, slow_mo={slow_mo}")
    context.browser = context.playwright.chromium.launch(headless=headless_testing, slow_mo=slow_mo)
    context.browser.new_context()
    context.page = context.browser.new_page()


def before_scenario(context, scenario):
    context.page.context.clear_cookies()
    context.page.context.clear_permissions()
    simulator_url = os.environ.get("ONE_LOGIN_SIMULATOR_URL")
    if os.environ.get("SSO_MODE", "").lower() == "one-login" and simulator_url:
        configure_identity("alice@example.gov.uk", True, simulator_url)
    if "think_time" in context:
        delattr(context, "think_time")

    # Also clear storage for all origins
    context.page.context.add_cookies([])  # Wipe all cookies

    def clear_db():
        print("****************** Clearing DB *****************************")
        from django.contrib.auth.models import User

        from webcaf.webcaf.models import (
            Assessment,
            Configuration,
            Organisation,
            UserProfile,
        )

        test_user_emails = [
            email.strip() for email in context.config.userdata.get("user_emails", "").split(",") if email.strip()
        ]

        Assessment.objects.filter(created_by__email__in=test_user_emails).delete()

        Assessment.objects.filter(
            system__organisation__name__in=[
                org.strip() for org in context.config.userdata.get("organisation_names", "").split(",")
            ]
        ).delete()

        UserProfile.objects.filter(
            user__email__in=test_user_emails,
            user__is_staff=False,
            user__is_superuser=False,
        ).delete()

        Organisation.objects.annotate(normalized_name=Replace(Lower(F("name")), Value(" "), Value(""))).filter(
            normalized_name__in=[
                org.lower().replace(" ", "").strip()
                for org in context.config.userdata.get("organisation_names", "").split(",")
            ]
        ).delete()

        User.objects.filter(email__in=test_user_emails, is_staff=False, is_superuser=False).delete()

        Configuration.objects.all().delete()
        # We need to create a default configuration for testing purposes.
        assessment_period, assessment_year = get_current_assessment_period()
        Configuration.objects.create(
            name="default",
            config_data={
                "default_framework": settings.DEFAULT_CAF_FRAMEWORK,
                "current_assessment_period": assessment_period,
                "assessment_period_end": f"31 March {assessment_year} 11:59pm",
            },
        )

    run_async_orm(clear_db)


def get_current_assessment_period() -> tuple[str, str]:
    """
    Generate the current assessment period based on the current date.
    if the date is less than 11:59pm of 31st March, then the assessment period is current year -1/current year, else
    it is current year/current year +1

    Returns:
        tuple[str, str]: A tuple containing the assessment period and the assessment year
    """
    current_date = datetime.now()
    # Determines the assessment period based on the current date
    if (
        current_date.month < 3
        or (current_date.month == 3 and current_date.day < 31)
        or (current_date.month == 3 and current_date.day == 31 and current_date.hour < 23 and current_date.minute < 59)
    ):
        return f"{current_date.year - 1}/{current_date.year}".replace("20", ""), f"{current_date.year}"
    else:
        return f"{current_date.year}/{current_date.year + 1}".replace("20", ""), f"{current_date.year + 1}"


def after_scenario(context, scenario):
    print(f"After Scenario: {scenario.status}")
    status = scenario.status.name if hasattr(scenario.status, "name") else str(scenario.status)
    status = status.lower()
    screenshot_base64 = ""
    screenshot_path = None
    failed_step, error = _failed_step_details(scenario)

    if scenario.status in [Status.failed, Status.error]:
        parent_path = _repo_root()
        print(f"Saving screenshot and HTML for failed scenario {parent_path} {scenario.name}")
        os.makedirs(parent_path / "artifacts", exist_ok=True)  # Folder for all failure artifacts

        if hasattr(context, "page") and isinstance(context.page, Page):
            # Generate safe filename
            safe_name = _safe_artifact_name(f"{scenario.feature.name}_{scenario.line}_{scenario.name}")

            try:
                # Save screenshot
                screenshot_path = f"artifacts/{safe_name}.png"
                context.page.screenshot(path=screenshot_path, full_page=True)
                screenshot_base64 = base64.b64encode((parent_path / screenshot_path).read_bytes()).decode("ascii")

                # Save HTML
                html_path = f"artifacts/{safe_name}.html"
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(context.page.content())

                print(f"Screenshot saved: {screenshot_path}")
                print(f"HTML saved: {html_path}")
            except Exception as screenshot_error:
                print(f"Could not save failure screenshot or HTML: {screenshot_error}")

    context.scenario_results.append(
        {
            "feature": scenario.feature.name,
            "scenario": scenario.name,
            "status": status,
            "duration": getattr(scenario, "duration", 0) or 0,
            "failed_step": failed_step,
            "error": error,
            "screenshot_path": screenshot_path,
            "screenshot_base64": screenshot_base64,
        }
    )


def after_all(context):
    _write_behave_summary_report(context)
    if hasattr(context, "browser"):
        context.browser.close()
    if hasattr(context, "playwright"):
        context.playwright.stop()
    ORM_EXECUTOR.shutdown(wait=True)
