import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/features/models/model_controller.dart';
import 'package:past_partner/features/models/model_gateway.dart';
import 'package:past_partner/features/models/model_option.dart';
import 'package:past_partner/features/models/model_selection_screen.dart';

class _ScreenGateway implements ModelGateway {
  @override
  Future<List<ModelOption>> list({
    required ApiEndpoint endpoint,
    required Session session,
    String? providerId,
  }) async =>
      <ModelOption>[
        const ModelOption(
          providerId: 'deepseek',
          providerName: 'DeepSeek',
          id: 'deepseek-v4-flash',
          displayName: 'DeepSeek V4 Flash',
          capabilities: <String>['chat', 'streaming'],
          contextLength: 128000,
          privacyMetadata: <String>['provider-retention-disclosed'],
          pricing: ModelPricing(
            inputPricePerMillionTokens: 0.14,
            outputPricePerMillionTokens: 0.28,
            currency: 'USD',
            lastRefreshedAt: '2026-08-10T00:00:00+00:00',
          ),
        ),
      ];

  @override
  Future<ModelCostEstimate> estimate({
    required ApiEndpoint endpoint,
    required Session session,
    required String providerId,
    required String modelId,
    required int inputTokens,
    required int outputTokens,
  }) async =>
      const ModelCostEstimate(
        providerId: 'deepseek',
        modelId: 'deepseek-v4-flash',
        currency: 'USD',
        estimatedCost: 0.28,
        priceLastRefreshedAt: '2026-08-10T00:00:00+00:00',
      );
}

void main() {
  testWidgets('shows model details and returns selected model',
      (WidgetTester tester) async {
    final ModelSelectionController controller = ModelSelectionController(
      endpoint: ApiEndpoint.parseDebug('http://127.0.0.1:8080'),
      session: Session(
        accessToken: 'token',
        ownerId: 'owner',
        expiresAt: DateTime.utc(2099),
      ),
      gateway: _ScreenGateway(),
    );
    ModelOption? selected;

    await tester.pumpWidget(MaterialApp(
      home: ModelSelectionScreen(
        controller: controller,
        onSelected: (ModelOption model) => selected = model,
      ),
    ));
    await tester.pumpAndSettle();

    expect(find.text('DeepSeek V4 Flash'), findsOneWidget);
    expect(find.text('128K 上下文'), findsOneWidget);
    expect(find.text('价格更新时间：2026-08-10 00:00'), findsOneWidget);
    expect(find.text('DeepSeek V4 Flash'), findsOneWidget);
    await tester
        .tap(find.byKey(const Key('model-select-deepseek-deepseek-v4-flash')));
    await tester.pump();
    expect(selected, isNull);
    await tester.drag(find.byType(ListView), const Offset(0, -700));
    await tester.pump();
    expect(find.byKey(const Key('model-confirm-selection')), findsOneWidget);
    await tester.tap(find.byKey(const Key('model-confirm-selection')));
    await tester.pump();
    expect(selected?.id, 'deepseek-v4-flash');
  });
}
