import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/network/api_client.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/core/session/session_controller.dart';
import 'package:past_partner/core/session/session_store.dart';
import 'package:past_partner/features/imports/import_controller.dart';
import 'package:past_partner/features/imports/import_gateway.dart';
import 'package:past_partner/features/imports/import_job.dart';
import 'package:past_partner/features/imports/import_workspace_screen.dart';
import 'package:past_partner/features/persona/persona.dart';

class _Store implements SessionStore {
  @override
  Future<void> clear() async {}
  @override
  Future<Session?> read() async => null;
  @override
  Future<void> write(Session session) async {}
}

class _Gateway implements ImportGateway {
  final List<ImportJob> jobs = <ImportJob>[];
  bool fail = false;

  @override
  Future<List<ImportJob>> list({
    required ApiEndpoint endpoint,
    required Session session,
    required String personaId,
  }) async {
    if (fail) throw Exception();
    return List<ImportJob>.of(jobs);
  }

  @override
  Future<ImportJob> create({
    required ApiEndpoint endpoint,
    required Session session,
    required ImportDraft draft,
  }) async {
    final ImportJob job = ImportJob(
      id: 'import-1',
      personaId: draft.personaId,
      sourceName: draft.sourceName,
      mediaType: draft.mediaType,
      totalBytes: draft.totalBytes,
      receivedBytes: 0,
      chunkCount: 0,
      state: ImportState.created,
      createdAt: '2026-08-13T00:00:00Z',
      updatedAt: '2026-08-13T00:00:00Z',
    );
    jobs.add(job);
    return job;
  }
}

void main() {
  final Persona persona = const Persona(
    id: 'persona-1',
    displayName: '小雅',
    relationshipType: PersonaRelationship.friend,
  );

  ImportController controller(_Gateway gateway) {
    final SessionController sessionController =
        SessionController(_Store(), ApiClient())
          ..session = Session(
              accessToken: 'token',
              ownerId: 'owner',
              expiresAt: DateTime.utc(2099))
          ..endpoint = ApiEndpoint.parseDebug('http://127.0.0.1:8080');
    return ImportController(sessionController,
        personaId: persona.id, gateway: gateway);
  }

  testWidgets('validates metadata and shows created import status',
      (WidgetTester tester) async {
    final _Gateway gateway = _Gateway();
    await tester.pumpWidget(MaterialApp(
      home: ImportWorkspaceScreen(
        persona: persona,
        controller: controller(gateway),
      ),
    ));
    await tester.pumpAndSettle();

    expect(find.text('还没有导入任务'), findsOneWidget);
    await tester.tap(find.byKey(const Key('create-import-task-button')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('save-import-task-button')));
    await tester.pumpAndSettle();
    expect(find.text('请输入来源名称'), findsOneWidget);
    expect(find.text('请输入媒体类型'), findsOneWidget);
    expect(find.text('请输入非负字节数'), findsOneWidget);

    await tester.enterText(
        find.byKey(const Key('import-source-name')), 'chat.txt');
    await tester.enterText(
        find.byKey(const Key('import-media-type')), 'text/plain');
    await tester.enterText(find.byKey(const Key('import-total-bytes')), '12');
    await tester.tap(find.text('保存导入任务'));
    await tester.pumpAndSettle();

    expect(find.text('chat.txt'), findsOneWidget);
    expect(find.textContaining('待上传'), findsOneWidget);
    expect(find.textContaining('0 B / 12 B'), findsOneWidget);
  });

  testWidgets('shows stable retry state when loading fails',
      (WidgetTester tester) async {
    final _Gateway gateway = _Gateway()..fail = true;
    await tester.pumpWidget(MaterialApp(
      home: ImportWorkspaceScreen(
        persona: persona,
        controller: controller(gateway),
      ),
    ));
    await tester.pumpAndSettle();

    expect(find.text('导入任务加载失败，请重试。'), findsOneWidget);
    expect(find.text('重试加载'), findsOneWidget);
  });
}
