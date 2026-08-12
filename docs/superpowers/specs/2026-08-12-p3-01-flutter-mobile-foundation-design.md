# P3-01 Flutter Mobile Foundation And Local Development Session Design

**Status:** Proposed for implementation approval
**Date:** 2026-08-12
**Roadmap position:** Phase 4 / P3-01
**Depends on:** P0-01 through P2-07 merged on `main`

## 1. Purpose

P3-01 establishes the smallest real Flutter client foundation for Android and iOS. It
connects a development build to the existing local API through a constrained device
pairing flow, stores only the resulting bearer session in OS-backed secure storage,
and provides the reusable visual foundation for the two accepted conversation themes.

This task deliberately does not start persona creation, imports, background upload,
model selection, consent, or real chat. Those are later Phase 4 tasks in the approved
platform design. The included conversation screen is a local, static style preview so
that the two visual systems can be validated before the real chat flow is introduced.

## 2. Existing Contract And Constraints

The current backend already provides a local session contract:

- `POST /api/v1/auth/session` returns `201` with `access_token`, `token_type`,
  `owner_id`, and `expires_at`.
- Every versioned API route except `/api/v1/health` and the session bootstrap route
  requires `Authorization: Bearer <token>`.
- Local sessions expire after 24 hours. The token is stored in the database only as a
  SHA-256 digest; the owner record is encrypted.
- The default backend bind remains `127.0.0.1:8080`. Native Flutter clients do not
  require a CORS exception.

The primary architecture remains Flutter for Android/iOS and the existing Web client
for PC development, review, and administration. Provider API keys must never enter
the Flutter project, secure storage, build defines, logs, or exported diagnostics.

## 3. P3-01 Scope

### 3.1 Flutter foundation

Create a standard Flutter application with Android and iOS runners, a feature-first
directory layout, and the following bounded layers:

```text
mobile/
  lib/
    app/                 # app shell, routes, dependency assembly
    core/config/         # validated API endpoint configuration
    core/network/        # HTTP client, redaction-aware transport errors
    core/session/        # session model, secure persistence, restoration
    features/connection/ # local service pairing and session UI
    features/appearance/ # theme preference and static conversation preview
  test/
```

The client stores only `access_token`, `owner_id`, and `expires_at` in OS-backed
secure storage. It does not store the development pairing token after a successful
session bootstrap. Endpoint configuration is Debug-only and is never silently copied
to a Release build. The Release app has no developer endpoint override, pairing route,
pairing-token input or persistence, or code path that emits
`X-Dev-Device-Bootstrap-Token`.

Request-serving logs use a structured allowlist: HTTP method, normalized route
template, status, peer class, and diagnostic ID only. They never record request
headers, request bodies, raw request targets or query values, exception arguments,
tracebacks, provider keys, session/bootstrap tokens, or full local file paths.

The startup state machine is:

```text
starting
  -> restoring_session
  -> connected                 (stored bearer token passes an authenticated probe)
  -> pairing_required          (no token, expired token, or a 401 response)
  -> pairing_in_progress
  -> connected | pairing_error
```

An authenticated `GET /api/v1/personas` is the initial session probe because it is
already available, protected, and returns a valid empty list before any persona exists.
On `401`, the Flutter client clears only its local secure session record and returns to
`pairing_required`. P3-01 adds no refresh token, account recovery, remote login, or
multi-account model.

### 3.2 Development device pairing

Physical devices cannot use the existing loopback-only development bootstrap directly.
P3-01 therefore adds a separate, development-only device-pairing capability without
weakening the current default. The selected topology is a direct application TLS
listener on the private LAN address. TLS-terminating reverse proxies, forwarded client
address headers, DNS-based endpoint discovery, and proxy trust are outside P3-01;
the server must observe the device's direct socket peer itself.

- New environment variables:
  - `PAST_PARTNER_DEV_DEVICE_BOOTSTRAP_TOKEN`
  - `PAST_PARTNER_DEV_DEVICE_ALLOWED_NETWORKS`, a comma-separated exact IP/CIDR
    allowlist such as `192.168.1.42/32`.
  - `PAST_PARTNER_DEV_DEVICE_TLS_CERT_FILE`
  - `PAST_PARTNER_DEV_DEVICE_TLS_KEY_FILE`
- Pairing is permitted only when all of these are true:
  1. `PAST_PARTNER_MODE=development`.
  2. `PAST_PARTNER_HOST` is an explicit private LAN address, not loopback,
     `0.0.0.0`, `::`, public, multicast, or unspecified.
  3. The device pairing token and at least one allowed network are configured.
  4. Every allowed CIDR is parsed with `ipaddress`, belongs to the same address
     family as its peer, and is either IPv4 RFC1918 or IPv6 ULA. Loopback,
     link-local, public, multicast, reserved, unspecified, mapped-IPv4, zone-indexed,
     catch-all, and overly broad CIDRs are rejected at startup. `/32` and `/128` are
     the required default; an intentionally wider private subnet is limited to `/24`
     for IPv4 or `/64` for IPv6.
  5. The TCP peer address is inside the configured allowlist after the same canonical
     address-family validation.
  6. The request carries the matching `X-Dev-Device-Bootstrap-Token`.
- Device pairing requires an application-created `ssl.SSLContext` around the private
  LAN listener. The certificate and key paths are regular local files outside source
  control; the certificate contains the exact configured private host address as an IP
  subject alternative name. A TLS-terminating proxy cannot satisfy these conditions.
- Token comparison uses `hmac.compare_digest`. The device token is a different value
  from `PAST_PARTNER_OWNER_BOOTSTRAP_TOKEN` and is never accepted as a production
  owner-bootstrap credential. Startup uses the same constant-time comparison and
  fails closed when both configured values are equal.
- The HTTP handler passes the direct socket peer address only. It does not trust
  `X-Forwarded-For` or another client-supplied address header.
- A failure returns a stable authentication error without revealing whether the token,
  host mode, or allowlist entry was wrong.
- Loopback development continues to work exactly as it does today and does not need a
  pairing token. Production mode continues to use only its existing owner bootstrap
  contract; it does not gain device pairing.

The Flutter Debug connection screen accepts the API base URL and a temporary pairing
token through obscured controls. It sends the token only in the pairing request and
immediately discards it after the response. The configuration token is reusable only
within the active trusted-development setup; it is not represented as a single-use
secret or as replay protection.

The Debug endpoint validator has exactly two modes:

- **Physical device LAN:** an `https://` URI whose host is the canonical private IP
  literal configured as `PAST_PARTNER_HOST` (IPv4 RFC1918 or IPv6 ULA). Device source
  addresses are separately checked against the configured allowed networks. The URI
  has no DNS hostname, user-info, query, fragment, or redirect behavior. The TLS chain must
  validate through the platform trust store against a developer-controlled local CA,
  and the leaf certificate must contain the entered IP as an IP SAN. Trust-all
  certificate callbacks are prohibited.
- **Simulator or USB port forwarding:** an `http://127.0.0.1` or `http://[::1]` URI
  only, when the forwarding mechanism makes the backend observe its existing loopback
  peer. It sends no device bootstrap header and uses the pre-existing loopback session
  contract. If the forwarding mechanism does not preserve loopback at the backend, the
  developer must use the physical-device TLS path instead.

Bootstrap requests set redirect following to false. Any redirect, certificate failure,
or endpoint-validation failure aborts before the device bootstrap header or a bearer
session can be sent to another origin.

Direct HTTP is not permitted for a physical device because the pairing header and the
24-hour bearer session could be observed and replayed on a shared LAN. Release builds
keep Android cleartext traffic and iOS ATS restrictions enabled. The implementation
must set `android:usesCleartextTraffic="false"` in the Release merged manifest, keep
Release network-security configuration free of cleartext exceptions, avoid
`NSAllowsArbitraryLoads` and ATS exceptions in the Release iOS plist, and reject an
`http` endpoint at Release runtime. CI inspects the merged Release Android manifest
and Release iOS plist. Production requires TLS and later production identity work.

### 3.3 Conversation visual foundation

The user may choose either accepted layout family in the client settings. Design and
acceptance use the terms **WeChat-style** and **QQ-style** because they describe the
provided references; the production app labels the choices **Simplified conversation**
and **Lively conversation** so it does not imply an affiliation with either product.

The preference is a non-sensitive local UI preference. It applies immediately to the
static preview and later to the real chat route. It does not change persona data,
message semantics, API payloads, provider selection, or consent.

| Attribute | Simplified conversation (WeChat-style reference) | Lively conversation (QQ-style reference) |
| --- | --- | --- |
| Header | centered title, back control, compact detail control | leading title, back control, menu control |
| Conversation canvas | neutral light gray with centered time dividers | white/light cool-gray canvas with cards and airy spacing |
| Outgoing message | right aligned, compact square avatar, soft green bubble | right aligned, pale blue bubble, rounded corners |
| Composer | voice control, broad white input, expression and add controls | rounded input, blue send button, compact quick-action rail |
| Expansion panel | dense expression grid or 4-column utility grid | categorized expressions, voice state, or large tiled utility grid |
| Motion and accessibility | restrained transitions; system keyboard owns input | restrained transitions; system keyboard owns input |

The preview and future component API use independent names such as
`CalmConversationScaffold` and `BrightConversationScaffold`. They use Flutter
`CupertinoIcons`/`Icons` and original neutral illustration placeholders only. No
third-party logo, avatar, sticker, mini-program card, image, proprietary icon asset,
or payment/transfer/gift behavior is copied from the reference applications.

P3-01 supplies static examples only. The preview may demonstrate chat, expression,
more, and voice panel geometry, but it sends no message and makes no media, location,
call, payment, or provider request. Later chat work must expose only actions with real
backend support; it must not show decorative controls that look actionable but cannot
be completed.

All layouts use `SafeArea`, platform text scaling, semantic labels, 44 dp minimum
interactive targets, and keyboard-aware `viewInsets`. The client never draws an
imitation operating-system keyboard; the platform keyboard appears when the real text
field receives focus.

## 4. API And Configuration Changes

The session endpoint keeps its response schema and Bearer session contract. Only the
development bootstrap path is extended:

```text
POST /api/v1/auth/session
  X-Dev-Device-Bootstrap-Token: <debug-only value>  # only for permitted LAN peers

201 { access_token, token_type: "Bearer", owner_id, expires_at }
401/403 { error: { code, message, ... } }           # existing stable error envelope
```

`ServerConfig` parses and validates the pairing environment variables at process
startup. Invalid CIDR syntax, a forbidden address family or network, an empty
allowlist when a token is configured, missing/unreadable TLS files, a certificate host
that does not match the configured private IP, equal device and owner tokens, a token
outside development mode, or a pairing-enabled host that is not an explicit private
LAN address fail closed during startup. The bootstrap token must decode to at least 32
random bytes. No CORS wildcard, credentialed cookie mode, or Flutter-specific CORS
exception is added. The device bootstrap header is deliberately not added to CORS
allowed headers because native Flutter does not use browser CORS; a future Web-only
decision must define its own exact-origin contract rather than reusing this development
pairing mechanism.

The server boundary receives both headers separately:

- `X-Local-Owner-Token` retains its existing production owner-bootstrap meaning.
- `X-Dev-Device-Bootstrap-Token` is considered only in the explicit development LAN
  pairing branch.

`LocalAuthService` distinguishes loopback and device-pairing session origin. The
existing loopback session TTL remains 24 hours. A device-pairing session has a fixed
one-hour maximum TTL and stores only an origin marker plus the SHA-256 fingerprint of
the configured device token, never the token itself. Authentication rejects a device
session whose fingerprint no longer matches the configured token. Rotating the
high-entropy device token and restarting the development service therefore revokes all
previously paired device sessions while preserving loopback sessions. The service keeps
an in-memory rate limit of at most five failed pairing attempts per peer per ten
minutes and twenty pairing attempts globally per minute; throttled and failed requests
return the same generic authentication result. These limits are for a single trusted
development process, not a production distributed rate-limiting system.

## 5. Out Of Scope

- OIDC/OAuth2, production mobile login, account registration, multiple accounts,
  refresh tokens, device management, and account recovery.
- Public network binding, automatic discovery, arbitrary LAN clients, proxy trust, or
  allowing a Flutter release build to use local HTTP.
- Persona creation, native file selection, imports, chunk upload, background retry,
  review, models, consent, real chat, streaming, calls, location, media capture, and
  payments.
- Replacing the Web client, adding a second frontend API, or putting provider keys in
  Flutter.

## 6. Implementation Sequence

1. Generate the Flutter Android/iOS skeleton and add only the supported dependencies
   required for HTTP, secure storage, and test doubles. Lock Release transport policy
   in the Android manifest, network-security configuration, iOS plist, and endpoint
   validator before connection UI work begins.
2. Add server configuration parsing and `LocalAuthService` pairing authorization with
   red tests for every allow/deny branch before implementation, including token
   equality, token entropy, address canonicalization, CIDR width, prohibited networks,
   TLS listener configuration, rate limits, one-hour device session expiry, and token
   rotation revocation.
3. Add direct application TLS to the configured private LAN listener. Explicitly reject
   pairing configuration when a TLS-terminating proxy would be required. Extend the
   HTTP boundary to pass the new header without changing production owner bootstrap
   behavior or browser CORS headers.
4. Add Flutter endpoint validation, pairing UI, secure session persistence, restore,
   and 401 clearing. Test physical-device pairing through direct application HTTPS or
   loopback port forwarding, never through direct HTTP, proxy forwarding, or an
   unvalidated certificate.
5. Add the two reusable conversation theme scaffolds and the non-networked preview
   selector.
6. Run Python unit/integration tests, `flutter analyze`, Dart/widget tests, Android
   Release manifest inspection, iOS Release plist inspection, and the available
   Android/iOS build or emulator smoke checks. Document any unavailable platform
   runtime explicitly rather than treating a generated project as tested.

## 7. Acceptance Criteria

### Backend pairing

1. Default `127.0.0.1` development bootstrap still succeeds without a device token.
2. A non-loopback request is denied when device pairing is absent, mismatched, or its
   peer IP is outside the configured CIDRs.
3. A non-loopback request succeeds only with development mode, an explicit private LAN
   bind, a matching device token, and an allowed peer IP.
4. Device pairing cannot be enabled in production and cannot replace or satisfy the
   production owner bootstrap token.
5. Equal device and owner bootstrap tokens, forbidden host addresses, public or
   catch-all networks, IPv4-mapped IPv6, zone-indexed addresses, invalid CIDRs, and
   over-broad private subnets fail during configuration validation without leaking a
   token value.
6. The direct private-LAN listener uses application TLS; a TLS-terminating proxy or a
   forwarded address header cannot enable pairing. Transport tests reject plain HTTP
   before the handler processes a pairing header.
7. Device tokens require at least 32 random bytes. A paired session expires within one
   hour, and rotating the device token then restarting the service invalidates earlier
   device-origin sessions without revoking loopback sessions. Failed and throttled
   pairing attempts are indistinguishable to the caller and respect the defined
   per-peer/global limits.
8. Server access and error logging uses only the specified structured allowlist.
   Captured-log tests prove that protected headers, body text, query secrets, provider
   keys, exception text, and full local paths do not appear.
9. Existing unauthenticated routes, Bearer checks, and exact CORS behavior regress
   neither in unit nor HTTP integration tests.

### Flutter session

1. Android and iOS projects compile from the same Flutter source tree.
2. In a Debug build, a developer can explicitly configure an allowed private-IP HTTPS
   API address with a platform-validated local certificate, or use exact loopback port
   forwarding, enter a pairing token where required, and receive a session from a
   correctly configured server.
3. The pairing token is not persisted; the bearer session is stored only through the
   OS secure-storage implementation and is redacted from logs and error UI.
4. Restarting the app restores a valid session. A 401 or expired session clears local
   session state and shows the reconnect route.
5. Release build configuration rejects cleartext HTTP and has no development pairing
   default embedded in the app; automated checks inspect the Android merged Release
   manifest and iOS Release plist for forbidden cleartext/ATS exceptions.
6. Release behavior tests prove that the connection route, endpoint override,
   pairing-token input/storage, and device bootstrap header emission are unavailable.
   Debug bootstrap tests prove redirects and certificate failures transmit neither the
   device bootstrap header nor the bearer token to a different origin.

### Visual foundation

1. Settings presents two clearly previewable conversation appearance choices and
   persists the selected non-sensitive preference.
2. Simplified and Lively previews match the approved hierarchy: header placement,
   neutral/cool conversation canvas, outgoing bubble treatment, composer proportions,
   and expandable-panel density.
3. Chat, expression, more, and voice preview states can be inspected without any
   network side effect.
4. Widget tests cover theme selection, visible semantic labels, keyboard-safe composer
   layout, and both compact-phone and tablet/desktop width constraints.
5. No copied third-party assets or nonfunctional payment/transfer/gift actions appear
   in the shipped client.

## 8. Risks And Follow-Up Boundaries

LAN pairing is intentionally narrow and applies only to a trusted local development
network. It is not a substitute for production TLS, authenticated identity, or device
authorization. The first physical-device smoke test must use a developer-controlled
network, direct application HTTPS or loopback port forwarding, and a `/32` or `/128`
allowlist whenever possible. A reverse proxy or a broad private subnet requires a
separate architecture decision; it is not silently enabled by this task.

The visual foundation is designed before real chat data, streaming behavior, media
permissions, and background execution exist. Those later tasks must validate their
own loading, offline, error, accessibility, and platform-permission states against the
same reusable scaffolds instead of extending P3-01 without a separately accepted task.
