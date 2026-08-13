import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/features/imports/import_file.dart';
import 'package:past_partner/features/imports/import_gateway.dart';
import 'package:past_partner/features/imports/import_job.dart';
import 'package:past_partner/features/imports/import_upload_controller.dart';

class _Gateway implements ImportUploadGateway {
  ImportDraft? createdDraft;
  final List<int> uploadedIndexes = <int>[];
  final List<List<int>> uploadedBytes = <List<int>>[];
  final List<String> uploadedDigests = <String>[];
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
      'received_bytes': 4,
      'received_chunks': <int>[0],
      'missing_chunks': <int>[1],
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
    uploadedIndexes.add(index);
    uploadedBytes.add(bytes);
    uploadedDigests.add(sha256);
    return <String, dynamic>{
      'import_id': importId,
      'index': index,
      'length': bytes.length,
      'sha256': sha256,
      'duplicate': false,
      'received_bytes': 6,
      'total_bytes': 6,
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
    return _job(state: ImportState.uploaded);
  }

  Future<ImportJob> create(ImportDraft draft) async {
    createdDraft = draft;
    return _job(state: ImportState.created);
  }

  ImportJob _job({required ImportState state}) => ImportJob(
        id: 'import-1',
        personaId: 'persona-1',
        sourceName: 'chat.txt',
        mediaType: 'text/plain',
        totalBytes: 6,
        receivedBytes: state == ImportState.uploaded ? 6 : 0,
        chunkCount: state == ImportState.uploaded ? 2 : 0,
        state: state,
        createdAt: '2026-08-13T00:00:00Z',
        updatedAt: '2026-08-13T00:00:00Z',
      );
}

void main() {
  final ApiEndpoint endpoint =
      ApiEndpoint.parseDebug('http://127.0.0.1:8080');
  final Session session = Session(
    accessToken: 'token',
    ownerId: 'owner',
    expiresAt: DateTime.utc(2099),
  );

  test('creates a manifest, skips received chunks, hashes and completes',
      () async {
    final _Gateway gateway = _Gateway();
    final ImportUploadController controller = ImportUploadController(
      endpoint: endpoint,
      session: session,
      personaId: 'persona-1',
      gateway: gateway,
      createImport: gateway.create,
      chunkSize: 4,
    );

    await controller.upload(<LocalImportFile>[
      MemoryImportFile(
        sourceName: 'a.txt',
        mediaType: 'text/plain',
        bytes: <int>[0, 1, 2, 3],
      ),
      MemoryImportFile(
        sourceName: 'b.txt',
        mediaType: 'text/plain',
        bytes: <int>[4, 5],
      ),
    ]);

    expect(gateway.createdDraft!.totalBytes, 6);
    expect(gateway.createdDraft!.files, hasLength(2));
    expect(gateway.uploadedIndexes, <int>[1]);
    expect(gateway.uploadedBytes.single, <int>[4, 5]);
    expect(gateway.uploadedDigests.single,
        sha256.convert(<int>[4, 5]).toString());
    expect(gateway.completeCalls, 1);
    expect(controller.state, ImportUploadState.ready);
    expect(controller.job!.state, ImportState.uploaded);
    expect(controller.receivedBytes, 6);
  });

  test('rejects a resumed upload when selected files do not match', () async {
    final _Gateway gateway = _Gateway();
    final ImportJob existing = gateway._job(state: ImportState.uploading);
    final ImportUploadController controller = ImportUploadController(
      endpoint: endpoint,
      session: session,
      personaId: 'persona-1',
      gateway: gateway,
      createImport: gateway.create,
      chunkSize: 4,
    );

    await controller.upload(<LocalImportFile>[
      MemoryImportFile(
        sourceName: 'different.txt',
        mediaType: 'text/plain',
        bytes: <int>[0, 1, 2, 3, 4, 5],
      ),
    ], existingJob: existing);

    expect(controller.state, ImportUploadState.error);
    expect(controller.errorMessage, '选择的文件与原导入任务不匹配。');
    expect(gateway.completeCalls, 0);
  });
}
