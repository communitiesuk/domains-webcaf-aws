import re

from behave import step
from playwright.sync_api import expect

from features.one_login_simulator import configure_identity


@step('the user signs in with One Login as "{user_name}"')
def sign_in_with_one_login(context, user_name):
    configure_identity(user_name, True)
    context.page.get_by_role("button", name="Sign in").click()
    context.current_email = user_name


@step('the user attempts to sign in with an unverified One Login email "{user_name}"')
def sign_in_with_unverified_one_login_email(context, user_name):
    configure_identity(user_name, False)
    context.page.get_by_role("button", name="Sign in").click()


@step("the user logs out through One Login")
def log_out_through_one_login(context):
    context.page.get_by_role("button", name=re.compile("^Logout")).click()
    base_url = re.escape(context.config.userdata["base_url"])
    expect(context.page).to_have_url(re.compile(rf"{base_url}/\??$"))
