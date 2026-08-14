import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/network/api_client.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/features/models/model_gateway.dart';
import 'package:past_partner/features/models/model_option.dart';

void main() {
  final ApiEndpoint endpoint = ApiEndpoint.parseDebug('http://127.0.0.1:8080');
  final Session session = Session(
    accessToken: 'token',
    ownerId: 'owner',
    expiresAt: DateTime.utc(2099),
  );

  test('loads model catalog and preserves pricing metadata', () async {
    final List<http.Request> requests = <http.Request>[];
    final ApiClient client = ApiClient(
      client: MockClient((http.Request request) async {
        requests.add(request);
        return http.Response(
          jsonEncode(<String, dynamic>{
            'models': <Map<String, dynamic>>[
              <String, dynamic>{
                'provider_id': 'deepseek',
                'id': 'deepseek-v4-flash',
                'display_name': 'DeepSeek V4 Flash',
                'capabilities': <String>['chat', 'streaming'],
                'context_length': 128000,
                'privacy_metadata': <String>['provider-retention-disclosed'],
                'pricing': <String, dynamic>{
                  'input_price_per_million_tokens': 0.14,
                  'output_price_per_million_tokens': 0.28,
                  'currency': 'USD',
                  'last_refreshed_at': '2026-08-10T00:00:00+00:00',
                },
              },
            ],
          }),
          200,
        );
      }),
    );
    final ApiClientModelGateway gateway = ApiClientModelGateway(client);

    final List<ModelOption> models = await gateway.list(
      endpoint: endpoint,
      session: session,
      providerId: 'deepseek',
    );

    expect(models.single.providerId, 'deepseek');
    expect(models.single.contextLength, 128000);
    expect(models.single.pricing.inputPricePerMillionTokens, 0.14);
    expect(models.single.pricing.lastRefreshedAt, '2026-08-10T00:00:00+00:00');
    expect(requests.single.url.queryParameters['provider_id'], 'deepseek');
    expect(requests.single.headers['authorization'], 'Bearer token');
  });

  test('normalizes the filtered response provider envelope onto each model',
      () async {
    final ApiClientModelGateway gateway = ApiClientModelGateway(
      ApiClient(
        client: MockClient((http.Request request) async => http.Response(
              jsonEncode(<String, dynamic>{
                'provider_id': 'deepseek',
                'models': <Map<String, dynamic>>[
                  <String, dynamic>{
                    'id': 'deepseek-v4-flash',
                    'display_name': 'DeepSeek V4 Flash',
                    'capabilities': <String>['chat'],
                  },
                ],
              }),
              200,
            )),
      ),
    );

    final List<ModelOption> models = await gateway.list(
      endpoint: endpoint,
      session: session,
      providerId: 'deepseek',
    );

    expect(models.single.providerId, 'deepseek');
  });

  test('rejects a malformed model catalog instead of creating partial models',
      () async {
    final ApiClientModelGateway gateway = ApiClientModelGateway(
      ApiClient(
        client: MockClient((http.Request request) async => http.Response(
              jsonEncode(<String, dynamic>{
                'models': <Map<String, dynamic>>[
                  <String, dynamic>{
                    'provider_id': 'deepseek',
                    'id': 'missing-name'
                  },
                ],
              }),
              200,
            )),
      ),
    );

    expect(
      () => gateway.list(endpoint: endpoint, session: session),
      throwsA(isA<FormatException>()),
    );
  });

  test('estimates cost using bounded token fields', () async {
    final List<http.Request> requests = <http.Request>[];
    final ApiClientModelGateway gateway = ApiClientModelGateway(
      ApiClient(
        client: MockClient((http.Request request) async {
          requests.add(request);
          return http.Response(
            jsonEncode(<String, dynamic>{
              'provider_id': 'deepseek',
              'model_id': 'deepseek-v4-flash',
              'currency': 'USD',
              'estimated_cost': 0.28,
              'price_last_refreshed_at': '2026-08-10T00:00:00+00:00',
            }),
            200,
          );
        }),
      ),
    );

    final ModelCostEstimate estimate = await gateway.estimate(
      endpoint: endpoint,
      session: session,
      providerId: 'deepseek',
      modelId: 'deepseek-v4-flash',
      inputTokens: 1000000,
      outputTokens: 500000,
    );

    expect(estimate.estimatedCost, 0.28);
    expect(jsonDecode(requests.single.body), <String, dynamic>{
      'provider_id': 'deepseek',
      'model_id': 'deepseek-v4-flash',
      'input_tokens': 1000000,
      'output_tokens': 500000,
    });
  });

  test('rejects an estimate for a different provider or model', () async {
    final ApiClientModelGateway gateway = ApiClientModelGateway(
      ApiClient(
        client: MockClient((http.Request request) async => http.Response(
              jsonEncode(<String, dynamic>{
                'provider_id': 'qwen',
                'model_id': 'qwen3.7-plus',
                'currency': 'USD',
                'estimated_cost': 0.28,
              }),
              200,
            )),
      ),
    );

    expect(
      () => gateway.estimate(
        endpoint: endpoint,
        session: session,
        providerId: 'deepseek',
        modelId: 'deepseek-v4-flash',
        inputTokens: 100,
        outputTokens: 50,
      ),
      throwsA(isA<FormatException>()),
    );
  });
}
