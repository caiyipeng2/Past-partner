import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/network/api_client.dart';
import 'package:past_partner/core/network/api_failure.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/features/media/media_analysis_controller.dart';

final ApiEndpoint endpoint = ApiEndpoint.parseDebug('http://127.0.0.1:8080');
final Session session = Session(
  accessToken: 'token',
  ownerId: 'owner',
  expiresAt: DateTime.utc(2099),
);

class _FakeGateway implements MediaAnalysisGateway {
  MediaAnalysisResult? result;
  Object? error;
  int calls = 0;
  MediaAnalysisRequestData? lastRequest;

  @override
  Future<MediaAnalysisResult> analyze(MediaAnalysisRequestData request) async {
    calls++;
    lastRequest = request;
    if (error != null) throw error!;
    return result ??
        const MediaAnalysisResult(
          importId: 'import-1',
          fileId: 'file-1',
          providerId: 'openai',
          modelId: 'gpt-4.1-mini',
          mediaCategory: 'image',
          mediaType: 'image/png',
          description: '图片描述',
          usage: <String, int>{'prompt_tokens': 3},
          providerTransfer: true,
          providerRequestId: 'request-1',
        );
  }
}

void main() {
  test('client gateway posts the consent-gated media analysis request',
      () async {
    final List<http.Request> requests = <http.Request>[];
    final ApiClient client = ApiClient(
      client: MockClient((http.Request request) async {
        requests.add(request);
        return http.Response.bytes(
          utf8.encode(jsonEncode(<String, dynamic>{
            'import_id': 'import-1',
            'file_id': 'file-1',
            'state': 'uploaded',
            'provider_id': 'openai',
            'model_id': 'gpt-4.1-mini',
            'media_category': 'image',
            'media_type': 'image/png',
            'description': '图片描述',
            'usage': <String, int>{'prompt_tokens': 3},
            'provider_transfer': true,
            'provider_request_id': 'request-1',
          })),
          200,
          headers: <String, String>{
            'content-type': 'application/json; charset=utf-8'
          },
        );
      }),
    );
    final ApiClientMediaAnalysisGateway gateway =
        ApiClientMediaAnalysisGateway(client);

    final MediaAnalysisResult result = await gateway.analyze(
      MediaAnalysisRequestData(
        endpoint: endpoint,
        session: session,
        importId: 'import-1',
        consentId: 'consent-1',
        providerId: 'openai',
        modelId: 'gpt-4.1-mini',
        dataCategory: 'image',
        authorizationScope: 'persona-image-analysis',
        prompt: '描述图片',
        fileId: 'file-1',
      ),
    );

    expect(result.description, '图片描述');
    expect(requests.single.method, 'POST');
    expect(
      requests.single.url.path,
      '/api/v1/imports/import-1/media-analysis',
    );
    expect(requests.single.headers['authorization'], 'Bearer token');
    expect(jsonDecode(requests.single.body), <String, dynamic>{
      'consent_id': 'consent-1',
      'provider_id': 'openai',
      'model_id': 'gpt-4.1-mini',
      'data_category': 'image',
      'authorization_scope': 'persona-image-analysis',
      'prompt': '描述图片',
      'file_id': 'file-1',
    });
  });

  test('client gateway carries OCR operation and structured result', () async {
    final List<http.Request> requests = <http.Request>[];
    final ApiClient client = ApiClient(
      client: MockClient((http.Request request) async {
        requests.add(request);
        return http.Response.bytes(
          utf8.encode(jsonEncode(<String, dynamic>{
            'import_id': 'import-1',
            'file_id': 'file-1',
            'state': 'uploaded',
            'provider_id': 'openai',
            'model_id': 'ocr-model',
            'media_category': 'image',
            'media_type': 'image/png',
            'analysis_kind': 'ocr',
            'description': '识别出的文字',
            'structured_data': <String, dynamic>{
              'text': '识别出的文字',
              'blocks': <Map<String, dynamic>>[
                <String, dynamic>{'text': '识别出的文字'},
              ],
            },
            'provider_transfer': true,
          })),
          200,
          headers: <String, String>{'content-type': 'application/json'},
        );
      }),
    );

    final MediaAnalysisResult result =
        await ApiClientMediaAnalysisGateway(client).analyze(
      MediaAnalysisRequestData(
        endpoint: endpoint,
        session: session,
        importId: 'import-1',
        consentId: 'consent-1',
        providerId: 'openai',
        modelId: 'ocr-model',
        dataCategory: 'image',
        authorizationScope: 'persona-image-ocr',
        prompt: '识别图片文字',
        analysisKind: 'ocr',
      ),
    );

    expect(result.analysisKind, 'ocr');
    expect(result.structuredData?['text'], '识别出的文字');
    expect(
        jsonDecode(requests.single.body), containsPair('analysis_kind', 'ocr'));
  });

  test(
      'controller carries OCR operation and structured result without media bytes',
      () async {
    final _FakeGateway gateway = _FakeGateway()
      ..result = const MediaAnalysisResult(
        importId: 'import-1',
        fileId: 'file-1',
        providerId: 'openai',
        modelId: 'ocr-model',
        mediaCategory: 'image',
        mediaType: 'image/png',
        description: '识别出的文字',
        usage: <String, int>{'prompt_tokens': 3},
        providerTransfer: true,
        analysisKind: 'ocr',
        structuredData: <String, dynamic>{
          'text': '识别出的文字',
          'blocks': <dynamic>[]
        },
      );
    final MediaAnalysisController controller = MediaAnalysisController(
      endpoint: endpoint,
      session: session,
      importId: 'import-1',
      gateway: gateway,
    );

    expect(
      await controller.analyze(
        consentId: 'consent-1',
        providerId: 'openai',
        modelId: 'ocr-model',
        dataCategory: 'image',
        authorizationScope: 'persona-image-ocr',
        prompt: '识别图片文字',
        analysisKind: 'ocr',
      ),
      isTrue,
    );
    expect(gateway.lastRequest?.analysisKind, 'ocr');
    expect(controller.result?.structuredData?['text'], '识别出的文字');
  });

  test('controller exposes loading and success without retaining media bytes',
      () async {
    final _FakeGateway gateway = _FakeGateway();
    final MediaAnalysisController controller = MediaAnalysisController(
      endpoint: endpoint,
      session: session,
      importId: 'import-1',
      gateway: gateway,
    );

    final Future<bool> completed = controller.analyze(
      consentId: 'consent-1',
      providerId: 'openai',
      modelId: 'gpt-4.1-mini',
      dataCategory: 'image',
      authorizationScope: 'persona-image-analysis',
      prompt: '描述图片',
      fileId: 'file-1',
    );
    expect(controller.state, MediaAnalysisState.loading);
    expect(await completed, isTrue);
    expect(controller.state, MediaAnalysisState.success);
    expect(controller.result?.description, '图片描述');
    expect(controller.errorCode, isNull);
    expect(gateway.lastRequest?.prompt, '描述图片');
  });

  test(
      'controller maps capability/provider errors and retries the same request',
      () async {
    final _FakeGateway gateway = _FakeGateway()
      ..error = const ApiFailure('capability_not_supported', 'unsupported');
    final MediaAnalysisController controller = MediaAnalysisController(
      endpoint: endpoint,
      session: session,
      importId: 'import-1',
      gateway: gateway,
    );

    final bool first = await controller.analyze(
      consentId: 'consent-1',
      providerId: 'openai',
      modelId: 'gpt-4.1-mini',
      dataCategory: 'image',
      authorizationScope: 'persona-image-analysis',
      prompt: '描述图片',
    );
    expect(first, isFalse);
    expect(controller.state, MediaAnalysisState.error);
    expect(controller.errorCode, 'capability_not_supported');
    expect(controller.errorMessage, contains('不支持'));

    gateway.error = null;
    expect(await controller.retry(), isTrue);
    expect(gateway.calls, 2);
    expect(controller.state, MediaAnalysisState.success);

    gateway.error = const ApiFailure('provider_unavailable', 'unavailable');
    await controller.analyze(
      consentId: 'consent-1',
      providerId: 'openai',
      modelId: 'gpt-4.1-mini',
      dataCategory: 'image',
      authorizationScope: 'persona-image-analysis',
      prompt: '描述图片',
    );
    expect(controller.errorCode, 'provider_unavailable');
    expect(controller.errorMessage, contains('暂时不可用'));
  });
}
