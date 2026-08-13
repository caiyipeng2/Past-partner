import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/network/api_client.dart';
import 'package:past_partner/core/session/session.dart';

void main() {
  final ApiEndpoint endpoint =
      ApiEndpoint.parseDebug('http://127.0.0.1:8080');
  final Session session = Session(
    accessToken: 'token',
    ownerId: 'owner',
    expiresAt: DateTime.utc(2099),
  );

  test('reads missing chunks with the expected chunk count', () async {
    final List<http.Request> requests = <http.Request>[];
    final ApiClient client = ApiClient(
      client: MockClient((http.Request request) async {
        requests.add(request);
        return http.Response(
          jsonEncode(<String, dynamic>{
            'import_id': 'import-1',
            'expected_chunk_count': 3,
            'received_chunks': <int>[0],
            'missing_chunks': <int>[1, 2],
          }),
          200,
        );
      }),
    );

    final Map<String, dynamic> result = await client.missingChunks(
      endpoint,
      session,
      'import-1',
      expectedChunks: 3,
    );

    expect(result['missing_chunks'], <int>[1, 2]);
    expect(requests.single.method, 'GET');
    expect(
      requests.single.url.toString(),
      'http://127.0.0.1:8080/api/v1/imports/import-1/missing-chunks?expected_chunks=3',
    );
    expect(requests.single.headers['authorization'], 'Bearer token');
  });

  test('uploads a chunk with digest and completes the import', () async {
    final List<http.Request> requests = <http.Request>[];
    final ApiClient client = ApiClient(
      client: MockClient((http.Request request) async {
        requests.add(request);
        if (request.method == 'PUT') {
          return http.Response(
            jsonEncode(<String, dynamic>{
              'import_id': 'import-1',
              'index': 0,
              'length': 3,
              'sha256': 'abc',
              'duplicate': false,
              'received_bytes': 3,
              'total_bytes': 3,
            }),
            200,
          );
        }
        return http.Response(
          jsonEncode(<String, dynamic>{
            'id': 'import-1',
            'state': 'uploaded',
          }),
          200,
        );
      }),
    );

    final Map<String, dynamic> receipt = await client.putChunk(
      endpoint,
      session,
      'import-1',
      0,
      <int>[1, 2, 3],
      'abc',
    );
    final Map<String, dynamic> completed = await client.completeImport(
      endpoint,
      session,
      'import-1',
      wholeSha256: 'whole-digest',
    );

    expect(receipt['index'], 0);
    expect(completed['state'], 'uploaded');
    expect(requests[0].method, 'PUT');
    expect(requests[0].url.path, '/api/v1/imports/import-1/chunks/0');
    expect(requests[0].headers['x-chunk-sha256'], 'abc');
    expect(requests[0].bodyBytes, <int>[1, 2, 3]);
    expect(requests[1].method, 'POST');
    expect(requests[1].url.path, '/api/v1/imports/import-1/complete');
    expect(jsonDecode(requests[1].body), <String, dynamic>{
      'sha256': 'whole-digest',
    });
  });
}
