import 'package:flutter/foundation.dart';

import '../../core/config/api_endpoint.dart';
import '../../core/session/session.dart';
import 'model_gateway.dart';
import 'model_option.dart';

enum ModelSelectionState { idle, loading, ready, estimating, error }

class ModelSelectionController extends ChangeNotifier {
  ModelSelectionController({
    required this.endpoint,
    required this.session,
    required this.gateway,
    ModelOption? initialSelection,
  }) : selected = initialSelection;

  final ApiEndpoint endpoint;
  final Session session;
  final ModelGateway gateway;

  ModelSelectionState state = ModelSelectionState.idle;
  List<ModelOption> models = const <ModelOption>[];
  String? providerFilter;
  ModelOption? selected;
  ModelCostEstimate? estimateResult;
  String? errorMessage;
  String? estimateErrorMessage;

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
      ModelOption? refreshedSelection;
      if (previous != null) {
        for (final ModelOption value in models) {
          if (value.providerId == previous.providerId &&
              value.id == previous.id) {
            refreshedSelection = value;
            break;
          }
        }
      }
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

  void select(ModelOption model) {
    selected = model;
    estimateResult = null;
    estimateErrorMessage = null;
    notifyListeners();
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
