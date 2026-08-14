import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/network/api_client.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/features/imports/import_review.dart';
import 'package:past_partner/features/imports/import_review_gateway.dart';

class _StubApiClient extends ApiClient {
  Map<String, dynamic> previewResponse = <String, dynamic>{};
  Map<String, dynamic> mappingResponse = <String, dynamic>{};
  Map<String, String>? savedMapping;
  List<Map<String, dynamic>>? savedCorrections;

  @override
  Future<Map<String, dynamic>> getImportPreview(
    ApiEndpoint endpoint,
    Session session,
    String importId, {
    int limit = 20,
  }) async =>
      previewResponse;

  @override
  Future<Map<String, dynamic>> getParticipantMapping(
    ApiEndpoint endpoint,
    Session session,
    String importId,
  ) async =>
      mappingResponse;

  @override
  Future<Map<String, dynamic>> saveParticipantMapping(
    ApiEndpoint endpoint,
    Session session,
    String importId,
    Map<String, String> mapping,
  ) async {
    savedMapping = mapping;
    return <String, dynamic>{'mapping': mapping};
  }

  @override
  Future<void> saveImportCorrections(
    ApiEndpoint endpoint,
    Session session,
    String importId,
    List<Map<String, dynamic>> corrections,
  ) async {
    savedCorrections = corrections;
  }
}

void main() {
  final ApiEndpoint endpoint = ApiEndpoint.parseDebug('http://127.0.0.1:8080');
  final Session session = Session(
    accessToken: 'token',
    ownerId: 'owner',
    expiresAt: DateTime.utc(2099),
  );

  test('decodes preview and submits mapping and corrections', () async {
    final _StubApiClient client = _StubApiClient()
      ..previewResponse = <String, dynamic>{
        'import_id': 'import-1',
        'state': 'uploaded',
        'source_name': 'wechat.txt',
        'media_type': 'text/plain',
        'source_type': 'wechat',
        'summary': <String, dynamic>{
          'record_count': 2,
          'warning_count': 1,
          'confidence': 0.75,
          'truncated': false,
        },
        'warnings': <dynamic>['存在一条缺少时间戳的消息'],
        'records': <dynamic>[
          <String, dynamic>{
            'record_id': 'a' * 64,
            'sender_id': 'wx-a',
            'sender_name': '小雅',
            'content': '你好',
            'timestamp': '2026-08-13T00:00:00Z',
            'message_type': 'text',
            'review_state': 'needs_review',
          },
        ],
        'file_summaries': <dynamic>[],
      }
      ..mappingResponse = <String, dynamic>{
        'mapping': <String, dynamic>{'wx-a': 'persona'},
      };
    final ApiClientImportReviewGateway gateway =
        ApiClientImportReviewGateway(client);

    final ImportPreview preview = await gateway.preview(
      endpoint: endpoint,
      session: session,
      importId: 'import-1',
      limit: 10,
    );
    final Map<String, String> mapping = await gateway.mapping(
      endpoint: endpoint,
      session: session,
      importId: 'import-1',
    );
    await gateway.saveMapping(
      endpoint: endpoint,
      session: session,
      importId: 'import-1',
      mapping: <String, String>{'wx-a': 'user'},
    );
    await gateway.saveCorrections(
      endpoint: endpoint,
      session: session,
      importId: 'import-1',
      corrections: <ImportCorrection>[
        ImportCorrection(
          recordId: 'a' * 64,
          reviewState: ReviewState.accepted,
        ),
      ],
    );

    expect(preview.records.single.content, '你好');
    expect(preview.summary.warningCount, 1);
    expect(mapping, <String, String>{'wx-a': 'persona'});
    expect(client.savedMapping, <String, String>{'wx-a': 'user'});
    expect(client.savedCorrections, <Map<String, dynamic>>[
      <String, dynamic>{'record_id': 'a' * 64, 'review_state': 'accepted'},
    ]);
  });

  test('rejects malformed preview records', () async {
    final _StubApiClient client = _StubApiClient()
      ..previewResponse = <String, dynamic>{
        'import_id': 'import-1',
        'state': 'uploaded',
        'source_name': 'chat.txt',
        'media_type': 'text/plain',
        'source_type': 'qq',
        'summary': <String, dynamic>{'record_count': 1},
        'warnings': <dynamic>[],
        'records': <dynamic>[
          <String, dynamic>{'record_id': 'bad'}
        ],
        'file_summaries': <dynamic>[],
      };
    final ApiClientImportReviewGateway gateway =
        ApiClientImportReviewGateway(client);

    expect(
      () => gateway.preview(
        endpoint: endpoint,
        session: session,
        importId: 'import-1',
      ),
      throwsA(isA<FormatException>()),
    );
  });
}
