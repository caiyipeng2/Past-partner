import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/features/models/model_controller.dart';
import 'package:past_partner/features/models/model_gateway.dart';
import 'package:past_partner/features/models/model_option.dart';
import 'package:past_partner/features/models/model_selection_store.dart';

class _FakeGateway implements ModelGateway {
  _FakeGateway(this.models);

  final List<ModelOption> models;
  int estimateCalls = 0;

  @override
  Future<List<ModelOption>> list({
    required ApiEndpoint endpoint,
    required Session session,
    String? providerId,
  }) async {
    return models
        .where((ModelOption model) =>
            providerId == null || model.providerId == providerId)
        .toList(growable: false);
  }

  @override
  Future<ModelCostEstimate> estimate({
    required ApiEndpoint endpoint,
    required Session session,
    required String providerId,
    required String modelId,
    required int inputTokens,
    required int outputTokens,
  }) async {
    estimateCalls++;
    return const ModelCostEstimate(
      providerId: 'deepseek',
      modelId: 'deepseek-v4-flash',
      currency: 'USD',
      estimatedCost: 0.28,
      priceLastRefreshedAt: '2026-08-10T00:00:00+00:00',
    );
  }
}

void main() {
  final ApiEndpoint endpoint = ApiEndpoint.parseDebug('http://127.0.0.1:8080');
  final Session session = Session(
    accessToken: 'token',
    ownerId: 'owner',
    expiresAt: DateTime.utc(2099),
  );
  final List<ModelOption> models = <ModelOption>[
    const ModelOption(
      providerId: 'deepseek',
      providerName: 'DeepSeek',
      id: 'deepseek-v4-flash',
      displayName: 'DeepSeek V4 Flash',
      capabilities: <String>['chat'],
      contextLength: 128000,
      pricing: ModelPricing(inputPricePerMillionTokens: 0.14),
    ),
    const ModelOption(
      providerId: 'qwen',
      providerName: 'Alibaba Qwen',
      id: 'qwen3.7-plus',
      displayName: 'Qwen 3.7 Plus',
      capabilities: <String>['chat', 'vision'],
      contextLength: 32768,
      pricing: ModelPricing(),
    ),
  ];

  test('loads, filters, selects and estimates without losing selection',
      () async {
    final _FakeGateway gateway = _FakeGateway(models);
    final ModelSelectionController controller = ModelSelectionController(
      endpoint: endpoint,
      session: session,
      gateway: gateway,
    );

    await controller.load();
    expect(controller.state, ModelSelectionState.ready);
    expect(controller.visibleModels, models);
    await controller.select(models.first);
    controller.setProviderFilter('qwen');
    expect(controller.selected?.id, 'deepseek-v4-flash');
    expect(controller.visibleModels.single.providerId, 'qwen');
    await controller.estimate(inputTokens: 1000, outputTokens: 500);
    expect(controller.estimateResult?.estimatedCost, 0.28);
    expect(gateway.estimateCalls, 1);
  });

  test('returns a stable error and exposes retry after loading failure',
      () async {
    final ModelSelectionController controller = ModelSelectionController(
      endpoint: endpoint,
      session: session,
      gateway: _FailingGateway(),
    );

    await controller.load();
    expect(controller.state, ModelSelectionState.error);
    expect(controller.errorMessage, '模型目录加载失败，请重试。');
  });

  test('keeps the selected model when cost is unavailable', () async {
    final ModelSelectionController controller = ModelSelectionController(
      endpoint: endpoint,
      session: session,
      gateway: _UnavailableCostGateway(models),
    );

    await controller.load();
    await controller.select(models.first);
    await controller.estimate(inputTokens: 1000, outputTokens: 500);

    expect(controller.state, ModelSelectionState.ready);
    expect(controller.selected?.id, 'deepseek-v4-flash');
    expect(controller.estimateResult, isNull);
    expect(controller.estimateErrorMessage, '当前价格不可估算，请稍后重试。');
  });

  test('keeps old models visible and reports a refresh failure', () async {
    final _RefreshFailingGateway gateway = _RefreshFailingGateway(models);
    final ModelSelectionController controller = ModelSelectionController(
      endpoint: endpoint,
      session: session,
      gateway: gateway,
    );

    await controller.load();
    gateway.fail = true;
    await controller.load();

    expect(controller.state, ModelSelectionState.error);
    expect(controller.models, models);
    expect(controller.errorMessage, '模型目录加载失败，请重试。');
  });

  test(
      'restores a persisted model only when the refreshed catalog still contains it',
      () async {
    final InMemoryModelSelectionStore store = InMemoryModelSelectionStore();
    await store.write(
      'owner',
      const ModelSelection(
          providerId: 'deepseek', modelId: 'deepseek-v4-flash'),
    );
    final ModelSelectionController controller = ModelSelectionController(
      endpoint: endpoint,
      session: session,
      gateway: _FakeGateway(models),
      selectionStore: store,
      selectionScope: session.ownerId,
    );

    await controller.load();

    expect(controller.selected?.providerId, 'deepseek');
    expect(controller.selected?.id, 'deepseek-v4-flash');

    final ModelSelectionController unavailable = ModelSelectionController(
      endpoint: endpoint,
      session: session,
      gateway: _FakeGateway(<ModelOption>[models.last]),
      selectionStore: store,
      selectionScope: session.ownerId,
    );
    await unavailable.load();

    expect(unavailable.selected, isNull);
  });

  test('persists a newly selected model without storing session credentials',
      () async {
    final InMemoryModelSelectionStore store = InMemoryModelSelectionStore();
    final ModelSelectionController controller = ModelSelectionController(
      endpoint: endpoint,
      session: session,
      gateway: _FakeGateway(models),
      selectionStore: store,
      selectionScope: session.ownerId,
    );
    await controller.load();
    await controller.select(models.first);

    final ModelSelection? saved = await store.read(session.ownerId);
    expect(saved?.modelId, models.first.id);
    expect(saved?.toJson().toString(), isNot(contains('token')));
  });
}

class _FailingGateway implements ModelGateway {
  @override
  Future<List<ModelOption>> list({
    required ApiEndpoint endpoint,
    required Session session,
    String? providerId,
  }) async {
    throw StateError('network');
  }

  @override
  Future<ModelCostEstimate> estimate({
    required ApiEndpoint endpoint,
    required Session session,
    required String providerId,
    required String modelId,
    required int inputTokens,
    required int outputTokens,
  }) async {
    throw StateError('network');
  }
}

class _UnavailableCostGateway implements ModelGateway {
  _UnavailableCostGateway(this.models);

  final List<ModelOption> models;

  @override
  Future<List<ModelOption>> list({
    required ApiEndpoint endpoint,
    required Session session,
    String? providerId,
  }) async =>
      models;

  @override
  Future<ModelCostEstimate> estimate({
    required ApiEndpoint endpoint,
    required Session session,
    required String providerId,
    required String modelId,
    required int inputTokens,
    required int outputTokens,
  }) async {
    throw StateError('pricing_unavailable');
  }
}

class _RefreshFailingGateway implements ModelGateway {
  _RefreshFailingGateway(this.models);

  final List<ModelOption> models;
  bool fail = false;

  @override
  Future<List<ModelOption>> list({
    required ApiEndpoint endpoint,
    required Session session,
    String? providerId,
  }) async {
    if (fail) throw StateError('network');
    return models;
  }

  @override
  Future<ModelCostEstimate> estimate({
    required ApiEndpoint endpoint,
    required Session session,
    required String providerId,
    required String modelId,
    required int inputTokens,
    required int outputTokens,
  }) async =>
      throw StateError('network');
}
