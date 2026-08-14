import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/features/imports/import_review.dart';
import 'package:past_partner/features/imports/import_review_controller.dart';
import 'package:past_partner/features/imports/import_review_gateway.dart';

class _StubGateway implements ImportReviewGateway {
  bool fail = false;
  final ImportPreview previewValue = ImportPreview.fromJson(<String, dynamic>{
    'import_id': 'import-1',
    'state': 'uploaded',
    'source_name': 'wechat.txt',
    'media_type': 'text/plain',
    'source_type': 'wechat',
    'summary': <String, dynamic>{
      'record_count': 1,
      'warning_count': 0,
      'confidence': 1,
      'truncated': false,
    },
    'warnings': <dynamic>[],
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
  });
  final Map<String, String> mappingValue = <String, String>{'wx-a': 'unknown'};
  Map<String, String>? savedMapping;
  List<ImportCorrection>? savedCorrections;

  @override
  Future<ImportPreview> preview({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    int limit = 20,
  }) async {
    if (fail) throw Exception('offline');
    return previewValue;
  }

  @override
  Future<Map<String, String>> mapping({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
  }) async {
    if (fail) throw Exception('offline');
    return mappingValue;
  }

  @override
  Future<void> saveMapping({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    required Map<String, String> mapping,
  }) async {
    if (fail) throw Exception('offline');
    savedMapping = mapping;
  }

  @override
  Future<void> saveCorrections({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    required List<ImportCorrection> corrections,
  }) async {
    if (fail) throw Exception('offline');
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

  test('loads preview and persists record and participant edits', () async {
    final _StubGateway gateway = _StubGateway();
    final ImportReviewController controller = ImportReviewController(
      endpoint: endpoint,
      session: session,
      importId: 'import-1',
      gateway: gateway,
    );

    await controller.load();
    expect(controller.state, ImportReviewState.ready);
    expect(controller.preview!.records.single.content, '你好');
    expect(controller.mapping['wx-a'], 'unknown');

    controller.setMapping('wx-a', ParticipantRole.persona);
    await controller.saveMapping();
    controller.setReviewState('a' * 64, ReviewState.accepted);
    await controller.saveCorrections();

    expect(gateway.savedMapping, <String, String>{'wx-a': 'persona'});
    expect(gateway.savedCorrections!.single.reviewState, ReviewState.accepted);
  });

  test('exposes stable retry message when review loading fails', () async {
    final ImportReviewController controller = ImportReviewController(
      endpoint: endpoint,
      session: session,
      importId: 'import-1',
      gateway: (_StubGateway()..fail = true),
    );

    await controller.load();
    expect(controller.state, ImportReviewState.error);
    expect(controller.errorMessage, '导入审核加载失败，请重试。');
  });
}
