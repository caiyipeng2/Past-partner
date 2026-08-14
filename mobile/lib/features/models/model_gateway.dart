import '../../core/config/api_endpoint.dart';
import '../../core/network/api_client.dart';
import '../../core/session/session.dart';
import 'model_option.dart';

abstract interface class ModelGateway {
  Future<List<ModelOption>> list({
    required ApiEndpoint endpoint,
    required Session session,
    String? providerId,
  });

  Future<ModelCostEstimate> estimate({
    required ApiEndpoint endpoint,
    required Session session,
    required String providerId,
    required String modelId,
    required int inputTokens,
    required int outputTokens,
  });
}

class ApiClientModelGateway implements ModelGateway {
  const ApiClientModelGateway(this.client);

  final ApiClient client;

  @override
  Future<List<ModelOption>> list({
    required ApiEndpoint endpoint,
    required Session session,
    String? providerId,
  }) async {
    final List<Map<String, dynamic>> values =
        await client.listModels(endpoint, session, providerId: providerId);
    return values.map(ModelOption.fromJson).toList(growable: false);
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
    if (inputTokens < 0 ||
        inputTokens > 100000000 ||
        outputTokens < 0 ||
        outputTokens > 100000000) {
      throw const FormatException('Token estimate is out of bounds.');
    }
    final Map<String, dynamic> response = await client.estimateModelCost(
      endpoint,
      session,
      providerId: providerId,
      modelId: modelId,
      inputTokens: inputTokens,
      outputTokens: outputTokens,
    );
    final ModelCostEstimate estimate = ModelCostEstimate.fromJson(response);
    if (estimate.providerId != providerId || estimate.modelId != modelId) {
      throw const FormatException(
          'The cost estimate does not match the model.');
    }
    return estimate;
  }
}
