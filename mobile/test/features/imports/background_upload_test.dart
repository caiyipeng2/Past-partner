import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/features/imports/background_upload.dart';
import 'package:past_partner/features/imports/import_file.dart';
import 'package:past_partner/features/imports/import_gateway.dart';
import 'package:past_partner/features/imports/import_job.dart';
import 'package:past_partner/features/imports/import_resume.dart';
import 'package:past_partner/features/imports/import_upload_controller.dart';

class _Scheduler implements BackgroundUploadScheduler {
  final List<BackgroundUploadRequest> enqueued = <BackgroundUploadRequest>[];
  final List<BackgroundUploadUpdate> updates = <BackgroundUploadUpdate>[];
  final List<String> cancelled = <String>[];

  @override
  Future<void> enqueue(BackgroundUploadRequest request) async {
    enqueued.add(request);
  }

  @override
  Future<void> report(BackgroundUploadUpdate update) async {
    updates.add(update);
  }

  @override
  Future<void> cancel(String importId) async {
    cancelled.add(importId);
  }
}

class _Gateway implements ImportUploadGateway {
  _Gateway({this.failUpload = false});

  final bool failUpload;
  int completeCalls = 0;

  @override
  Future<Map<String, dynamic>> missingChunks({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    required int expectedChunks,
  }) async {
    return <String, dynamic>{
      'import_id': importId,
      'expected_chunk_count': expectedChunks,
      'received_bytes': 0,
      'received_chunks': <int>[],
      'missing_chunks': <int>[0],
    };
  }

  @override
  Future<Map<String, dynamic>> putChunk({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    required int index,
    required List<int> bytes,
    required String sha256,
  }) async {
    if (failUpload) throw const ImportFileError('network');
    return <String, dynamic>{
      'import_id': importId,
      'index': index,
      'length': bytes.length,
      'sha256': sha256,
      'duplicate': false,
      'received_bytes': bytes.length,
      'total_bytes': bytes.length,
    };
  }

  @override
  Future<ImportJob> complete({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    String? wholeSha256,
  }) async {
    completeCalls++;
    return ImportJob(
      id: importId,
      personaId: 'persona-1',
      sourceName: 'chat.txt',
      mediaType: 'text/plain',
      totalBytes: 4,
      receivedBytes: 4,
      chunkCount: 1,
      state: ImportState.uploaded,
      createdAt: '2026-08-26T00:00:00Z',
      updatedAt: '2026-08-26T00:00:00Z',
    );
  }

  Future<ImportJob> create() async {
    return ImportJob(
      id: 'import-1',
      personaId: 'persona-1',
      sourceName: 'chat.txt',
      mediaType: 'text/plain',
      totalBytes: 4,
      receivedBytes: 0,
      chunkCount: 1,
      state: ImportState.created,
      createdAt: '2026-08-26T00:00:00Z',
      updatedAt: '2026-08-26T00:00:00Z',
    );
  }
}

void main() {
  final ApiEndpoint endpoint = ApiEndpoint.parseDebug('http://127.0.0.1:8080');
  final Session session = Session(
    accessToken: 'token',
    ownerId: 'owner',
    expiresAt: DateTime.utc(2099),
  );

  ImportUploadController buildController({
    required _Gateway gateway,
    required _Scheduler scheduler,
    InMemoryImportResumeStore? resumeStore,
  }) {
    return ImportUploadController(
      endpoint: endpoint,
      session: session,
      personaId: 'persona-1',
      gateway: gateway,
      createImport: (_) => gateway.create(),
      resumeStore: resumeStore,
      backgroundScheduler: scheduler,
      chunkSize: 4,
    );
  }

  test('reports enqueue, progress and completion cleanup', () async {
    final _Scheduler scheduler = _Scheduler();
    final _Gateway gateway = _Gateway();
    final ImportUploadController controller = buildController(
      gateway: gateway,
      scheduler: scheduler,
      resumeStore: InMemoryImportResumeStore(),
    );

    await controller.upload(<LocalImportFile>[
      MemoryImportFile(
        sourceName: 'chat.txt',
        mediaType: 'text/plain',
        bytes: <int>[0, 1, 2, 3],
      ),
    ]);

    expect(scheduler.enqueued, hasLength(1));
    expect(scheduler.enqueued.single.importId, 'import-1');
    expect(
        scheduler.updates.map((BackgroundUploadUpdate item) => item.state),
        containsAll(<BackgroundUploadState>[
          BackgroundUploadState.running,
          BackgroundUploadState.completed,
        ]));
    expect(gateway.completeCalls, 1);
    expect(controller.state, ImportUploadState.ready);
  });

  test('reports a retryable failure without leaking the provider error',
      () async {
    final _Scheduler scheduler = _Scheduler();
    final ImportUploadController controller = buildController(
      gateway: _Gateway(failUpload: true),
      scheduler: scheduler,
    );

    await controller.upload(<LocalImportFile>[
      MemoryImportFile(
        sourceName: 'chat.txt',
        mediaType: 'text/plain',
        bytes: <int>[0, 1, 2, 3],
      ),
    ]);

    final BackgroundUploadUpdate retry = scheduler.updates.last;
    expect(retry.state, BackgroundUploadState.retrying);
    expect(retry.errorMessage, '文件上传失败，请重试。');
    expect(retry.errorMessage, isNot(contains('network')));
    expect(controller.state, ImportUploadState.error);
  });

  test('cancellation delegates to the scheduler for a persisted job', () async {
    final _Scheduler scheduler = _Scheduler();
    final ImportUploadController controller = buildController(
      gateway: _Gateway(),
      scheduler: scheduler,
    );

    await controller.cancelBackgroundUpload(importId: 'import-1');

    expect(scheduler.cancelled, <String>['import-1']);
  });

  test('iOS uses the no-op scheduler until a native path is planned', () {
    expect(
      backgroundUploadSchedulerForPlatform(platform: TargetPlatform.iOS),
      isA<NoopBackgroundUploadScheduler>(),
    );
  });

  test('scheduler payload contains no access or pairing secrets', () {
    final Map<String, dynamic> request = const BackgroundUploadRequest(
      importId: 'import-1',
      personaId: 'persona-1',
      totalBytes: 4,
      chunkCount: 1,
    ).toJson();
    final Map<String, dynamic> update = const BackgroundUploadUpdate(
      importId: 'import-1',
      state: BackgroundUploadState.running,
      receivedBytes: 2,
      totalBytes: 4,
    ).toJson();

    expect(request.keys, isNot(contains('access_token')));
    expect(request.keys, isNot(contains('pairing_token')));
    expect(update.keys, isNot(contains('access_token')));
    expect(update.keys, isNot(contains('pairing_token')));
  });
}
