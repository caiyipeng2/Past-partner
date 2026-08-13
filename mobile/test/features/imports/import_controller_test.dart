import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/network/api_client.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/core/session/session_controller.dart';
import 'package:past_partner/core/session/session_store.dart';
import 'package:past_partner/features/imports/import_controller.dart';
import 'package:past_partner/features/imports/import_gateway.dart';
import 'package:past_partner/features/imports/import_job.dart';

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
  bool failCreate = false;
  int listCalls = 0;

  @override
  Future<List<ImportJob>> list({
    required ApiEndpoint endpoint,
    required Session session,
    required String personaId,
  }) async {
    listCalls++;
    return List<ImportJob>.of(jobs);
  }

  @override
  Future<ImportJob> create({
    required ApiEndpoint endpoint,
    required Session session,
    required ImportDraft draft,
  }) async {
    if (failCreate) throw Exception('server body must not reach UI');
    final ImportJob job = ImportJob(
      id: 'import-${jobs.length + 1}',
      personaId: draft.personaId,
      sourceName: draft.sourceName,
      mediaType: draft.mediaType,
      totalBytes: draft.totalBytes,
      receivedBytes: 0,
      chunkCount: 0,
      state: ImportState.created,
      createdAt: DateTime.utc(2026).toIso8601String(),
      updatedAt: DateTime.utc(2026).toIso8601String(),
    );
    jobs.add(job);
    return job;
  }
}

void main() {
  final Session session = Session(
    accessToken: 'token',
    ownerId: 'owner',
    expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
  );
  final ApiEndpoint endpoint = ApiEndpoint.parseDebug('http://127.0.0.1:8080');

  SessionController sessionController() =>
      SessionController(_Store(), ApiClient())
        ..session = session
        ..endpoint = endpoint;

  test('loads and refreshes import jobs for one persona after create',
      () async {
    final _Gateway gateway = _Gateway();
    final ImportController controller = ImportController(
      sessionController(),
      personaId: 'persona-1',
      gateway: gateway,
    );

    await controller.load();
    expect(controller.state, ImportStateView.ready);
    expect(gateway.listCalls, 1);

    await controller.create(const ImportDraft(
      personaId: 'persona-1',
      sourceName: 'chat.txt',
      totalBytes: 12,
      mediaType: 'text/plain',
    ));
    expect(controller.jobs.single.sourceName, 'chat.txt');
    expect(controller.state, ImportStateView.ready);
  });

  test('maps create failures to a stable retryable message', () async {
    final _Gateway gateway = _Gateway()..failCreate = true;
    final ImportController controller = ImportController(
      sessionController(),
      personaId: 'persona-1',
      gateway: gateway,
    );

    await controller.create(const ImportDraft(
      personaId: 'persona-1',
      sourceName: 'chat.txt',
      totalBytes: 12,
      mediaType: 'text/plain',
    ));

    expect(controller.state, ImportStateView.error);
    expect(controller.errorMessage, '导入任务创建失败，请重试。');
  });
}
