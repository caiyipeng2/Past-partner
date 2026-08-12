import 'package:flutter/foundation.dart';

import '../config/api_endpoint.dart';
import '../network/api_client.dart';
import '../network/api_failure.dart';
import 'session.dart';
import 'session_store.dart';

enum SessionState { starting, restoringSession, disconnected, connected, pairingRequired, pairingInProgress, pairingError }

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
    state = SessionState.restoringSession;
    errorMessage = null;
    notifyListeners();
    final Session? saved = await store.read();
    if (saved == null || saved.isExpired) {
      await store.clear();
      state = SessionState.pairingRequired;
      notifyListeners();
      return;
    }
    try {
      await client.probe(configuredEndpoint, saved);
      session = saved;
      state = SessionState.connected;
    } on ApiFailure catch (failure) {
      if (failure.isUnauthorized) await store.clear();
      state = failure.isUnauthorized ? SessionState.pairingRequired : SessionState.pairingError;
      errorMessage = failure.isUnauthorized ? null : 'Connection could not be established.';
    } catch (_) {
      state = SessionState.pairingError;
      errorMessage = 'Connection could not be established.';
    }
    notifyListeners();
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
      state = failure.isUnauthorized ? SessionState.pairingRequired : SessionState.pairingError;
      errorMessage = failure.isUnauthorized ? null : 'Connection could not be established.';
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
