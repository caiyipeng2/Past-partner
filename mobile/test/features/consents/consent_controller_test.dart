import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/features/consents/consent.dart';
import 'package:past_partner/features/consents/consent_controller.dart';
import 'package:past_partner/features/consents/consent_gateway.dart';

class _ControllerGateway implements ConsentGateway {
  _ControllerGateway(this.items);

  List<Consent> items;
  bool failLoad = false;
  bool failCreate = false;

  @override
  Future<List<Consent>> list({
    required ApiEndpoint endpoint,
    required Session session,
    required String personaId,
  }) async {
    if (failLoad) throw StateError('offline');
    return List<Consent>.of(items);
  }

  @override
  Future<Consent> create({
    required ApiEndpoint endpoint,
    required Session session,
    required ConsentDraft draft,
  }) async {
    if (failCreate) throw StateError('duplicate');
    final Consent created = Consent(
      id: 'consent-2',
      personaId: draft.personaId,
      providerId: draft.providerId,
      modelId: draft.modelId,
      dataCategory: draft.dataCategory,
      estimatedCost: draft.estimatedCost,
      purpose: draft.purpose,
      authorizationScope: draft.authorizationScope,
      createdAt: '2026-08-14T00:00:00+00:00',
    );
    items = <Consent>[...items, created];
    return created;
  }

  @override
  Future<Consent> revoke({
    required ApiEndpoint endpoint,
    required Session session,
    required String consentId,
  }) async {
    final Consent current =
        items.singleWhere((Consent item) => item.id == consentId);
    final Consent revoked = current.revoke('2026-08-14T01:00:00+00:00');
    items = items
        .map((Consent item) => item.id == consentId ? revoked : item)
        .toList();
    return revoked;
  }
}

Consent _activeConsent() => const Consent(
      id: 'consent-1',
      personaId: 'persona-1',
      providerId: 'deepseek',
      modelId: 'deepseek-v4-flash',
      dataCategory: ConsentDataCategory.image,
      estimatedCost: 0.12,
      purpose: '图片理解',
      authorizationScope: 'persona-image-analysis',
      createdAt: '2026-08-14T00:00:00+00:00',
    );

void main() {
  final ApiEndpoint endpoint = ApiEndpoint.parseDebug('http://127.0.0.1:8080');
  final Session session = Session(
    accessToken: 'token',
    ownerId: 'owner',
    expiresAt: DateTime.utc(2099),
  );

  test('loads, creates, and revokes without losing list state', () async {
    final _ControllerGateway gateway =
        _ControllerGateway(<Consent>[_activeConsent()]);
    final ConsentController controller = ConsentController(
      endpoint: endpoint,
      session: session,
      personaId: 'persona-1',
      gateway: gateway,
    );

    await controller.load();
    expect(controller.state, ConsentState.ready);
    expect(controller.consents, hasLength(1));
    await controller.create(const ConsentDraft(
      personaId: 'persona-1',
      providerId: 'deepseek',
      modelId: 'deepseek-v4-flash',
      dataCategory: ConsentDataCategory.audio,
      estimatedCost: 0,
      purpose: '音频转写',
      authorizationScope: 'persona-audio-transcription',
    ));
    expect(controller.consents, hasLength(2));
    await controller.revoke(controller.consents.first);
    expect(controller.consents.first.status, ConsentStatus.revoked);
  });

  test('keeps existing items visible when refresh fails', () async {
    final _ControllerGateway gateway =
        _ControllerGateway(<Consent>[_activeConsent()])..failLoad = true;
    final ConsentController controller = ConsentController(
      endpoint: endpoint,
      session: session,
      personaId: 'persona-1',
      gateway: gateway,
    );

    await controller.load();
    expect(controller.state, ConsentState.error);
    expect(controller.consents, isEmpty);
    expect(controller.errorMessage, isNotNull);
  });
}
