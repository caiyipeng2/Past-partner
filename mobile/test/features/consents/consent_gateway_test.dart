import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/network/api_client.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/features/consents/consent.dart';
import 'package:past_partner/features/consents/consent_gateway.dart';

class _FakeApiClient extends ApiClient {
  _FakeApiClient(this.values);

  final List<Map<String, dynamic>> values;

  @override
  Future<List<Map<String, dynamic>>> listConsents(
    ApiEndpoint endpoint,
    Session session,
    String personaId,
  ) async =>
      values;

  @override
  Future<Map<String, dynamic>> createConsent(
    ApiEndpoint endpoint,
    Session session,
    Map<String, dynamic> payload,
  ) async =>
      values.single;

  @override
  Future<Map<String, dynamic>> revokeConsent(
    ApiEndpoint endpoint,
    Session session,
    String consentId,
  ) async =>
      <String, dynamic>{...values.single, 'status': 'revoked'};
}

void main() {
  final ApiEndpoint endpoint = ApiEndpoint.parseDebug('http://127.0.0.1:8080');
  final Session session = Session(
    accessToken: 'token',
    ownerId: 'owner',
    expiresAt: DateTime.utc(2099),
  );

  test('decodes bounded consent metadata and sends a typed draft', () async {
    final Map<String, dynamic> value = <String, dynamic>{
      'id': 'consent-1',
      'persona_id': 'persona-1',
      'provider_id': 'deepseek',
      'model_id': 'deepseek-v4-flash',
      'data_category': 'image',
      'estimated_cost': 0.12,
      'purpose': '图片理解',
      'authorization_scope': 'persona-image-analysis',
      'created_at': '2026-08-14T00:00:00+00:00',
      'status': 'active',
    };
    final ConsentGateway gateway = ApiClientConsentGateway(
      _FakeApiClient(<Map<String, dynamic>>[value]),
    );

    final List<Consent> listed = await gateway.list(
      endpoint: endpoint,
      session: session,
      personaId: 'persona-1',
    );
    final Consent created = await gateway.create(
      endpoint: endpoint,
      session: session,
      draft: const ConsentDraft(
        personaId: 'persona-1',
        providerId: 'deepseek',
        modelId: 'deepseek-v4-flash',
        dataCategory: ConsentDataCategory.image,
        estimatedCost: 0.12,
        purpose: '图片理解',
        authorizationScope: 'persona-image-analysis',
      ),
    );

    expect(listed.single.id, 'consent-1');
    expect(listed.single.dataCategory, ConsentDataCategory.image);
    expect(created.providerId, 'deepseek');
    expect(created.status, ConsentStatus.active);
  });

  test('rejects malformed consent lists instead of creating partial records',
      () async {
    final ConsentGateway gateway = ApiClientConsentGateway(
      _FakeApiClient(<Map<String, dynamic>>[
        <String, dynamic>{'id': 'missing-fields'},
      ]),
    );

    expect(
      () => gateway.list(
        endpoint: endpoint,
        session: session,
        personaId: 'persona-1',
      ),
      throwsFormatException,
    );
  });
}
