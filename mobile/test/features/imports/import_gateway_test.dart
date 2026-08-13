import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/network/api_client.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/features/imports/import_gateway.dart';
import 'package:past_partner/features/imports/import_job.dart';

class _StubApiClient extends ApiClient {
  List<Map<String, dynamic>> listed = <Map<String, dynamic>>[];
  Map<String, dynamic>? createdPayload;

  @override
  Future<List<Map<String, dynamic>>> listImports(
      ApiEndpoint endpoint, Session session, String personaId) async {
    return listed;
  }

  @override
  Future<Map<String, dynamic>> createImport(ApiEndpoint endpoint,
      Session session, Map<String, dynamic> payload) async {
    createdPayload = payload;
    return <String, dynamic>{
      'id': 'import-1',
      'persona_id': 'persona-1',
      'source_name': 'chat.txt',
      'media_type': 'text/plain',
      'total_bytes': 12,
      'received_bytes': 0,
      'chunk_count': 0,
      'state': 'created',
      'created_at': '2026-08-13T00:00:00Z',
      'updated_at': '2026-08-13T00:00:00Z',
    };
  }
}

void main() {
  final ApiEndpoint endpoint = ApiEndpoint.parseDebug('http://127.0.0.1:8080');
  final Session session = Session(
      accessToken: 'token', ownerId: 'owner', expiresAt: DateTime.utc(2099));

  test('decodes import jobs and submits metadata for the selected persona',
      () async {
    final _StubApiClient client = _StubApiClient()
      ..listed = <Map<String, dynamic>>[
        <String, dynamic>{
          'id': 'import-2',
          'persona_id': 'persona-1',
          'source_name': 'wechat.txt',
          'media_type': 'text/plain',
          'total_bytes': 20,
          'received_bytes': 10,
          'chunk_count': 1,
          'state': 'uploading',
          'created_at': '2026-08-13T00:00:00Z',
          'updated_at': '2026-08-13T00:01:00Z',
        }
      ];
    final ApiClientImportGateway gateway = ApiClientImportGateway(client);

    final List<ImportJob> jobs = await gateway.list(
        endpoint: endpoint, session: session, personaId: 'persona-1');
    final ImportJob created = await gateway.create(
      endpoint: endpoint,
      session: session,
      draft: const ImportDraft(
        personaId: 'persona-1',
        sourceName: 'chat.txt',
        totalBytes: 12,
        mediaType: 'text/plain',
      ),
    );

    expect(jobs.single.state, ImportState.uploading);
    expect(jobs.single.progressLabel, '10 B / 20 B');
    expect(created.id, 'import-1');
    expect(client.createdPayload, <String, dynamic>{
      'persona_id': 'persona-1',
      'source_name': 'chat.txt',
      'total_bytes': 12,
      'media_type': 'text/plain',
    });
  });

  test('rejects malformed import job responses', () async {
    final _StubApiClient client = _StubApiClient()
      ..listed = <Map<String, dynamic>>[
        <String, dynamic>{'id': 'missing-state'}
      ];
    final ApiClientImportGateway gateway = ApiClientImportGateway(client);

    expect(
      () => gateway.list(
          endpoint: endpoint, session: session, personaId: 'persona-1'),
      throwsA(isA<FormatException>()),
    );
  });
}
