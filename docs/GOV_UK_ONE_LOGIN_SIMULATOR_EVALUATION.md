# GOV.UK One Login simulator evaluation

## Decision

WebCAF should support both the GOV.UK One Login simulator and DEX for defined purposes.

The simulator should be used for One Login-specific local development and focused end-to-end protocol tests. DEX should remain the provider for generic OIDC development and the existing broad BDD suite until WebCAF's future identity-provider requirements are agreed.

The simulator supplements DEX; it does not replace it.

## Scope

This assessment was completed for CS-435 against simulator image `ghcr.io/govuk-one-login/simulator:26.09.16`, pinned to multi-platform digest `sha256:1f0930e04ced60b624c1b4e830cbe0a622be216b5065ece4756dbebdb0e92a60`.

The assessment covers WebCAF's current `govuk-onelogin-django` integration, local developer authentication, browser-driven testing, comparison with DEX, and dependencies on possible multiple-identity-provider requirements.

Verification against the pinned image completed a real authorization request, RSA private-key JWT token exchange, ES256 ID-token and JWKS validation, UserInfo request, verified-email user match, and redirect to WebCAF's no-profile response. The test ran against an isolated Django test database and restored the simulator's redirect configuration afterward. Pull-request CI runs this protocol smoke test and a separate serial browser suite for successful login, no-profile handling, unverified email rejection, and front-channel logout.

## WebCAF requirements

WebCAF's One Login client currently requires:

- OpenID Connect discovery with authorization, token, UserInfo, JWKS and end-session endpoints.
- Authorization code flow with state, nonce, `openid email`, and vector of trust `Cl.Cm.P0`.
- `private_key_jwt` client authentication with an RSA-signed client assertion.
- Signed ID token validation against the discovered issuer, audience, nonce and JWKS.
- UserInfo claims `sub`, `email`, and the JSON boolean `email_verified=true`.
- Front-channel logout using `id_token_hint` and a registered post-logout redirect URI.
- Configurable identities and failures for automated authentication tests.

Application roles, organisations and profiles are deliberately managed in WebCAF rather than sourced from identity-provider claims.

## Supported capabilities

The simulator meets the protocol requirements needed by the current integration:

- It publishes a discovery document for `/authorize`, `/token`, `/userinfo`, JWKS and `/logout`.
- It supports authorization code flow, state, nonce, scopes and WebCAF's `Cl.Cm.P0` vector of trust.
- It supports `private_key_jwt` and validates RSA client assertions against a configured static public key or JWKS.
- It signs ID tokens with ES256 or RS256 and publishes matching keys.
- It returns configurable `sub`, `email` and boolean `email_verified` UserInfo claims.
- It validates the ID token hint and registered redirect URI during front-channel logout.
- It can simulate authorization errors and invalid issuer, audience, algorithm, signature, expiry, issued-at, nonce and vector-of-trust values.
- It supports publishing and selecting replacement ID-token signing keys to test key rotation.
- It offers interactive response configuration for developers and a JSON `/config` API for tests.
- The published image supports both AMD64 and ARM64 development machines.

These capabilities exercise important behavior that WebCAF's mocked unit tests do not cover, particularly the real discovery, client assertion, token exchange, JWKS, ID-token validation and UserInfo contracts.

## Limitations

The simulator is a protocol simulator, not a complete local copy of One Login:

- It does not reproduce account creation, password, passkey, MFA, recovery or identity-provider user journeys.
- Interactive mode presents a simulator configuration form rather than the real One Login interface.
- It does not reproduce production One Login availability, TLS, rate limiting, latency or network controls.
- Logout validates and redirects but does not model a durable One Login browser session.
- Its mutable configuration and token stores are in memory and reset on restart.
- `/config` is unauthenticated and controls global simulator state. Tests sharing one instance can interfere with each other, so simulator-backed tests must run serially or use an isolated instance per worker.
- It cannot directly inject every token-endpoint, UserInfo or network failure. Some transport cases still require mocks or stopping the service.
- Its date-based image tags are released frequently and there are no GitHub Releases. WebCAF must pin and deliberately update the tested image rather than use `latest`.

The simulator cannot establish complete behavioral parity with deployed One Login. Integration-environment testing remains necessary before release.

## Local development assessment

The simulator is suitable for local WebCAF authentication when Django runs on the host:

- The simulator and PostgreSQL run in Docker, while Django uses `http://localhost:3000` for all discovered endpoints.
- Interactive mode allows developers to enter the email, verification state and subject needed for a scenario without maintaining static provider users.
- The simulator's published test key works with WebCAF's existing private-key path configuration.
- No new WebCAF authentication mode or production dependency is required.

The simulator is not a direct replacement for DEX in the default fully containerised stack. Discovery returns absolute URLs based on one configured simulator origin. `localhost:3000` is correct for the host browser but points back to the WebCAF container during server-side token and UserInfo requests. An internal service hostname has the inverse problem for the host browser. Solving this would require host-network assumptions or an additional shared hostname/proxy configuration, which is not justified for the initial local workflow.

## Automated and BDD testing assessment

The simulator is used for a focused, serial browser suite. Each scenario configures a deterministic subject, email and verification state through `/config`, starts the WebCAF login, and allows the simulator to redirect immediately without interacting with provider UI.

The highest-value simulator-backed scenarios are:

- Successful login for an existing user with a profile.
- Successful protocol login for an existing user without a profile, ending on the current no-profile response.
- Automatic creation for a verified user on an allowed domain.
- Rejection of an unverified email or disallowed domain.
- Authorization denial and representative invalid ID-token responses.
- Front-channel logout and return to WebCAF.
- ID-token signing-key rotation.

The existing mocked tests should remain because they are faster and can isolate application error handling. The full role and assessment BDD suite should also remain on DEX initially: after login those scenarios test WebCAF authorization and business behavior rather than One Login, and duplicating the whole suite would increase runtime without proportionate coverage.

A dedicated simulator Compose overlay and CI job keep this coverage separate from the current feature-test stack. In that environment the WebCAF container, simulator and Playwright browser share a Compose-resolvable simulator hostname, avoiding the host-browser networking constraint described above.

## Comparison with DEX

The simulator provides better fidelity for WebCAF's deployed One Login integration:

- It exercises `govuk-onelogin-django` rather than `mozilla-django-oidc`.
- It validates One Login's private-key JWT, vector-of-trust, claims and logout contracts.
- It provides controlled token validation errors and signing-key rotation.
- It removes the need to create accounts in the remote integration environment for protocol tests.

DEX remains useful and materially different:

- It exercises WebCAF's generic OIDC backend and client-secret flow.
- Its static multi-user password database is convenient for the existing role-based BDD suite.
- It supports the current fully containerised local workflow without split-host discovery issues.
- It preserves a development and regression path for a future non-One-Login identity provider.
- It is independent of One Login-specific package behavior and therefore catches generic OIDC regressions.

## Multiple identity providers

The authentication route for MHCLG or other internal users is not yet defined. Those users may use One Login, a separate internal identity provider, or another access mechanism. DEX must not be removed until that requirement is resolved.

WebCAF currently links One Login users by verified email and requires `sub` without persisting it. Supporting multiple external providers safely will require a durable identity mapping based on provider issuer and subject, with an explicit account-linking and email-change policy. Reusing email alone across providers risks incorrect account linking.

## Recommendation

Retain DEX as the default generic OIDC provider for the fully containerised development stack and broad BDD suite.

Use the One Login simulator as an opt-in local profile for host-based One Login development and retain the focused simulator-backed browser coverage in its serial CI job.

Review this decision after internal-user authentication and multi-provider identity mapping requirements have been agreed. No DEX removal work should be raised before that review.

## Follow-on ticket drafts

### Expand GOV.UK One Login simulator BDD coverage

Extend the dedicated simulator-backed feature-test stack and CI job with domain rejection, representative protocol errors and key rotation. Keep simulator state isolated or run the suite serially, pin the image, and leave the existing DEX suite operational.

Acceptance criteria:

- Domain rejection is browser-tested.
- Representative authorization and token-validation errors are browser-tested.
- ID-token signing-key rotation is exercised.
- Simulator configuration cannot leak between scenarios or parallel workers.
- The existing DEX-backed feature suite remains green.

### Define authentication for MHCLG and internal WebCAF users

Confirm which user populations must access WebCAF, whether each population can use GOV.UK One Login, and whether another identity provider is required. Define provider selection, account administration, assurance, logout and operational support requirements.

Acceptance criteria:

- Required user populations and identity providers are recorded.
- The authentication and account-recovery route for each population is agreed.
- The need to retain or retire the generic OIDC path is decided.
- Security and service ownership approve the decision.

### Introduce durable external identity mapping

If multiple external providers are required, model external identities by issuer and subject rather than linking solely by email. Define migration, account linking, changed-email and duplicate-identity behavior before enabling another provider.

Acceptance criteria:

- External identities are uniquely mapped by provider issuer and subject.
- Existing One Login users have a safe migration path.
- Email changes do not create or attach the wrong WebCAF account.
- Duplicate and conflicting mappings fail safely and are auditable without logging raw identifiers.
