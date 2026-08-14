import 'package:flutter/foundation.dart';

import '../../core/config/api_endpoint.dart';
import '../../core/network/api_failure.dart';
import '../../core/session/session.dart';
import 'consent.dart';
import 'consent_gateway.dart';

enum ConsentState { idle, loading, ready, saving, error }

class ConsentController extends ChangeNotifier {
  ConsentController({
    required this.endpoint,
    required this.session,
    required this.personaId,
    required this.gateway,
  });

  final ApiEndpoint endpoint;
  final Session session;
  final String personaId;
  final ConsentGateway gateway;

  ConsentState state = ConsentState.idle;
  List<Consent> consents = const <Consent>[];
  String? errorMessage;

  Future<void> load() async {
    if (state == ConsentState.loading) return;
    state = ConsentState.loading;
    errorMessage = null;
    notifyListeners();
    try {
      final List<Consent> loaded = await gateway.list(
        endpoint: endpoint,
        session: session,
        personaId: personaId,
      );
      consents = loaded;
      state = ConsentState.ready;
    } catch (_) {
      state = ConsentState.error;
      errorMessage = '授权列表加载失败，请重试。';
    }
    notifyListeners();
  }

  Future<Consent?> create(ConsentDraft draft) async {
    if (state == ConsentState.saving) return null;
    state = ConsentState.saving;
    errorMessage = null;
    notifyListeners();
    try {
      final Consent created = await gateway.create(
        endpoint: endpoint,
        session: session,
        draft: draft,
      );
      consents = <Consent>[...consents, created];
      state = ConsentState.ready;
      notifyListeners();
      return created;
    } catch (error) {
      state = ConsentState.error;
      errorMessage = _createError(error);
      notifyListeners();
      return null;
    }
  }

  Future<Consent?> revoke(Consent consent) async {
    if (consent.status == ConsentStatus.revoked ||
        state == ConsentState.saving) {
      return consent;
    }
    state = ConsentState.saving;
    errorMessage = null;
    notifyListeners();
    try {
      final Consent revoked = await gateway.revoke(
        endpoint: endpoint,
        session: session,
        consentId: consent.id,
      );
      consents = consents
          .map((Consent item) => item.id == revoked.id ? revoked : item)
          .toList(growable: false);
      state = ConsentState.ready;
      notifyListeners();
      return revoked;
    } catch (_) {
      state = ConsentState.error;
      errorMessage = '撤回授权失败，请重试。';
      notifyListeners();
      return null;
    }
  }

  static String _createError(Object error) {
    if (error is ApiFailure && error.code == 'consent_exists') {
      return '已有相同授权，请勿重复创建。';
    }
    return '授权创建失败，请确认范围后重试。';
  }
}
