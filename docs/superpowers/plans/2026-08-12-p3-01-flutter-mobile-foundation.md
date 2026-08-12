# P3-01 Flutter Mobile Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first Android/iOS Flutter client foundation, with tightly scoped development-device pairing and the accepted switchable static conversation visual systems.

**Architecture:** The existing Python API remains the only backend gateway. Development LAN pairing extends the current session endpoint through a separately configured, direct-TLS listener and a device-session origin, while loopback development and production owner bootstrap retain their current contracts. The Flutter client owns endpoint validation, ephemeral pairing-token handling, OS-backed bearer-session storage, and a non-networked conversation preview whose two themes are reusable for later real chat work.

**Tech Stack:** Python 3.14 standard-library HTTP/SSL/IP parsing, `cryptography`, SQLite migrations, Python `unittest`; Flutter/Dart, `http`, `flutter_secure_storage`, `shared_preferences`, Material/Cupertino icons, Android manifest configuration, iOS Info.plist.

---

## Scope Guardrails

- Keep the default `127.0.0.1:8080` development flow unchanged.
- Enable device pairing only in development, only on a direct private-LAN TLS listener, and only for validated private CIDRs.
- Never add the device bootstrap header to browser CORS headers, logs, diagnostics, persisted records, build defines, or Release code paths.
- Store a bearer session only in OS-backed secure storage; keep appearance preference separately in non-sensitive preferences; never persist the pairing token.
- Ship static preview geometry only. Do not add chat transport, providers, media/location/call/payment actions, persona flows, imports, or consent workflows to P3-01.
- Preserve the user's existing unstaged `.gitignore` change in the primary worktree. Execute feature work in an isolated worktree and `codex/p3-01-flutter-mobile-foundation` branch.

## File Structure

| Path | Responsibility |
| --- | --- |
| `src/server/config.py` | Parse and fail-closed validate device-pairing environment configuration, private addresses, allowed CIDRs, token entropy, and TLS certificate IP SAN. |
| `src/services/local_auth.py` | Authorize loopback versus device-pairing session issue, hash device-token fingerprint, enforce one-hour device TTL, validate device session rotation, and apply in-memory pairing limits. |
| `src/services/database.py` | Add an append-only migration for `local_sessions.session_origin` and `local_sessions.pairing_token_fingerprint`. |
| `src/server/application.py` | Assemble validated device-pairing configuration and pass both bootstrap credentials to local auth without changing owner-bootstrap semantics. |
| `src/server/http.py` | Wrap a pairing-enabled LAN listener with application TLS, forward only direct peer address plus separate headers, preserve CORS, and emit redacted structured request logging. |
| `src/server/__main__.py` | Display the actual serving scheme without printing sensitive configuration. |
| `.env.example` and `README.md` | Document development-only direct-TLS pairing, strict IP/CIDR examples, token generation, certificate requirements, and port-forwarding option. |
| `tests/support/__init__.py` | Makes test-only TLS helpers importable through `unittest` discovery. |
| `tests/support/tls_fixtures.py` | Generate ephemeral local CA/server test certificates containing an IP SAN; do not commit a test private key. |
| `tests/unit/test_server_config.py` | Cover all configuration allow/deny branches and no-secret errors. |
| `tests/unit/test_local_auth.py` | Cover pairing authorization, limits, expiry, fingerprint rotation, and loopback regression behavior. |
| `tests/unit/test_http_logging.py` | Prove request and error logs contain only the declared safe fields. |
| `tests/integration/test_http_device_pairing.py` | Exercise direct TLS pairing, plain HTTP rejection, header separation, CORS preservation, and session probe behavior. |
| `mobile/pubspec.yaml` and `mobile/analysis_options.yaml` | Define the Flutter application, narrowly scoped dependencies, linting, and test configuration. |
| `mobile/android/` and `mobile/ios/` | Generated native runners plus Release transport policy that forbids cleartext and ATS exceptions. |
| `mobile/lib/app/` | App assembly, typed navigation, dependency creation, and startup state routing. |
| `mobile/lib/core/config/` | Debug-only endpoint parser and build-mode restrictions. |
| `mobile/lib/core/network/` | Redirect-disabled session bootstrap and authenticated probe with redacted transport errors. |
| `mobile/lib/core/session/` | Session value, secure store interface/implementation, and restore/pair/clear state controller. |
| `mobile/lib/features/connection/` | Debug connection form and release-safe unavailable route. |
| `mobile/lib/features/appearance/` | Non-sensitive theme preference plus `CalmConversationScaffold` and `BrightConversationScaffold` static previews. |
| `mobile/test/` | Endpoint, session, transport-policy, connection, and visual/widget regression tests. |

## Task 0: Establish the Mobile Toolchain Baseline

**Files:**
- Create: generated Flutter project files under `mobile/` after the SDK preflight succeeds
- Modify: none before the preflight
- Test: command-only prerequisite checks

- [ ] **Step 1: Confirm the Flutter and Android toolchain without mutating the workstation**

Run:

```powershell
flutter --version
flutter doctor -v
```

Expected: Flutter SDK is on `PATH`; `flutter doctor -v` reports an Android toolchain suitable for Debug APK builds. Record the exact unresolved items rather than treating generated source as buildable.

- [ ] **Step 2: Obtain explicit approval before installing missing SDKs or accepting Android licenses**

Do not run a package manager, install a Flutter SDK, accept Android licenses, or modify global `PATH` until the user has approved that machine-level change. On the current Windows workstation `flutter` is not found, so this approval is required before the Flutter implementation and build tasks can be verified.

- [ ] **Step 3: Record platform verification boundaries**

Use this delivery matrix throughout P3-01:

| Target | Required evidence on this workstation |
| --- | --- |
| Android Debug | `flutter analyze`, `flutter test`, and `flutter build apk --debug` |
| Android Release policy | `flutter build apk --release` plus an automated merged-manifest assertion |
| iOS source policy | Dart test parses Release `Info.plist` and finds no ATS exception |
| iOS binary | Mark unavailable on Windows; run `flutter build ios --no-codesign` on a macOS runner before claiming iOS compilation |

## Task 1: Add Fail-Closed Device Pairing Configuration

**Files:**
- Modify: `src/server/config.py`
- Modify: `tests/unit/test_server_config.py`
- Create: `tests/support/__init__.py`
- Create: `tests/support/tls_fixtures.py`

- [ ] **Step 1: Write failing configuration tests for the complete pairing matrix**

Add tests using an ephemeral certificate generated by `tests.support.tls_fixtures.create_server_certificate`:

```python
def test_device_pairing_accepts_only_private_host_and_narrow_private_networks(self) -> None:
    certificate, key = create_server_certificate(self.root, "192.168.50.7")
    config = ServerConfig(
        host="192.168.50.7",
        mode="development",
        device_bootstrap_token=_token(32),
        device_allowed_networks=("192.168.50.42/32",),
        device_tls_cert_file=certificate,
        device_tls_key_file=key,
    ).validated()
    self.assertTrue(config.device_pairing_enabled)

def test_device_pairing_rejects_equal_owner_token_public_host_and_broad_networks(self) -> None:
    with self.assertRaisesRegex(ValueError, "device pairing"):
        ServerConfig(host="8.8.8.8", mode="development", device_bootstrap_token=_token(32)).validated()
    with self.assertRaisesRegex(ValueError, "must differ"):
        ServerConfig(mode="development", owner_bootstrap_token=_token(32), device_bootstrap_token=_token(32)).validated()
    with self.assertRaisesRegex(ValueError, "allowed network"):
        ServerConfig(host="192.168.50.7", mode="development", device_allowed_networks=("192.168.0.0/16",)).validated()
```

Also add individual assertions for: partial configuration, device pairing in production or test mode, empty token, base64 decoding below 32 bytes, invalid CIDR, `0.0.0.0/0`, `::/0`, public/multicast/reserved/unspecified/loopback/link-local networks, IPv4-mapped IPv6, zone-indexed input, mismatched address family, absent/unreadable certificate/key, and an IP SAN that differs from `host`. Assert exception messages do not contain the token value.

- [ ] **Step 2: Run the new configuration tests and observe red**

Run:

```powershell
python -m unittest tests.unit.test_server_config -v
```

Expected: FAIL because `ServerConfig` has no device pairing fields or validation helpers.

- [ ] **Step 3: Implement the exact configuration model and helpers**

Add these immutable fields to `ServerConfig` and parse their environment values in `from_env`:

```python
device_bootstrap_token: str | None = None
device_allowed_networks: tuple[str, ...] = ()
device_tls_cert_file: Path | None = None
device_tls_key_file: Path | None = None

@property
def device_pairing_enabled(self) -> bool:
    return self.device_bootstrap_token is not None
```

Implement private helpers in the same module:

```python
def _parse_private_host(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address: ...
def _parse_allowed_network(value: str, host: ipaddress._BaseAddress) -> ipaddress._BaseNetwork: ...
def _decode_device_token(value: str) -> bytes: ...
def _certificate_has_ip_san(certificate_path: Path, host: ipaddress._BaseAddress) -> bool: ...
```

`_parse_private_host` accepts only RFC1918 IPv4 or ULA IPv6 literals, rejects hostnames and every non-private category, and rejects IPv4-mapped IPv6 and zone-indexed values before parsing. `_parse_allowed_network` uses `ipaddress.ip_network(..., strict=True)`, requires the same family, accepts only RFC1918/ULA address space, rejects every forbidden category/catch-all, and requires prefix length at least `/24` for IPv4 or `/64` for IPv6. `_decode_device_token` uses strict Base64 decoding and requires at least 32 bytes. `_certificate_has_ip_san` reads `x509.SubjectAlternativeName` through `cryptography` and requires the exact parsed IP. Require all four device fields together; only then allow a non-loopback development listener. Reject device fields outside development and use `hmac.compare_digest` to reject device and owner tokens with equal values.

`tests/support/__init__.py` must be an empty package marker. `tests/support/tls_fixtures.py` must create a temporary CA, issue a server certificate with `x509.IPAddress(ipaddress.ip_address(host))`, and return the PEM certificate and key paths under the test runtime directory. It must never log or commit generated key material.

- [ ] **Step 4: Run configuration tests to green**

Run:

```powershell
python -m unittest tests.unit.test_server_config -v
```

Expected: PASS, including every explicit reject case and one valid `/32` or `/128` configuration.

- [ ] **Step 5: Commit the configuration boundary**

```powershell
git add src/server/config.py tests/unit/test_server_config.py tests/support/__init__.py tests/support/tls_fixtures.py
git commit -m "feat: validate development device pairing configuration"
```

## Task 2: Persist and Enforce Device-Session Origin

**Files:**
- Modify: `src/services/database.py`
- Modify: `src/services/local_auth.py`
- Modify: `tests/unit/test_local_auth.py`
- Modify: `tests/unit/test_database_migrations.py`

- [ ] **Step 1: Write failing local-auth tests for pairing and rotation**

Add test helpers that construct a `DevicePairingSettings` value from Task 1. Cover this contract:

```python
def test_device_pairing_issues_one_hour_fingerprinted_session(self) -> None:
    token = _token(32)
    auth = self._auth_with_device_pairing(token=token, allowed_networks=("192.168.50.42/32",))
    session = auth.issue_session("192.168.50.42", presented_device_bootstrap_token=token)
    row = self._session_row(session["access_token"])
    self.assertEqual("device", row["session_origin"])
    self.assertEqual(32, len(row["pairing_token_fingerprint"]))
    self.assertLessEqual(_expires_at(session) - datetime.now(UTC), timedelta(hours=1, seconds=1))

def test_rotating_device_token_invalidates_only_device_session(self) -> None:
    original = _token(32)
    auth = self._auth_with_device_pairing(token=original)
    loopback = auth.issue_session("127.0.0.1")
    device = auth.issue_session("192.168.50.42", presented_device_bootstrap_token=original)
    restarted = self._auth_with_device_pairing(token=_token(32))
    restarted.authenticate(f"Bearer {loopback['access_token']}")
    with self.assertRaisesRegex(LocalAuthError, "authentication_required"):
        restarted.authenticate(f"Bearer {device['access_token']}")
```

Also assert: direct non-loopback still fails with no settings; wrong/missing device token, owner token supplied in the device header, peer outside the CIDR, and every rate-limited request all return the identical `auth_bootstrap_forbidden` code; five failures per peer in ten minutes blocks the sixth; twenty total attempts in one minute blocks the twenty-first; a valid loopback session remains 24 hours; and a production bootstrap cannot use a device token.

- [ ] **Step 2: Run local-auth tests and observe red**

Run:

```powershell
python -m unittest tests.unit.test_local_auth -v
```

Expected: FAIL because device settings, columns, and separate header parameters do not exist.

- [ ] **Step 3: Add migration eight and the pairing authorization branch**

Append this migration without changing migrations 1 through 7:

```python
Migration(
    version=8,
    name="device_pairing_sessions",
    statements=(
        "ALTER TABLE local_sessions ADD COLUMN session_origin TEXT NOT NULL DEFAULT 'loopback' CHECK (session_origin IN ('loopback', 'device'))",
        "ALTER TABLE local_sessions ADD COLUMN pairing_token_fingerprint BLOB CHECK (pairing_token_fingerprint IS NULL OR length(pairing_token_fingerprint) = 32)",
    ),
)
```

In `local_auth.py`, define immutable `DevicePairingSettings` with canonical host, parsed allowed networks, raw device token, and `token_fingerprint = sha256(token.encode("utf-8")).digest()`. Add an injected monotonic clock and a lock-protected `PairingAttemptLimiter` with `record_attempt(peer, now)` and `record_failure(peer, now)`. The limiter must prune stale timestamp deques before enforcing the five-failure/ten-minute and twenty-attempt/one-minute thresholds.

Change the public method boundary exactly once:

```python
def issue_session(
    self,
    remote_address: str,
    presented_owner_bootstrap_token: str | None = None,
    presented_device_bootstrap_token: str | None = None,
) -> dict[str, str]:
```

For development/test loopback, retain the current 24-hour branch. For development plus settings, parse the direct peer IP, require a canonical same-family allowed network and constant-time matching device token, record attempts/failures, then create a `device` session with `min(self.session_ttl, timedelta(hours=1))` and the fingerprint. All denied pairing paths use the same `LocalAuthError("auth_bootstrap_forbidden", "device pairing is unavailable")`. Authentication selects `session_origin` and fingerprint, rejecting a device row unless `hmac.compare_digest(stored_fingerprint, current_fingerprint)` succeeds. Existing loopback rows require no fingerprint.

- [ ] **Step 4: Run local-auth and migration tests to green**

Run:

```powershell
python -m unittest tests.unit.test_local_auth tests.unit.test_database_migrations -v
```

Expected: PASS. Inspect a test database row to confirm it contains only a SHA-256 fingerprint, never the device token.

- [ ] **Step 5: Commit device-session persistence**

```powershell
git add src/services/database.py src/services/local_auth.py tests/unit/test_local_auth.py tests/unit/test_database_migrations.py
git commit -m "feat: enforce bounded development device sessions"
```

## Task 3: Add Direct TLS Transport and Redacted Request Logging

**Files:**
- Modify: `src/server/application.py`
- Modify: `src/server/http.py`
- Modify: `src/server/__main__.py`
- Create: `tests/integration/test_http_device_pairing.py`
- Create: `tests/unit/test_http_logging.py`

- [ ] **Step 1: Write failing TLS and header-separation integration tests**

Create a `DevicePairingHttpTests` fixture that uses the generated certificate. The fixture must keep the two concerns separate: call `LocalAuthService` directly with the private peer address from Task 2 for authorization, and bind a deliberately constructed `ApplicationServer` to `127.0.0.1` with a spy `Application.issue_session` for transport/header routing. The test-only loopback binding must not flow through `create_server` or change `ServerConfig` validation; `create_server` is separately asserted to install an `ssl.SSLContext` whenever a valid private-LAN pairing configuration is supplied. Its HTTPS client must use an `ssl.SSLContext` trusting the temporary CA. Include:

```python
def test_tls_pairing_accepts_only_the_device_header(self) -> None:
    status, _, payload = self.https_request(
        "POST",
        "/api/v1/auth/session",
        headers={"X-Dev-Device-Bootstrap-Token": self.device_token},
    )
    self.assertEqual(201, status)
    self.assertIn("access_token", payload)

def test_options_does_not_advertise_device_pairing_header(self) -> None:
    status, headers, _ = self.https_request("OPTIONS", "/api/v1/personas")
    self.assertEqual(204, status)
    self.assertNotIn("X-Dev-Device-Bootstrap-Token", headers["Access-Control-Allow-Headers"])
```

Add a plain `http.client.HTTPConnection` test against the TLS socket that proves no HTTP response is processed. Add tests that `X-Forwarded-For` cannot alter the peer decision, existing `X-Local-Owner-Token` production behavior stays separate, and an existing approved CORS origin is echoed exactly while an unapproved origin is not.

For logs, capture the `src.server.http` logger and send a request with `Authorization`, both bootstrap headers, a query secret, a request body secret, a provider key-like string, and a generated local file path. Assert the captured output has a request method, normalized path, status, peer class, and diagnostic ID, but does not contain any supplied secret, raw query, body text, header value, exception text, traceback, or full path.

- [ ] **Step 2: Run transport/logging tests and observe red**

Run:

```powershell
python -m unittest tests.integration.test_http_device_pairing tests.unit.test_http_logging -v
```

Expected: FAIL because the server creates a plain socket, passes one ambiguous bootstrap header, and logs raw request lines.

- [ ] **Step 3: Implement TLS listener, separate headers, and safe logs**

In `Application.from_config`, pass `config.device_pairing_settings` into `LocalAuthService`. Change `Application.issue_session` to name the two header inputs and forward them unchanged:

```python
def issue_session(
    self,
    remote_address: str,
    presented_owner_bootstrap_token: str | None,
    presented_device_bootstrap_token: str | None,
) -> dict[str, Any]:
    return self.auth.issue_session(
        remote_address,
        presented_owner_bootstrap_token,
        presented_device_bootstrap_token,
    )
```

Import `socket` and change `ApplicationServer` so it selects `socket.AF_INET6` before `ThreadingHTTPServer.__init__` whenever the validated host is an IPv6 ULA. Its `server_bind` sets `IPV6_V6ONLY=1` before binding; this prevents an IPv4-mapped connection from bypassing the address-family policy. Add an IPv6 ULA constructor test asserting `server.address_family == socket.AF_INET6` without relying on an externally routable LAN interface.

In `create_server`, instantiate `ApplicationServer` first and only when `validated.device_pairing_enabled` build a standard-library server context:

```python
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.minimum_version = ssl.TLSVersion.TLSv1_2
context.load_cert_chain(str(validated.device_tls_cert_file), str(validated.device_tls_key_file))
server.socket = context.wrap_socket(server.socket, server_side=True)
server.is_tls = True
```

Set `is_tls = False` for ordinary loopback servers. Do not introduce proxy protocol or forwarded-header processing. In the session route, pass `self.client_address[0]`, `self.headers.get("X-Local-Owner-Token")`, and `self.headers.get("X-Dev-Device-Bootstrap-Token")` separately. Keep `Access-Control-Allow-Headers` unchanged.

Replace `log_message` and generic exception logging with a private structured logger that receives only `method=self.command`, a normalized path with query removed, `status`, `peer_class` (`loopback`, `private_lan`, or `other`), and generated `diagnostic_id`. Do not interpolate exception objects, `self.path`, request headers, request bodies, or `address_string()` into log messages. Update `_error` to emit the same allowlisted fields and response diagnostic ID. Update `__main__` to print only `http` or `https`, host, port, and no certificate/token path.

- [ ] **Step 4: Run focused transport, logging, and existing HTTP regression tests to green**

Run:

```powershell
python -m unittest tests.integration.test_http_device_pairing tests.unit.test_http_logging tests.integration.test_http_api -v
```

Expected: PASS. Existing loopback session bootstrap and exact CORS behavior remain passing.

- [ ] **Step 5: Commit transport and logging changes**

```powershell
git add src/server/application.py src/server/http.py src/server/__main__.py tests/integration/test_http_device_pairing.py tests/unit/test_http_logging.py
git commit -m "feat: add direct TLS development device pairing"
```

## Task 4: Document Secure Development Pairing

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/unit/test_server_config.py`

- [ ] **Step 1: Write failing template assertions**

Extend `test_env_template_exposes_disabled_raw_retention_setting` with exact safe examples:

```python
self.assertIn("PAST_PARTNER_DEV_DEVICE_BOOTSTRAP_TOKEN=", template)
self.assertIn("PAST_PARTNER_DEV_DEVICE_ALLOWED_NETWORKS=192.168.1.42/32", template)
self.assertIn("PAST_PARTNER_DEV_DEVICE_TLS_CERT_FILE=", template)
self.assertIn("PAST_PARTNER_DEV_DEVICE_TLS_KEY_FILE=", template)
```

- [ ] **Step 2: Run the template test and observe red**

Run:

```powershell
python -m unittest tests.unit.test_server_config.ServerConfigTests.test_env_template_exposes_disabled_raw_retention_setting -v
```

Expected: FAIL because device pairing variables are undocumented.

- [ ] **Step 3: Add safe operator documentation**

Add commented blank values in `.env.example`; retain no live secret. In `README.md`, document:

```text
Development physical device: direct TLS to an explicit RFC1918/ULA server IP, certificate IP SAN must match exactly, and use /32 or /128 allowlists by default.
Simulator/USB: only http://127.0.0.1 or http://[::1] through forwarding that preserves the backend loopback peer; no pairing header.
Never use a reverse proxy, public address, broad CIDR, production mode, HTTP physical-device URL, or a Release mobile build for pairing.
```

Include a token-generation example that writes only to the developer terminal, such as `python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"`, and state that the device token must differ from the owner bootstrap token. Do not document CORS changes or suggest putting a token in query parameters.

- [ ] **Step 4: Run documentation-adjacent tests to green**

Run:

```powershell
python -m unittest tests.unit.test_server_config -v
```

Expected: PASS, with no token present in either changed document.

- [ ] **Step 5: Commit pairing documentation**

```powershell
git add .env.example README.md tests/unit/test_server_config.py
git commit -m "docs: explain secure development device pairing"
```

## Task 5: Create the Flutter Project and Lock Release Transport Policy

**Files:**
- Create: `mobile/` generated Flutter Android/iOS project tree
- Modify: `mobile/pubspec.yaml`
- Create: `mobile/analysis_options.yaml`
- Modify: `mobile/android/app/src/main/AndroidManifest.xml`
- Create: `mobile/android/app/src/debug/AndroidManifest.xml`
- Modify: `mobile/ios/Runner/Info.plist`
- Create: `mobile/test/release_transport_contract_test.dart`

- [ ] **Step 1: Generate the Flutter runners only after Task 0 passes**

Run from repository root:

```powershell
flutter create --platforms=android,ios --org com.pastpartner --project-name past_partner mobile
```

Expected: a standard Flutter project exists at `mobile/` with Android and iOS runner files. Do not add a web or desktop target in this task.

- [ ] **Step 2: Write failing static transport-policy tests**

Create `mobile/test/release_transport_contract_test.dart`:

```dart
test('release Android manifest forbids cleartext traffic', () {
  final manifest = File('android/app/src/main/AndroidManifest.xml').readAsStringSync();
  expect(manifest, contains('android:usesCleartextTraffic="false"'));
});

test('release iOS plist contains no ATS exception', () {
  final plist = File('ios/Runner/Info.plist').readAsStringSync();
  expect(plist, isNot(contains('NSAllowsArbitraryLoads')));
  expect(plist, isNot(contains('NSExceptionDomains')));
});
```

Add tests that a debug-only manifest is the only place cleartext is enabled and that no source file outside test fixtures contains `X-Dev-Device-Bootstrap-Token` until Task 6 introduces the Debug transport client.

- [ ] **Step 3: Run static tests and observe red**

Run:

```powershell
Set-Location mobile
flutter test test/release_transport_contract_test.dart
```

Expected: FAIL until manifest and plist transport policy is explicit.

- [ ] **Step 4: Configure project dependencies and platform files**

Use only these direct dependencies in `mobile/pubspec.yaml`:

```yaml
dependencies:
  flutter:
    sdk: flutter
  http: ^1.2.2
  flutter_secure_storage: ^9.2.2
  shared_preferences: ^2.3.2
dev_dependencies:
  flutter_test:
    sdk: flutter
  flutter_lints: ^5.0.0
```

Set the base Android manifest to `android:usesCleartextTraffic="false"`. Create a Debug manifest that sets cleartext true solely so app-level endpoint validation can permit loopback forwarding; the Dart endpoint parser must reject all non-loopback HTTP values. Keep `ios/Runner/Info.plist` free of `NSAppTransportSecurity` overrides, `NSAllowsArbitraryLoads`, and `NSExceptionDomains`. Add strict lints in `analysis_options.yaml` and use only Flutter `Icons`/`CupertinoIcons` for app controls.

- [ ] **Step 5: Run dependency resolution, transport checks, and static analysis to green**

Run:

```powershell
Set-Location mobile
flutter pub get
flutter test test/release_transport_contract_test.dart
flutter analyze
```

Expected: PASS. The project has no Release cleartext or ATS exception.

- [ ] **Step 6: Commit the mobile project boundary**

```powershell
git add mobile
git commit -m "feat: scaffold Flutter mobile application"
```

## Task 6: Implement Endpoint Validation, Pairing, and Secure Session Restore

**Files:**
- Create: `mobile/lib/app/past_partner_app.dart`
- Create: `mobile/lib/app/app_dependencies.dart`
- Create: `mobile/lib/core/config/api_endpoint.dart`
- Create: `mobile/lib/core/config/build_policy.dart`
- Create: `mobile/lib/core/network/api_client.dart`
- Create: `mobile/lib/core/network/api_failure.dart`
- Create: `mobile/lib/core/session/session.dart`
- Create: `mobile/lib/core/session/session_store.dart`
- Create: `mobile/lib/core/session/secure_session_store.dart`
- Create: `mobile/lib/core/session/session_controller.dart`
- Create: `mobile/lib/features/connection/connection_screen.dart`
- Create: `mobile/test/core/config/api_endpoint_test.dart`
- Create: `mobile/test/core/session/session_controller_test.dart`
- Create: `mobile/test/features/connection/connection_screen_test.dart`

- [ ] **Step 1: Write failing endpoint and session-controller tests**

Define the public types in the tests first:

```dart
test('debug accepts only exact private-IP https and loopback forwarding', () {
  expect(ApiEndpoint.parseDebug('https://192.168.50.7:8443').isPhysicalDevice, isTrue);
  expect(ApiEndpoint.parseDebug('http://127.0.0.1:8080').isLoopbackForwarding, isTrue);
  expect(() => ApiEndpoint.parseDebug('http://192.168.50.7:8080'), throwsFormatException);
  expect(() => ApiEndpoint.parseDebug('https://api.example.test'), throwsFormatException);
  expect(() => ApiEndpoint.parseRelease('http://127.0.0.1:8080'), throwsFormatException);
});

test('401 clears only the secure session and returns pairing required', () async {
  final store = InMemorySessionStore();
  final controller = SessionController(store, FakeApiClient(unauthorizedOnProbe: true));
  await store.write(const Session(accessToken: 'bearer', ownerId: 'owner', expiresAt: future));
  await controller.restore();
  expect(controller.state, SessionState.pairingRequired);
  expect(await store.read(), isNull);
});
```

Cover parser rejection of hostnames, userinfo, query, fragment, redirects, public/private-invalid categories, IPv4-mapped IPv6, and non-default Release override. Cover controller transitions: `starting`, `restoringSession`, `connected`, `pairingRequired`, `pairingInProgress`, and `pairingError`; ensure the pairing token never reaches `SessionStore.write` or any user-visible error string.

- [ ] **Step 2: Run Dart tests and observe red**

Run:

```powershell
Set-Location mobile
flutter test test/core/config/api_endpoint_test.dart test/core/session/session_controller_test.dart
```

Expected: FAIL because no application classes exist.

- [ ] **Step 3: Implement typed endpoint, client, storage, and state contracts**

Make `ApiEndpoint` immutable and parse with `Uri.parse`, accepting exactly:

```dart
sealed class EndpointKind { const EndpointKind(); }
final class PhysicalDeviceEndpoint extends EndpointKind { const PhysicalDeviceEndpoint(); }
final class LoopbackForwardingEndpoint extends EndpointKind { const LoopbackForwardingEndpoint(); }
```

`ApiEndpoint.parseDebug` accepts a literal RFC1918 IPv4 or ULA IPv6 `https` host without user info/query/fragment for physical devices, or `http` only for literal `127.0.0.1` / `::1`. `ApiEndpoint.parseRelease` rejects every supplied override and exposes no pairing configuration. Disable redirects in `ApiClient` using `http.Request(...).followRedirects = false` and reject any 3xx response before a credential can be followed to another origin.

Define `Session`, `SessionStore`, and `SecureSessionStore`. Its only keys are `past_partner.access_token`, `past_partner.owner_id`, and `past_partner.expires_at`; it uses `FlutterSecureStorage`. Define a test-only `InMemorySessionStore` inside tests. `SessionController` is a `ChangeNotifier`; it discards expired or unauthorized sessions and calls authenticated `GET /api/v1/personas` to validate a stored bearer. It sends `X-Dev-Device-Bootstrap-Token` only from the Debug physical-device pairing request, keeps it in a local method variable, and wipes the form/controller after completion or error. It sends no device header for loopback forwarding.

Create the Debug `ConnectionScreen` with obscured pairing-token input, a visible endpoint format error, an explicit connect button, disabled/loading state, and semantic labels. In Release it renders a short non-interactive development-connection-unavailable message and has no endpoint or token fields. Use `SafeArea`, 48dp button targets, `Semantics`, and `MediaQuery.viewInsets` instead of imitating a keyboard.

- [ ] **Step 4: Run unit/widget tests, analysis, and verify source redaction**

Run:

```powershell
Set-Location mobile
flutter test test/core/config/api_endpoint_test.dart test/core/session/session_controller_test.dart test/features/connection/connection_screen_test.dart
flutter analyze
rg -n --glob '!test/**' 'access_token|X-Dev-Device-Bootstrap-Token|pairing token' lib
```

Expected: tests and analysis PASS. Review the `rg` output manually: only the narrowly scoped client header constant and model field names are present; there is no log, toast, exception interpolation, preference key, or Release UI carrying a pairing token.

- [ ] **Step 5: Commit connection and session foundation**

```powershell
git add mobile/lib mobile/test
git commit -m "feat: add Flutter development connection and secure sessions"
```

## Task 7: Build the Two Static Conversation Appearance Systems

**Files:**
- Create: `mobile/lib/features/appearance/conversation_appearance.dart`
- Create: `mobile/lib/features/appearance/appearance_store.dart`
- Create: `mobile/lib/features/appearance/appearance_controller.dart`
- Create: `mobile/lib/features/appearance/appearance_settings_screen.dart`
- Create: `mobile/lib/features/appearance/conversation_preview_screen.dart`
- Create: `mobile/lib/features/appearance/widgets/calm_conversation_scaffold.dart`
- Create: `mobile/lib/features/appearance/widgets/bright_conversation_scaffold.dart`
- Modify: `mobile/lib/app/past_partner_app.dart`
- Create: `mobile/test/features/appearance/appearance_controller_test.dart`
- Create: `mobile/test/features/appearance/conversation_preview_screen_test.dart`

- [ ] **Step 1: Write failing preference and preview widget tests**

Use a fake `AppearanceStore` and assert immediate preference persistence and both visual systems:

```dart
testWidgets('selecting lively conversation persists and swaps preview scaffold', (tester) async {
  final controller = AppearanceController(FakeAppearanceStore());
  await tester.pumpWidget(PastPartnerApp(appearanceController: controller));
  await tester.tap(find.bySemanticsLabel('Lively conversation'));
  await tester.pumpAndSettle();
  expect(controller.appearance, ConversationAppearance.lively);
  expect(find.byType(BrightConversationScaffold), findsOneWidget);
});

testWidgets('calm composer expands panels without network side effects', (tester) async {
  await tester.pumpWidget(const CalmConversationScaffold());
  await tester.tap(find.bySemanticsLabel('More actions'));
  await tester.pump();
  expect(find.bySemanticsLabel('More actions panel'), findsOneWidget);
  expect(find.text('No network action'), findsNothing);
});
```

Test both 375dp phone and 1024dp tablet constraints; text-scale factor 2.0; a visible `SafeArea`; semantics for navigation, expression, more, and voice controls; 48dp-sized controls; keyboard-aware composer; and toggling expression/more/voice preview states without HTTP requests.

- [ ] **Step 2: Run visual tests and observe red**

Run:

```powershell
Set-Location mobile
flutter test test/features/appearance/appearance_controller_test.dart test/features/appearance/conversation_preview_screen_test.dart
```

Expected: FAIL because appearance types, preference storage, and preview widgets do not exist.

- [ ] **Step 3: Implement the non-sensitive preference boundary**

Define exactly two values:

```dart
enum ConversationAppearance { simplified, lively }
```

`AppearanceStore` reads/writes only the string key `past_partner.conversation_appearance` through `SharedPreferences`; it contains no persona, message, session, token, or API data. `AppearanceController` restores `simplified` by default, persists a user selection, then calls `notifyListeners()` once. The settings labels are `Simplified conversation` and `Lively conversation`; do not display third-party application names in production UI.

- [ ] **Step 4: Implement reusable static previews with the accepted hierarchy**

`CalmConversationScaffold` uses a neutral gray canvas, centered title, left back control, compact right detail control, narrow centered time divider, right-aligned soft-green example bubble, and a composer containing voice, text input, expression, and more controls. Its expandable state is either a dense original expression grid or a four-column utility grid with non-actionable original generic labels; no payment, transfer, gift, call, location, or media action is shown.

`BrightConversationScaffold` uses a white/cool-gray canvas, leading title, a right menu control, pale-blue rounded outgoing bubble, rounded input, blue send affordance, and compact quick-action rail. Its expansion states are categorized original expression geometry, an explicit voice-preview surface, or a four-column generic utility grid. Do not render a fake operating-system keyboard, copied screenshots, third-party logos, avatars, stickers, names, mini-program cards, copyrighted content, or external images. Use `Icons`/`CupertinoIcons` only and neutral `Container` illustrations.

Both widgets must take their preview state via typed callbacks, use `AnimatedSize` or 150-300ms opacity changes without layout-shifting controls, respect `SafeArea` and `MediaQuery.viewInsets`, make every interactive region at least 48dp, and expose semantic labels. The parent preview screen owns state so neither scaffold starts an HTTP client or receives a `SessionController`.

- [ ] **Step 5: Run visual verification to green**

Run:

```powershell
Set-Location mobile
flutter test test/features/appearance/appearance_controller_test.dart test/features/appearance/conversation_preview_screen_test.dart
flutter analyze
```

Expected: PASS at both compact-phone and tablet test sizes, with no overflow and no network dependency.

- [ ] **Step 6: Commit switchable conversation visuals**

```powershell
git add mobile/lib/features/appearance mobile/test/features/appearance
git commit -m "feat: add switchable static conversation previews"
```

## Task 8: End-to-End Regression, Builds, and Human Acceptance Package

**Files:**
- Modify: `README.md` only if a test command or platform prerequisite changed during execution
- Test: full Python and Flutter suites; Android artifacts; optional macOS iOS job

- [ ] **Step 1: Run the full backend regression suite**

Run from repository root:

```powershell
npm test
```

Expected: all Python unit/integration tests and web workspace tests PASS. Investigate any unexpected failure with `systematic-debugging` before changing implementation.

- [ ] **Step 2: Run the full Flutter static and widget suite**

Run:

```powershell
Set-Location mobile
flutter analyze
flutter test
```

Expected: PASS with no analyzer diagnostics.

- [ ] **Step 3: Build and inspect Android Release policy**

Run:

```powershell
Set-Location mobile
flutter build apk --debug
flutter build apk --release
```

Expected: both APK builds succeed. Verify the generated Release merged manifest still contains `android:usesCleartextTraffic="false"` and no `networkSecurityConfig` that permits cleartext. Capture exact artifact paths, but do not commit generated APKs.

- [ ] **Step 4: Validate direct-TLS and UI acceptance flows**

Perform these manual checks on the supported environment:

1. Start a loopback backend with no pairing variables; ensure the existing session endpoint works and the Flutter Debug loopback connection succeeds only via forwarding.
2. Start a development backend with an explicit private IP, `/32` or `/128` peer allowlist, CA-trusted IP-SAN certificate, and distinct high-entropy device/owner tokens; ensure the Flutter physical-device connection succeeds through direct HTTPS.
3. Attempt a physical `http://` URL, a DNS name, a public address, a wrong certificate, a redirect, an incorrect token, and a peer outside the allowlist; every attempt must fail without a token/header/body log leak.
4. Change the device token, restart the backend, confirm device session restore returns to pairing, and confirm a loopback session remains valid.
5. In Flutter settings, switch Simplified/Lively previews, inspect chat/expression/more/voice geometry, focus the real input to show the platform keyboard, rotate to landscape, and increase text scale. Confirm no control overlaps or reaches under system insets.

- [ ] **Step 5: Run an iOS source-policy check and state the Windows build limit**

Run:

```powershell
Set-Location mobile
flutter test test/release_transport_contract_test.dart
```

Expected: PASS. On Windows, record iOS binary build as not run. On macOS, run `flutter build ios --no-codesign` and use its output as the only evidence for iOS compilation.

- [ ] **Step 6: Request code review, then commit final verification-only changes**

Run a review focused on: device-to-owner token separation, IP/CIDR canonicalization, direct peer and TLS enforcement, session rotation/revocation, log redaction, Release source policy, token persistence, and visual/accessibility scope. Apply only confirmed findings, rerun the affected focused tests and all checks from Steps 1-5, then commit any resulting documentation or test-only changes. Inspect the changed file list first and stage only P3-01 files; never stage unrelated worktree changes:

```powershell
git status --short
git add -- README.md tests mobile src/server src/services .env.example
git commit -m "test: verify P3-01 mobile foundation"
```

Only create this commit when there are actual post-review changes; otherwise leave the existing task commits unchanged.

## Completion and Handoff

After every task commit, keep the feature branch unmerged until the user accepts the P3-01 result. Present the user with:

1. The exact branch and commit range.
2. Python, Flutter, Android, and iOS verification evidence, separated by what actually ran.
3. A short screen recording or screenshots of both static themes on compact and wide sizes when Flutter tooling is available.
4. The one remaining platform limitation, if Windows prevented the iOS build.

Only after explicit user acceptance: merge the feature branch into `main`, run the full regression suite on `main`, push `origin/main`, and report the successful remote commit. Do not push an unaccepted feature branch as a replacement for this flow.
