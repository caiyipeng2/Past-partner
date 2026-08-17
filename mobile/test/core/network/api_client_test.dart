import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/network/api_client.dart';
import 'package:past_partner/core/session/session.dart';

void main() {
  final ApiEndpoint endpoint = ApiEndpoint.parseDebug('http://127.0.0.1:8080');
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
          jsonEncode(<String, dynamic>{'id': 'import-1', 'state': 'uploaded'}),
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

  test(
    'reads and saves import review data through the bounded routes',
    () async {
      final List<http.Request> requests = <http.Request>[];
      final ApiClient client = ApiClient(
        client: MockClient((http.Request request) async {
          requests.add(request);
          if (request.url.path.endsWith('/preview')) {
            return http.Response(
              jsonEncode(<String, dynamic>{
                'import_id': 'import-1',
                'state': 'uploaded',
              }),
              200,
            );
          }
          if (request.method == 'GET') {
            return http.Response(
              jsonEncode(<String, dynamic>{
                'mapping': <String, String>{'wx-a': 'persona'},
              }),
              200,
            );
          }
          return http.Response(jsonEncode(<String, dynamic>{'ok': true}), 200);
        }),
      );

      await client.getImportPreview(endpoint, session, 'import-1', limit: 999);
      await client.getParticipantMapping(endpoint, session, 'import-1');
      await client.saveParticipantMapping(
        endpoint,
        session,
        'import-1',
        <String, String>{'wx-a': 'user'},
      );
      await client.saveImportCorrections(
        endpoint,
        session,
        'import-1',
        <Map<String, dynamic>>[
          <String, dynamic>{'record_id': 'a' * 64, 'review_state': 'accepted'},
        ],
      );

      expect(requests[0].url.queryParameters['limit'], '100');
      expect(requests[0].url.path, '/api/v1/imports/import-1/preview');
      expect(
        requests[1].url.path,
        '/api/v1/imports/import-1/participant-mapping',
      );
      expect(jsonDecode(requests[2].body), <String, dynamic>{
        'mapping': <String, String>{'wx-a': 'user'},
      });
      expect(jsonDecode(requests[3].body), <String, dynamic>{
        'corrections': <Map<String, dynamic>>[
          <String, dynamic>{'record_id': 'a' * 64, 'review_state': 'accepted'},
        ],
      });
    },
  );

  test('lists, creates, and revokes owner-scoped consent records', () async {
    final List<http.Request> requests = <http.Request>[];
    final ApiClient client = ApiClient(
      client: MockClient((http.Request request) async {
        try {
          requests.add(request);
          if (request.method == 'GET') {
            return http.Response.bytes(
              utf8.encode(
                jsonEncode(<String, dynamic>{
                  'consents': <Map<String, dynamic>>[
                    <String, dynamic>{
                      'id': 'consent-1',
                      'persona_id': 'persona-1',
                      'provider_id': 'deepseek',
                      'model_id': 'deepseek-v4-flash',
                      'data_category': 'image',
                      'estimated_cost': 0.12,
                      'purpose': '图片理解',
                      'authorization_scope': 'persona-image-analysis',
                      'created_at': '2026-08-14T00:00:00+00:00',
                      'status': 'active',
                    },
                  ],
                }),
              ),
              200,
              headers: <String, String>{
                'content-type': 'application/json; charset=utf-8',
              },
            );
          }
          return http.Response.bytes(
            utf8.encode(
              jsonEncode(<String, dynamic>{
                'id': 'consent-1',
                'persona_id': 'persona-1',
                'provider_id': 'deepseek',
                'model_id': 'deepseek-v4-flash',
                'data_category': 'image',
                'estimated_cost': 0.12,
                'purpose': '图片理解',
                'authorization_scope': 'persona-image-analysis',
                'created_at': '2026-08-14T00:00:00+00:00',
                'status':
                    request.url.path.endsWith('/revoke') ? 'revoked' : 'active',
                if (request.url.path.endsWith('/revoke'))
                  'revoked_at': '2026-08-14T01:00:00+00:00',
              }),
            ),
            request.url.path.endsWith('/revoke') ? 200 : 201,
            headers: <String, String>{
              'content-type': 'application/json; charset=utf-8',
            },
          );
        } catch (_) {
          rethrow;
        }
      }),
    );

    final List<Map<String, dynamic>> listed = await client.listConsents(
      endpoint,
      session,
      'persona-1',
    );
    final Map<String, dynamic> created =
        await client.createConsent(endpoint, session, <String, dynamic>{
      'persona_id': 'persona-1',
      'provider_id': 'deepseek',
      'model_id': 'deepseek-v4-flash',
      'data_category': 'image',
      'estimated_cost': 0.12,
      'purpose': '图片理解',
      'authorization_scope': 'persona-image-analysis',
    });
    final Map<String, dynamic> revoked = await client.revokeConsent(
      endpoint,
      session,
      'consent-1',
    );

    expect(listed.single['status'], 'active');
    expect(created['id'], 'consent-1');
    expect(revoked['status'], 'revoked');
    expect(requests[0].url.path, '/api/v1/consents');
    expect(requests[0].url.queryParameters['persona_id'], 'persona-1');
    expect(jsonDecode(requests[1].body), <String, dynamic>{
      'persona_id': 'persona-1',
      'provider_id': 'deepseek',
      'model_id': 'deepseek-v4-flash',
      'data_category': 'image',
      'estimated_cost': 0.12,
      'purpose': '图片理解',
      'authorization_scope': 'persona-image-analysis',
    });
    expect(requests[2].url.path, '/api/v1/consents/consent-1/revoke');
    expect(requests[2].headers['authorization'], 'Bearer token');
  });

  test(
    'exports owner data and deletes a persona through authenticated routes',
    () async {
      final List<http.Request> requests = <http.Request>[];
      final ApiClient client = ApiClient(
        client: MockClient((http.Request request) async {
          requests.add(request);
          if (request.method == 'GET') {
            return http.Response(
              jsonEncode(<String, dynamic>{
                'export_version': 1,
                'generated_at': '2026-08-17T00:00:00Z',
                'scope': <String, dynamic>{
                  'raw_payloads_included': false,
                  'omitted': <String>[
                    'raw_import_payloads',
                    'provider_side_data',
                  ],
                },
                'personas': <Map<String, dynamic>>[],
                'imports': <Map<String, dynamic>>[],
                'consents': <Map<String, dynamic>>[],
                'training_jobs': <Map<String, dynamic>>[],
                'conversations': <Map<String, dynamic>>[],
              }),
              200,
            );
          }
          return http.Response(
            jsonEncode(<String, dynamic>{
              'persona_id': 'persona-1',
              'deleted': true,
            }),
            200,
          );
        }),
      );

      final Map<String, dynamic> exported = await client.exportData(
        endpoint,
        session,
      );
      final Map<String, dynamic> deleted = await client.deletePersona(
        endpoint,
        session,
        'persona-1',
      );

      expect(exported['export_version'], 1);
      expect(deleted['deleted'], true);
      expect(requests[0].method, 'GET');
      expect(requests[0].url.path, '/api/v1/data-export');
      expect(requests[0].headers['authorization'], 'Bearer token');
      expect(requests[1].method, 'DELETE');
      expect(requests[1].url.path, '/api/v1/personas/persona-1');
      expect(requests[1].headers['authorization'], 'Bearer token');
    },
  );

  test(
    'creates, reads, lists, and sends a persona-scoped conversation',
    () async {
      final List<http.Request> requests = <http.Request>[];
      final Map<String, dynamic> conversation = <String, dynamic>{
        'id': 'conversation-1',
        'persona_id': 'persona-1',
        'provider_id': 'test',
        'model_id': 'deterministic',
        'created_at': '2026-08-14T00:00:00Z',
        'updated_at': '2026-08-14T00:00:00Z',
        'messages': <Map<String, dynamic>>[],
      };
      int call = 0;
      final ApiClient client = ApiClient(
        client: MockClient((http.Request request) async {
          requests.add(request);
          final int current = call++;
          if (current == 1) {
            return http.Response(
              jsonEncode(<String, dynamic>{
                'conversations': <Map<String, dynamic>>[conversation],
              }),
              200,
            );
          }
          if (current == 3) {
            final Map<String, dynamic> sent =
                Map<String, dynamic>.from(conversation)
                  ..['messages'] = <Map<String, dynamic>>[
                    <String, dynamic>{
                      'id': 'message-1',
                      'role': 'user',
                      'content': '你好',
                      'created_at': '2026-08-14T00:00:01Z',
                    },
                    <String, dynamic>{
                      'id': 'message-2',
                      'role': 'assistant',
                      'content': '测试回复：你好',
                      'created_at': '2026-08-14T00:00:02Z',
                    },
                  ];
            return http.Response.bytes(utf8.encode(jsonEncode(sent)), 200);
          }
          return http.Response(
              jsonEncode(conversation), current == 0 ? 201 : 200);
        }),
      );

      await client.createConversation(
        endpoint,
        session,
        personaId: 'persona-1',
        providerId: 'test',
        modelId: 'deterministic',
      );
      await client.listConversations(endpoint, session, personaId: 'persona-1');
      await client.getConversation(endpoint, session, 'conversation-1');
      await client.sendConversationMessage(
        endpoint,
        session,
        'conversation-1',
        '你好',
      );

      expect(requests[0].url.path, '/api/v1/conversations');
      expect(jsonDecode(requests[0].body), <String, dynamic>{
        'persona_id': 'persona-1',
        'provider_id': 'test',
        'model_id': 'deterministic',
      });
      expect(requests[1].url.queryParameters['persona_id'], 'persona-1');
      expect(requests[2].url.path, '/api/v1/conversations/conversation-1');
      expect(
        requests[3].url.path,
        '/api/v1/conversations/conversation-1/messages',
      );
      expect(jsonDecode(requests[3].body), <String, dynamic>{'content': '你好'});
    },
  );
}
