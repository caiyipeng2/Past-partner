import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/features/imports/import_review.dart';
import 'package:past_partner/features/imports/import_review_controller.dart';
import 'package:past_partner/features/imports/import_review_gateway.dart';
import 'package:past_partner/features/imports/import_review_screen.dart';

class _Gateway implements ImportReviewGateway {
  final ImportPreview previewValue = ImportPreview.fromJson(<String, dynamic>{
    'import_id': 'import-1',
    'state': 'uploaded',
    'source_name': '微信聊天记录',
    'media_type': 'text/plain',
    'source_type': 'wechat',
    'summary': <String, dynamic>{
      'record_count': 1,
      'warning_count': 1,
      'confidence': 0.8,
      'truncated': true,
    },
    'warnings': <dynamic>['发送者身份还未确认'],
    'records': <dynamic>[
      <String, dynamic>{
        'record_id': 'a' * 64,
        'sender_id': 'wx-a',
        'sender_name': '小雅',
        'content': '你好，最近怎么样？',
        'timestamp': '2026-08-13T00:00:00Z',
        'message_type': 'text',
        'review_state': 'needs_review',
      },
    ],
    'file_summaries': <dynamic>[],
  });

  @override
  Future<ImportPreview> preview({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    int limit = 20,
  }) async =>
      previewValue;

  @override
  Future<Map<String, String>> mapping({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
  }) async =>
      <String, String>{'wx-a': 'unknown'};

  @override
  Future<void> saveMapping({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    required Map<String, String> mapping,
  }) async {}

  @override
  Future<void> saveCorrections({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    required List<ImportCorrection> corrections,
  }) async {}
}

void main() {
  testWidgets('renders review summary, mapping, warnings and records',
      (WidgetTester tester) async {
    final ImportReviewController controller = ImportReviewController(
      endpoint: ApiEndpoint.parseDebug('http://127.0.0.1:8080'),
      session: Session(
          accessToken: 'token',
          ownerId: 'owner',
          expiresAt: DateTime.utc(2099)),
      importId: 'import-1',
      gateway: _Gateway(),
    );
    await tester.pumpWidget(MaterialApp(
      home: ImportReviewScreen(controller: controller),
    ));
    await tester.pumpAndSettle();

    expect(find.text('导入审核'), findsOneWidget);
    expect(find.text('微信聊天记录'), findsOneWidget);
    expect(find.text('需要留意的警告（1）'), findsOneWidget);
    expect(find.text('参与者身份'), findsOneWidget);
    expect(find.text('wx-a'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('你好，最近怎么样？'),
      400,
      scrollable: find.byType(Scrollable).last,
    );
    expect(find.text('你好，最近怎么样？'), findsOneWidget);
    expect(find.byKey(const Key('save-participant-mapping-button')),
        findsOneWidget);
    expect(find.byKey(const Key('save-corrections-button')), findsOneWidget);
  });
}
