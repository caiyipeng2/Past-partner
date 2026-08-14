import '../../core/config/api_endpoint.dart';
import '../../core/network/api_client.dart';
import '../../core/session/session.dart';
import 'consent.dart';

abstract interface class ConsentGateway {
  Future<List<Consent>> list({
    required ApiEndpoint endpoint,
    required Session session,
    required String personaId,
  });

  Future<Consent> create({
    required ApiEndpoint endpoint,
    required Session session,
    required ConsentDraft draft,
  });

  Future<Consent> revoke({
    required ApiEndpoint endpoint,
    required Session session,
    required String consentId,
  });
}

class ApiClientConsentGateway implements ConsentGateway {
  const ApiClientConsentGateway(this.client);

  final ApiClient client;

  @override
  Future<List<Consent>> list({
    required ApiEndpoint endpoint,
    required Session session,
    required String personaId,
  }) async {
    final List<Map<String, dynamic>> values =
        await client.listConsents(endpoint, session, personaId);
    if (values.length > 2048) {
      throw const FormatException('Consent list is too large.');
    }
    return values.map((Map<String, dynamic> value) {
      final Consent consent = Consent.fromJson(value);
      if (consent.personaId != personaId) {
        throw const FormatException('Consent belongs to another persona.');
      }
      return consent;
    }).toList(growable: false);
  }

  @override
  Future<Consent> create({
    required ApiEndpoint endpoint,
    required Session session,
    required ConsentDraft draft,
  }) async {
    _validateDraft(draft);
    final Consent consent = Consent.fromJson(
      await client.createConsent(endpoint, session, draft.toJson()),
    );
    if (consent.personaId != draft.personaId ||
        consent.providerId != draft.providerId ||
        consent.modelId != draft.modelId ||
        consent.dataCategory != draft.dataCategory ||
        consent.authorizationScope != draft.authorizationScope) {
      throw const FormatException(
          'Created consent does not match the request.');
    }
    return consent;
  }

  @override
  Future<Consent> revoke({
    required ApiEndpoint endpoint,
    required Session session,
    required String consentId,
  }) async {
    if (consentId.trim().isEmpty || consentId.length > 128) {
      throw const FormatException('Invalid consent id.');
    }
    final Consent consent = Consent.fromJson(
      await client.revokeConsent(endpoint, session, consentId),
    );
    if (consent.id != consentId || consent.status != ConsentStatus.revoked) {
      throw const FormatException('Consent revocation response is invalid.');
    }
    return consent;
  }

  static void _validateDraft(ConsentDraft draft) {
    if (draft.personaId.trim().isEmpty || draft.personaId.length > 128) {
      throw const FormatException('Invalid consent persona.');
    }
    if (draft.providerId.trim().isEmpty || draft.providerId.length > 128) {
      throw const FormatException('Invalid consent provider.');
    }
    if (draft.modelId.trim().isEmpty || draft.modelId.length > 256) {
      throw const FormatException('Invalid consent model.');
    }
    if (!draft.estimatedCost.isFinite ||
        draft.estimatedCost < 0 ||
        draft.estimatedCost > 1000000000) {
      throw const FormatException('Invalid consent estimated cost.');
    }
    if (draft.purpose.trim().isEmpty || draft.purpose.length > 512) {
      throw const FormatException('Invalid consent purpose.');
    }
    if (draft.authorizationScope.trim().isEmpty ||
        draft.authorizationScope.length > 256) {
      throw const FormatException('Invalid consent scope.');
    }
  }
}
