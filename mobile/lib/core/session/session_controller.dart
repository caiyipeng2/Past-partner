import 'package:flutter/foundation.dart';

import '../config/api_endpoint.dart';
import '../network/api_client.dart';
import '../network/api_failure.dart';
import 'session.dart';
import 'session_store.dart';

enum SessionState {
  starting,
  restoringSession,
  disconnected,
  connected,
  pairingRequired,
  pairingInProgress,
  pairingError
}

class SessionController extends ChangeNotifier {
  SessionController(this.store, this.client);

  final SessionStore store;
  final ApiClient client;
  SessionState state = SessionState.starting;
  Session? session;
  ApiEndpoint? endpoint;
  String? errorMessage;

  Future<void> restore(ApiEndpoint configuredEndpoint) async {
    endpoint = configuredEndpoint;
    session = null;
    state = SessionState.restoringSession;
    errorMessage = null;
    notifyListeners();
    final Session? saved;
    try {
      saved = await store.read();
    } on Object {
      state = SessionState.pairingError;
      errorMessage = '本地会话读取失败，请重试。';
      notifyListeners();
      return;
    }
    if (saved == null || saved.isExpired) {
      try {
        await store.clear();
      } on Object {
        state = SessionState.pairingError;
        errorMessage = '本地会话清理失败，请重试。';
        notifyListeners();
        return;
      }
      state = SessionState.pairingRequired;
      notifyListeners();
      return;
    }
    try {
      await client.probe(configuredEndpoint, saved);
      session = saved;
      state = SessionState.connected;
    } on ApiFailure catch (failure) {
      if (failure.isUnauthorized) {
        try {
          await store.clear();
        } on Object {
          state = SessionState.pairingError;
          errorMessage = '本地会话清理失败，请重试。';
          notifyListeners();
          return;
        }
      }
      state = failure.isUnauthorized
          ? SessionState.pairingRequired
          : SessionState.pairingError;
      errorMessage = failure.isUnauthorized
          ? null
          : 'Connection could not be established.';
    } catch (_) {
      state = SessionState.pairingError;
      errorMessage = 'Connection could not be established.';
    }
    notifyListeners();
  }

  /// Re-establishes a session through the already validated endpoint.
  ///
  /// Pairing tokens are intentionally accepted only for this call. They are
  /// never copied into [Session] or any persistent store.
  Future<bool> refresh({required String pairingToken}) async {
    final ApiEndpoint? configured = endpoint;
    if (configured == null) {
      state = SessionState.pairingRequired;
      errorMessage = '请先连接本地服务。';
      notifyListeners();
      return false;
    }
    return pair(configured.uri.toString(), pairingToken);
  }

  Future<bool> pair(String rawEndpoint, String pairingToken) async {
    state = SessionState.pairingInProgress;
    errorMessage = null;
    notifyListeners();
    try {
      final ApiEndpoint configured = ApiEndpoint.parseDebug(rawEndpoint);
      final Session paired = await client.pair(
        configured,
        deviceToken: configured.isPhysicalDevice ? pairingToken : null,
      );
      await store.write(paired);
      endpoint = configured;
      session = paired;
      state = SessionState.connected;
      return true;
    } on FormatException catch (error) {
      state = SessionState.pairingError;
      errorMessage = error.message;
    } on ApiFailure catch (failure) {
      state = failure.isUnauthorized
          ? SessionState.pairingRequired
          : SessionState.pairingError;
      errorMessage = failure.isUnauthorized
          ? null
          : 'Connection could not be established.';
    } catch (_) {
      state = SessionState.pairingError;
      errorMessage = 'Connection could not be established.';
    } finally {
      notifyListeners();
    }
    return false;
  }

  Future<void> clear() async {
    await store.clear();
    session = null;
    state = SessionState.pairingRequired;
    notifyListeners();
  }
}
