import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/features/consents/consent.dart';
import 'package:past_partner/features/consents/consent_controller.dart';
import 'package:past_partner/features/consents/consent_gateway.dart';
import 'package:past_partner/features/consents/consent_screen.dart';
import 'package:past_partner/features/models/model_option.dart';

class _ScreenGateway implements ConsentGateway {
  List<Consent> items = <Consent>[];

  @override
  Future<List<Consent>> list({
    required ApiEndpoint endpoint,
    required Session session,
    required String personaId,
  }) async =>
      List<Consent>.of(items);

  @override
  Future<Consent> create({
    required ApiEndpoint endpoint,
    required Session session,
    required ConsentDraft draft,
  }) async {
    final Consent created = Consent(
      id: 'consent-created',
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
  }) async =>
      items.singleWhere((Consent item) => item.id == consentId).revoke(
            '2026-08-14T01:00:00+00:00',
          );
}

void main() {
  testWidgets('creates consent with selected model and revokes it',
      (WidgetTester tester) async {
    final _ScreenGateway gateway = _ScreenGateway();
    final ConsentController controller = ConsentController(
      endpoint: ApiEndpoint.parseDebug('http://127.0.0.1:8080'),
      session: Session(
        accessToken: 'token',
        ownerId: 'owner',
        expiresAt: DateTime.utc(2099),
      ),
      personaId: 'persona-1',
      gateway: gateway,
    );
    const ModelOption model = ModelOption(
      providerId: 'deepseek',
      providerName: 'DeepSeek',
      id: 'deepseek-v4-flash',
      displayName: 'DeepSeek V4 Flash',
      capabilities: <String>['chat', 'vision'],
      pricing: ModelPricing(),
    );

    await tester.pumpWidget(MaterialApp(
      home: ConsentScreen(
        personaName: '小雅',
        controller: controller,
        selectedModel: model,
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text('还没有授权'), findsOneWidget);
    await tester.tap(find.byKey(const Key('consent-create')));
    await tester.pumpAndSettle();
    expect(find.textContaining('DeepSeek V4 Flash'), findsWidgets);
    await tester.enterText(find.byKey(const Key('consent-purpose')), '图片理解');
    await tester.tap(find.byKey(const Key('consent-submit')));
    await tester.pumpAndSettle();

    expect(find.textContaining('图片理解'), findsOneWidget);
    await tester.tap(find.byKey(const Key('consent-revoke-consent-created')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('consent-confirm-revoke')));
    await tester.pumpAndSettle();
    expect(find.text('已撤回'), findsOneWidget);
  });

  testWidgets('blocks creation when no model is selected',
      (WidgetTester tester) async {
    final ConsentController controller = ConsentController(
      endpoint: ApiEndpoint.parseDebug('http://127.0.0.1:8080'),
      session: Session(
        accessToken: 'token',
        ownerId: 'owner',
        expiresAt: DateTime.utc(2099),
      ),
      personaId: 'persona-1',
      gateway: _ScreenGateway(),
    );
    await tester.pumpWidget(MaterialApp(
      home: ConsentScreen(
        personaName: '小雅',
        controller: controller,
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text('请先选择模型，再创建授权。'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const Key('consent-create')))
          .onPressed,
      isNull,
    );
  });
}
