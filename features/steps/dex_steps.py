import re
from time import sleep

from behave import step
from playwright.sync_api import expect


@step('the user signs in through DEX as "{user_name}" with password "{password}"')
def sign_in_through_dex(context, user_name, password):
    page = context.page
    page.get_by_role("button", name="Sign in").click()
    if "think_time" in context:
        sleep(context.think_time)
    expect(page.get_by_role("heading")).to_contain_text("Log in to Your Account")
    page.get_by_placeholder("email address").fill(user_name)
    page.get_by_placeholder("password").fill(password)
    page.get_by_role("button", name="Login").click()
    expect(page.get_by_role("heading")).to_contain_text("Grant Access")
    page.get_by_role("button", name="Grant Access").click()
    context.current_email = user_name


@step("the user logs out through DEX")
def log_out_through_dex(context):
    context.page.get_by_role("button", name=re.compile("^Logout")).click()
    base_url = re.escape(context.config.userdata["base_url"])
    expect(context.page).to_have_url(re.compile(rf"{base_url}/\??$"))


@step("DEX requires the user to authenticate again")
def dex_requires_authentication(context):
    context.page.get_by_role("button", name="Sign in").click()
    expect(context.page.get_by_role("heading")).to_contain_text("Log in to Your Account")
