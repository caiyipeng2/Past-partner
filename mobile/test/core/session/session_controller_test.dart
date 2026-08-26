import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/network/api_client.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/core/session/session_controller.dart';
import 'package:past_partner/core/session/session_store.dart';

class _Store implements SessionStore {
  _Store([this.value]);

  Session? value;
  int clearCalls = 0;
  int writeCalls = 0;
  bool failRead = false;
  bool failClear = false;

  @override
  Future<Session?> read() async {
    if (failRead) throw StateError('read failed');
    return value;
  }

  @override
  Future<void> write(Session session) async {
    writeCalls++;
    value = session;
  }

  @override
  Future<void> clear() async {
    if (failClear) throw StateError('clear failed');
    clearCalls++;
    value = null;
  }
}

void main() {
  final ApiEndpoint loopback = ApiEndpoint.parseDebug('http://127.0.0.1:8080');

  test('expired persisted session is cleared before any network probe',
      () async {
    final _Store store = _Store(Session(
      accessToken: 'expired-access',
      ownerId: 'owner',
      expiresAt: DateTime.utc(2020),
    ));
    int requestCount = 0;
    final ApiClient client = ApiClient(
      client: MockClient((http.Request request) async {
        requestCount++;
        return http.Response('{}', 200);
      }),
    );
    final SessionController controller = SessionController(store, client);

    await controller.restore(loopback);

    expect(controller.state, SessionState.pairingRequired);
    expect(controller.session, isNull);
    expect(store.clearCalls, 1);
    expect(requestCount, 0);
  });

  test('session store read failure becomes a stable pairing error', () async {
    final _Store store = _Store()..failRead = true;
    final SessionController controller = SessionController(store, ApiClient());

    await controller.restore(loopback);

    expect(controller.state, SessionState.pairingError);
    expect(controller.errorMessage, '本地会话读取失败，请重试。');
  });

  test('expired session cleanup failure is visible instead of being ignored',
      () async {
    final _Store store = _Store(Session(
      accessToken: 'expired-access',
      ownerId: 'owner',
      expiresAt: DateTime.utc(2020),
    ))
      ..failClear = true;
    final SessionController controller = SessionController(store, ApiClient());

    await controller.restore(loopback);

    expect(controller.state, SessionState.pairingError);
    expect(controller.errorMessage, '本地会话清理失败，请重试。');
  });

  test('unauthorized probe cleanup failure is visible instead of escaping',
      () async {
    final _Store store = _Store(Session(
      accessToken: 'stale-access',
      ownerId: 'owner',
      expiresAt: DateTime.utc(2099),
    ))
      ..failClear = true;
    final ApiClient client = ApiClient(
      client: MockClient((http.Request request) async {
        return http.Response(
          jsonEncode(<String, dynamic>{
            'error': <String, dynamic>{'code': 'authentication_required'},
          }),
          401,
        );
      }),
    );
    final SessionController controller = SessionController(store, client);

    await controller.restore(loopback);

    expect(controller.state, SessionState.pairingError);
    expect(controller.errorMessage, '本地会话清理失败，请重试。');
  });

  test(
      'refresh re-pairs through the stored endpoint without persisting the pairing token',
      () async {
    final _Store store = _Store(Session(
      accessToken: 'expired-access',
      ownerId: 'owner',
      expiresAt: DateTime.utc(2020),
    ));
    final ApiEndpoint endpoint =
        ApiEndpoint.parseDebug('https://192.168.1.20:8080');
    final ApiClient client = ApiClient(
      client: MockClient((http.Request request) async {
        expect(request.url.path, '/api/v1/auth/session');
        expect(request.headers['x-dev-device-bootstrap-token'], 'one-time');
        return http.Response(
          jsonEncode(<String, dynamic>{
            'access_token': 'new-access',
            'owner_id': 'owner',
            'expires_at': '2099-01-01T00:00:00Z',
          }),
          201,
        );
      }),
    );
    final SessionController controller = SessionController(store, client);
    await controller.restore(endpoint);

    final bool refreshed = await controller.refresh(pairingToken: 'one-time');

    expect(refreshed, isTrue);
    expect(controller.state, SessionState.connected);
    expect(controller.session?.accessToken, 'new-access');
    expect(store.writeCalls, 1);
    expect(store.value?.accessToken, isNot(contains('one-time')));
  });

  test('refresh requires an endpoint instead of inventing a pairing target',
      () async {
    final _Store store = _Store();
    final SessionController controller =
        SessionController(store, ApiClient(client: MockClient((_) async {
      fail('refresh must not make a request without an endpoint');
    })));

    final bool refreshed = await controller.refresh(pairingToken: 'ignored');

    expect(refreshed, isFalse);
    expect(controller.state, SessionState.pairingRequired);
    expect(controller.errorMessage, '请先连接本地服务。');
  });
}
