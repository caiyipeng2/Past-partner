import 'dart:convert';

import 'package:http/http.dart' as http;

import '../config/api_endpoint.dart';
import '../session/session.dart';
import 'api_failure.dart';

class ApiClient {
  ApiClient({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  Future<Session> pair(ApiEndpoint endpoint, {String? deviceToken}) async {
    final http.Response response = await _send(
      'POST',
      endpoint.path('/api/v1/auth/session'),
      deviceToken == null
          ? null
          : <String, String>{'X-Dev-Device-Bootstrap-Token': deviceToken},
    );
    if (response.statusCode != 201) throw _failure(response);
    return Session.fromJson(_jsonObject(response));
  }

  Future<void> probe(ApiEndpoint endpoint, Session session) async {
    final http.Response response = await _send(
      'GET',
      endpoint.path('/api/v1/personas'),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
    );
    if (response.statusCode != 200) throw _failure(response);
  }

  Future<List<Map<String, dynamic>>> listPersonas(
    ApiEndpoint endpoint,
    Session session,
  ) async {
    final http.Response response = await _send(
      'GET',
      endpoint.path('/api/v1/personas'),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
    );
    if (response.statusCode != 200) throw _failure(response);
    final Map<String, dynamic> body = _jsonObject(response);
    final dynamic personas = body['personas'];
    if (personas is! List) {
      throw const ApiFailure(
        'invalid_response',
        'The local service returned an invalid response.',
      );
    }
    final List<Map<String, dynamic>> result = <Map<String, dynamic>>[];
    for (final dynamic value in personas) {
      if (value is! Map) {
        throw const ApiFailure(
          'invalid_response',
          'The local service returned an invalid response.',
        );
      }
      result.add(Map<String, dynamic>.from(value));
    }
    return result;
  }

  Future<List<Map<String, dynamic>>> listConsents(
    ApiEndpoint endpoint,
    Session session,
    String personaId,
  ) async {
    final http.Response response = await _send(
      'GET',
      endpoint
          .path('/api/v1/consents')
          .replace(queryParameters: <String, String>{'persona_id': personaId}),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
    );
    if (response.statusCode != 200) throw _failure(response);
    final Map<String, dynamic> body = _jsonObject(response);
    final dynamic consents = body['consents'];
    if (consents is! List || consents.length > 2048) {
      throw const ApiFailure(
        'invalid_response',
        'The local service returned an invalid response.',
      );
    }
    return consents
        .map((dynamic value) {
          if (value is! Map) {
            throw const ApiFailure(
              'invalid_response',
              'The local service returned an invalid response.',
            );
          }
          return Map<String, dynamic>.from(value);
        })
        .toList(growable: false);
  }

  Future<Map<String, dynamic>> createConsent(
    ApiEndpoint endpoint,
    Session session,
    Map<String, dynamic> payload,
  ) async {
    const Set<String> allowed = <String>{
      'persona_id',
      'provider_id',
      'model_id',
      'data_category',
      'estimated_cost',
      'purpose',
      'authorization_scope',
    };
    if (payload.keys.any((String key) => !allowed.contains(key))) {
      throw const ApiFailure(
        'invalid_request',
        'The consent request contains unsupported fields.',
      );
    }
    final http.Response response = await _sendJson(
      'POST',
      endpoint.path('/api/v1/consents'),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
      payload,
    );
    if (response.statusCode != 201) throw _failure(response);
    return _jsonObject(response);
  }

  Future<Map<String, dynamic>> revokeConsent(
    ApiEndpoint endpoint,
    Session session,
    String consentId,
  ) async {
    final http.Response response = await _sendJson(
      'POST',
      endpoint.path('/api/v1/consents/$consentId/revoke'),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
      <String, dynamic>{},
    );
    if (response.statusCode != 200) throw _failure(response);
    return _jsonObject(response);
  }

  Future<List<Map<String, dynamic>>> listModels(
    ApiEndpoint endpoint,
    Session session, {
    String? providerId,
  }) async {
    final Uri uri = endpoint
        .path('/api/v1/models')
        .replace(
          queryParameters: providerId == null
              ? null
              : <String, String>{'provider_id': providerId},
        );
    final http.Response response = await _send('GET', uri, <String, String>{
      'Authorization': 'Bearer ${session.accessToken}',
    });
    if (response.statusCode != 200) throw _failure(response);
    final Map<String, dynamic> body = _jsonObject(response);
    final dynamic models = body['models'];
    if (models is! List || models.length > 2048) {
      throw const ApiFailure(
        'invalid_response',
        'The local service returned an invalid response.',
      );
    }
    final List<Map<String, dynamic>> result = <Map<String, dynamic>>[];
    for (final dynamic value in models) {
      if (value is! Map) {
        throw const ApiFailure(
          'invalid_response',
          'The local service returned an invalid response.',
        );
      }
      final Map<String, dynamic> model = Map<String, dynamic>.from(value);
      // The filtered v1 response identifies the provider at the envelope
      // level. Normalize it onto each item so the client keeps one model
      // contract for both filtered and all-provider requests.
      if (providerId != null && !model.containsKey('provider_id')) {
        model['provider_id'] = providerId;
      }
      result.add(model);
    }
    return result;
  }

  Future<Map<String, dynamic>> estimateModelCost(
    ApiEndpoint endpoint,
    Session session, {
    required String providerId,
    required String modelId,
    required int inputTokens,
    required int outputTokens,
  }) async {
    final http.Response response = await _sendJson(
      'POST',
      endpoint.path('/api/v1/models/cost-estimate'),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
      <String, dynamic>{
        'provider_id': providerId,
        'model_id': modelId,
        'input_tokens': inputTokens,
        'output_tokens': outputTokens,
      },
    );
    if (response.statusCode != 200) throw _failure(response);
    return _jsonObject(response);
  }

  Future<List<Map<String, dynamic>>> listConversations(
    ApiEndpoint endpoint,
    Session session, {
    String? personaId,
  }) async {
    final Uri uri = endpoint
        .path('/api/v1/conversations')
        .replace(
          queryParameters: personaId == null
              ? null
              : <String, String>{'persona_id': personaId},
        );
    final http.Response response = await _send('GET', uri, <String, String>{
      'Authorization': 'Bearer ${session.accessToken}',
    });
    if (response.statusCode != 200) throw _failure(response);
    final dynamic values = _jsonObject(response)['conversations'];
    if (values is! List || values.length > 2048) {
      throw const ApiFailure(
        'invalid_response',
        'The local service returned an invalid response.',
      );
    }
    return values
        .map((dynamic value) {
          if (value is! Map) {
            throw const ApiFailure(
              'invalid_response',
              'The local service returned an invalid response.',
            );
          }
          return Map<String, dynamic>.from(value);
        })
        .toList(growable: false);
  }

  Future<Map<String, dynamic>> createConversation(
    ApiEndpoint endpoint,
    Session session, {
    required String personaId,
    required String providerId,
    required String modelId,
  }) async {
    final http.Response response = await _sendJson(
      'POST',
      endpoint.path('/api/v1/conversations'),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
      <String, dynamic>{
        'persona_id': personaId,
        'provider_id': providerId,
        'model_id': modelId,
      },
    );
    if (response.statusCode != 201) throw _failure(response);
    return _jsonObject(response);
  }

  Future<Map<String, dynamic>> getConversation(
    ApiEndpoint endpoint,
    Session session,
    String conversationId,
  ) async {
    final http.Response response = await _send(
      'GET',
      endpoint.path('/api/v1/conversations/$conversationId'),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
    );
    if (response.statusCode != 200) throw _failure(response);
    return _jsonObject(response);
  }

  Future<Map<String, dynamic>> sendConversationMessage(
    ApiEndpoint endpoint,
    Session session,
    String conversationId,
    String content,
  ) async {
    final http.Response response = await _sendJson(
      'POST',
      endpoint.path('/api/v1/conversations/$conversationId/messages'),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
      <String, dynamic>{'content': content},
    );
    if (response.statusCode != 200) throw _failure(response);
    return _jsonObject(response);
  }

  Future<Map<String, dynamic>> createPersona(
    ApiEndpoint endpoint,
    Session session,
    Map<String, dynamic> payload,
  ) async {
    final http.Response response = await _sendJson(
      'POST',
      endpoint.path('/api/v1/personas'),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
      payload,
    );
    if (response.statusCode != 201) throw _failure(response);
    return _jsonObject(response);
  }

  Future<List<Map<String, dynamic>>> listImports(
    ApiEndpoint endpoint,
    Session session,
    String personaId,
  ) async {
    final http.Response response = await _send(
      'GET',
      endpoint
          .path('/api/v1/imports')
          .replace(queryParameters: <String, String>{'persona_id': personaId}),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
    );
    if (response.statusCode != 200) throw _failure(response);
    final Map<String, dynamic> body = _jsonObject(response);
    final dynamic imports = body['imports'];
    if (imports is! List) {
      throw const ApiFailure(
        'invalid_response',
        'The local service returned an invalid response.',
      );
    }
    return imports
        .map((dynamic value) {
          if (value is! Map) {
            throw const ApiFailure(
              'invalid_response',
              'The local service returned an invalid response.',
            );
          }
          return Map<String, dynamic>.from(value);
        })
        .toList(growable: false);
  }

  Future<Map<String, dynamic>> createImport(
    ApiEndpoint endpoint,
    Session session,
    Map<String, dynamic> payload,
  ) async {
    final http.Response response = await _sendJson(
      'POST',
      endpoint.path('/api/v1/imports'),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
      payload,
    );
    if (response.statusCode != 201) throw _failure(response);
    return _jsonObject(response);
  }

  Future<Map<String, dynamic>> missingChunks(
    ApiEndpoint endpoint,
    Session session,
    String importId, {
    required int expectedChunks,
  }) async {
    final http.Response response = await _send(
      'GET',
      endpoint
          .path('/api/v1/imports/$importId/missing-chunks')
          .replace(
            queryParameters: <String, String>{
              'expected_chunks': '$expectedChunks',
            },
          ),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
    );
    if (response.statusCode != 200) throw _failure(response);
    return _jsonObject(response);
  }

  Future<Map<String, dynamic>> putChunk(
    ApiEndpoint endpoint,
    Session session,
    String importId,
    int index,
    List<int> bytes,
    String sha256,
  ) async {
    final http.Request request =
        http.Request(
            'PUT',
            endpoint.path('/api/v1/imports/$importId/chunks/$index'),
          )
          ..followRedirects = false
          ..maxRedirects = 0
          ..headers.addAll(<String, String>{
            'Authorization': 'Bearer ${session.accessToken}',
            'Content-Type': 'application/octet-stream',
            'X-Chunk-Sha256': sha256,
            'Content-Length': '${bytes.length}',
          })
          ..bodyBytes = bytes;
    final http.Response response = await _sendRequest(request);
    if (response.statusCode != 200) throw _failure(response);
    return _jsonObject(response);
  }

  Future<Map<String, dynamic>> completeImport(
    ApiEndpoint endpoint,
    Session session,
    String importId, {
    String? wholeSha256,
  }) async {
    final http.Response response = await _sendJson(
      'POST',
      endpoint.path('/api/v1/imports/$importId/complete'),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
      <String, dynamic>{if (wholeSha256 != null) 'sha256': wholeSha256},
    );
    if (response.statusCode != 200) throw _failure(response);
    return _jsonObject(response);
  }

  Future<Map<String, dynamic>> getImportPreview(
    ApiEndpoint endpoint,
    Session session,
    String importId, {
    int limit = 20,
  }) async {
    final int boundedLimit = limit.clamp(1, 100);
    final http.Response response = await _send(
      'GET',
      endpoint
          .path('/api/v1/imports/$importId/preview')
          .replace(queryParameters: <String, String>{'limit': '$boundedLimit'}),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
    );
    if (response.statusCode != 200) throw _failure(response);
    return _jsonObject(response);
  }

  Future<Map<String, dynamic>> getParticipantMapping(
    ApiEndpoint endpoint,
    Session session,
    String importId,
  ) async {
    final http.Response response = await _send(
      'GET',
      endpoint.path('/api/v1/imports/$importId/participant-mapping'),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
    );
    if (response.statusCode != 200) throw _failure(response);
    return _jsonObject(response);
  }

  Future<Map<String, dynamic>> saveParticipantMapping(
    ApiEndpoint endpoint,
    Session session,
    String importId,
    Map<String, String> mapping,
  ) async {
    final http.Response response = await _sendJson(
      'POST',
      endpoint.path('/api/v1/imports/$importId/participant-mapping'),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
      <String, dynamic>{'mapping': mapping},
    );
    if (response.statusCode != 200) throw _failure(response);
    return _jsonObject(response);
  }

  Future<void> saveImportCorrections(
    ApiEndpoint endpoint,
    Session session,
    String importId,
    List<Map<String, dynamic>> corrections,
  ) async {
    final http.Response response = await _sendJson(
      'POST',
      endpoint.path('/api/v1/imports/$importId/corrections'),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
      <String, dynamic>{'corrections': corrections},
    );
    if (response.statusCode != 200) throw _failure(response);
  }

  Future<http.Response> _send(
    String method,
    Uri uri,
    Map<String, String>? headers,
  ) async {
    final http.Request request = http.Request(method, uri)
      ..followRedirects = false
      ..maxRedirects = 0;
    if (headers != null) request.headers.addAll(headers);
    try {
      return _sendRequest(request);
    } on ApiFailure {
      rethrow;
    } catch (_) {
      throw const ApiFailure(
        'transport_unavailable',
        'The local service is unavailable.',
      );
    }
  }

  Future<http.Response> _sendRequest(http.Request request) async {
    try {
      final http.StreamedResponse streamed = await _client.send(request);
      final http.Response response = await http.Response.fromStream(streamed);
      if (response.isRedirect ||
          (response.statusCode >= 300 && response.statusCode < 400)) {
        throw const ApiFailure(
          'redirect_rejected',
          'The server redirect was rejected.',
        );
      }
      return response;
    } on ApiFailure {
      rethrow;
    } catch (_) {
      throw const ApiFailure(
        'transport_unavailable',
        'The local service is unavailable.',
      );
    }
  }

  Future<http.Response> _sendJson(
    String method,
    Uri uri,
    Map<String, String> headers,
    Map<String, dynamic> payload,
  ) async {
    final http.Request request = http.Request(method, uri)
      ..followRedirects = false
      ..maxRedirects = 0
      ..headers.addAll(<String, String>{
        ...headers,
        'Content-Type': 'application/json',
      })
      ..body = jsonEncode(payload);
    try {
      final http.StreamedResponse streamed = await _client.send(request);
      final http.Response response = await http.Response.fromStream(streamed);
      if (response.isRedirect ||
          (response.statusCode >= 300 && response.statusCode < 400)) {
        throw const ApiFailure(
          'redirect_rejected',
          'The server redirect was rejected.',
        );
      }
      return response;
    } on ApiFailure {
      rethrow;
    } catch (_) {
      throw const ApiFailure(
        'transport_unavailable',
        'The local service is unavailable.',
      );
    }
  }

  static Map<String, dynamic> _jsonObject(http.Response response) {
    try {
      final dynamic value = jsonDecode(response.body);
      if (value is Map<String, dynamic>) return value;
    } on FormatException {
      // Fall through to a stable client error.
    }
    throw const ApiFailure(
      'invalid_response',
      'The local service returned an invalid response.',
    );
  }

  static ApiFailure _failure(http.Response response) {
    String code = 'http_error';
    String message = 'The local service rejected the request.';
    try {
      final dynamic value = jsonDecode(response.body);
      if (value is Map<String, dynamic> &&
          value['error'] is Map<String, dynamic>) {
        final Map<String, dynamic> error =
            value['error'] as Map<String, dynamic>;
        if (error['code'] is String) code = error['code'] as String;
        if (error['message'] is String && error['message'] != '') {
          message = error['message'] as String;
        }
      }
    } on FormatException {
      // Do not expose the response body in a user-visible exception.
    }
    return ApiFailure(code, message, statusCode: response.statusCode);
  }
}
