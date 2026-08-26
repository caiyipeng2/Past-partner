import 'package:flutter/foundation.dart';

import '../../core/config/api_endpoint.dart';
import '../../core/session/session.dart';
import 'model_gateway.dart';
import 'model_option.dart';
import 'model_selection_store.dart';

enum ModelSelectionState { idle, loading, ready, estimating, error }

class ModelSelectionController extends ChangeNotifier {
  ModelSelectionController({
    required this.endpoint,
    required this.session,
    required this.gateway,
    ModelOption? initialSelection,
    this.selectionStore,
    this.selectionScope,
  }) : selected = initialSelection;

  final ApiEndpoint endpoint;
  final Session session;
  final ModelGateway gateway;
  final ModelSelectionStore? selectionStore;
  final String? selectionScope;

  ModelSelectionState state = ModelSelectionState.idle;
  List<ModelOption> models = const <ModelOption>[];
  String? providerFilter;
  ModelOption? selected;
  ModelCostEstimate? estimateResult;
  String? errorMessage;
  String? estimateErrorMessage;
  String? persistenceError;

  List<String> get providers {
    final Set<String> values = <String>{
      for (final ModelOption model in models) model.providerId,
    };
    return values.toList(growable: false);
  }

  List<ModelOption> get visibleModels => models
      .where((ModelOption model) =>
          providerFilter == null || model.providerId == providerFilter)
      .toList(growable: false);

  Future<void> load() async {
    state = ModelSelectionState.loading;
    errorMessage = null;
    notifyListeners();
    try {
      final List<ModelOption> loaded = await gateway.list(
        endpoint: endpoint,
        session: session,
        providerId: providerFilter,
      );
      models = loaded;
      final ModelOption? previous = selected;
      final ModelSelection? persisted =
          previous == null ? await _readPersistedSelection() : null;
      ModelOption? refreshedSelection;
      if (previous != null) {
        refreshedSelection = _find(previous.providerId, previous.id);
      }
      refreshedSelection ??= persisted == null
          ? null
          : _find(persisted.providerId, persisted.modelId);
      selected = refreshedSelection;
      state = ModelSelectionState.ready;
    } catch (_) {
      state = ModelSelectionState.error;
      errorMessage = '模型目录加载失败，请重试。';
    }
    notifyListeners();
  }

  void setProviderFilter(String? value) {
    providerFilter = value;
    notifyListeners();
  }

  Future<void> select(ModelOption model) async {
    selected = model;
    estimateResult = null;
    estimateErrorMessage = null;
    persistenceError = null;
    notifyListeners();
    final ModelSelectionStore? store = selectionStore;
    final String? scope = selectionScope;
    if (store == null || scope == null || scope.trim().isEmpty) return;
    try {
      await store.write(
        scope,
        ModelSelection(providerId: model.providerId, modelId: model.id),
      );
    } on Object {
      persistenceError = '模型选择保存失败，下次启动可能需要重新选择。';
      notifyListeners();
    }
  }

  Future<ModelSelection?> _readPersistedSelection() async {
    final ModelSelectionStore? store = selectionStore;
    final String? scope = selectionScope;
    if (store == null || scope == null || scope.trim().isEmpty) return null;
    try {
      return await store.read(scope);
    } on Object {
      return null;
    }
  }

  ModelOption? _find(String providerId, String modelId) {
    for (final ModelOption value in models) {
      if (value.providerId == providerId && value.id == modelId) {
        return value;
      }
    }
    return null;
  }

  Future<void> estimate(
      {required int inputTokens, required int outputTokens}) async {
    final ModelOption? model = selected;
    if (model == null) {
      estimateErrorMessage = '请先选择模型。';
      notifyListeners();
      return;
    }
    if (inputTokens < 0 ||
        inputTokens > 100000000 ||
        outputTokens < 0 ||
        outputTokens > 100000000) {
      estimateErrorMessage = 'Token 数量需在 0 到 100,000,000 之间。';
      notifyListeners();
      return;
    }
    state = ModelSelectionState.estimating;
    estimateErrorMessage = null;
    notifyListeners();
    try {
      estimateResult = await gateway.estimate(
        endpoint: endpoint,
        session: session,
        providerId: model.providerId,
        modelId: model.id,
        inputTokens: inputTokens,
        outputTokens: outputTokens,
      );
      state = ModelSelectionState.ready;
    } catch (_) {
      state = ModelSelectionState.ready;
      estimateErrorMessage = '当前价格不可估算，请稍后重试。';
    }
    notifyListeners();
  }
}
