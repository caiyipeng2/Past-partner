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
      ApiEndpoint endpoint, Session session) async {
    final http.Response response = await _send(
      'GET',
      endpoint.path('/api/v1/personas'),
      <String, String>{'Authorization': 'Bearer ${session.accessToken}'},
    );
    if (response.statusCode != 200) throw _failure(response);
    final Map<String, dynamic> body = _jsonObject(response);
    final dynamic personas = body['personas'];
    if (personas is! List) {
      throw const ApiFailure('invalid_response',
          'The local service returned an invalid response.');
    }
    final List<Map<String, dynamic>> result = <Map<String, dynamic>>[];
    for (final dynamic value in personas) {
      if (value is! Map) {
        throw const ApiFailure('invalid_response',
            'The local service returned an invalid response.');
      }
      result.add(Map<String, dynamic>.from(value));
    }
    return result;
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

  Future<http.Response> _send(
      String method, Uri uri, Map<String, String>? headers) async {
    final http.Request request = http.Request(method, uri)
      ..followRedirects = false
      ..maxRedirects = 0;
    if (headers != null) request.headers.addAll(headers);
    try {
      final http.StreamedResponse streamed = await _client.send(request);
      final http.Response response = await http.Response.fromStream(streamed);
      if (response.isRedirect ||
          (response.statusCode >= 300 && response.statusCode < 400)) {
        throw const ApiFailure(
            'redirect_rejected', 'The server redirect was rejected.');
      }
      return response;
    } on ApiFailure {
      rethrow;
    } catch (_) {
      throw const ApiFailure(
          'transport_unavailable', 'The local service is unavailable.');
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
      ..headers.addAll(
          <String, String>{...headers, 'Content-Type': 'application/json'})
      ..body = jsonEncode(payload);
    try {
      final http.StreamedResponse streamed = await _client.send(request);
      final http.Response response = await http.Response.fromStream(streamed);
      if (response.isRedirect ||
          (response.statusCode >= 300 && response.statusCode < 400)) {
        throw const ApiFailure(
            'redirect_rejected', 'The server redirect was rejected.');
      }
      return response;
    } on ApiFailure {
      rethrow;
    } catch (_) {
      throw const ApiFailure(
          'transport_unavailable', 'The local service is unavailable.');
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
        'invalid_response', 'The local service returned an invalid response.');
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
