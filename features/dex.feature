@dex
Feature: DEX authentication

  Background:
    Given Organisation "Ministry of Agriculture" of type "ministerial-department" exists with systems "System 1"
    And the user "admin@example.gov.uk" exists
    And User "admin@example.gov.uk" has the profile "GovAssure lead" assigned in "Ministry of Agriculture"
    And the application is running
    And cookies have been "accepted"

  Scenario: Existing user signs in through DEX
    When the user signs in through DEX as "admin@example.gov.uk" with password "password"
    Then page contains text "My GovAssure lead account" in header

  Scenario: Existing user logs out through DEX
    Given the user signs in through DEX as "admin@example.gov.uk" with password "password"
    When the user logs out through DEX
    Then they should see page title "Start page  - Complete a WebCAF self-assessment - GOV.UK"
    And DEX requires the user to authenticate again
