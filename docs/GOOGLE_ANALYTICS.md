# Google Analytics

WebCAF can load either a Google Analytics 4 (GA4) tag or the existing Google Tag Manager (GTM) integration. Analytics
is disabled unless the `GOOGLE_ANALYTICS_ID` environment variable contains a valid GA4 measurement ID (`G-XXXXXXXXXX`)
or GTM container ID (`GTM-...`). The ID is public configuration, not a credential, but it is kept outside the
application so each deployment can be configured independently.

## Sandbox configuration

Configure this GitHub Environment variable in the **Sandbox** environment:

```text
GOOGLE_ANALYTICS_ID=<Sandbox GA4 measurement ID>
```

The Sandbox deployment workflow passes the variable to the ECS task. Staging and production workflows do not pass a
Google Analytics ID, so this change does not enable GA4 in those environments.

## Consent behaviour

The GA4 script is rendered with an inert script type and is not executed or downloaded until the user accepts analytics
cookies through the existing cookie banner. A previous acceptance stored in the `cookies_accepted` cookie is also
honoured. Rejecting analytics cookies leaves the tag inactive. GA4 page views use a fixed, non-personal page title so
that names present in some browser page titles are not sent to Google; pages remain distinguishable by their URL.

## Validation

After deploying to Sandbox:

1. Open browser developer tools, clear the site's cookie preferences, and reload the application.
2. Confirm that no request to `googletagmanager.com/gtag/js` occurs before consent.
3. Accept analytics cookies and confirm a request to `https://www.googletagmanager.com/gtag/js?id=<measurement-id>`
   succeeds.
4. In the Network panel, filter for `collect` and confirm page-view requests use the configured Sandbox measurement ID.
5. Confirm the page view in the Sandbox GA4 data stream's Realtime report. To use DebugView, first enable debug mode
   with the Google Analytics Debugger browser extension. The dedicated measurement ID distinguishes Sandbox traffic
   from traffic sent to other data streams.

Capture the successful script and `collect` requests, together with the corresponding GA4 Realtime or DebugView event,
as deployment evidence for the ticket.
