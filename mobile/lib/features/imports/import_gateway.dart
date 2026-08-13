import '../../core/config/api_endpoint.dart';
import '../../core/network/api_client.dart';
import '../../core/session/session.dart';
import 'import_job.dart';

abstract interface class ImportGateway {
  Future<List<ImportJob>> list({
    required ApiEndpoint endpoint,
    required Session session,
    required String personaId,
  });

  Future<ImportJob> create({
    required ApiEndpoint endpoint,
    required Session session,
    required ImportDraft draft,
  });
}

abstract interface class ImportUploadGateway {
  Future<Map<String, dynamic>> missingChunks({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    required int expectedChunks,
  });

  Future<Map<String, dynamic>> putChunk({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    required int index,
    required List<int> bytes,
    required String sha256,
  });

  Future<ImportJob> complete({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    String? wholeSha256,
  });
}

class ApiClientImportGateway implements ImportGateway, ImportUploadGateway {
  const ApiClientImportGateway(this.client);

  final ApiClient client;

  @override
  Future<List<ImportJob>> list({
    required ApiEndpoint endpoint,
    required Session session,
    required String personaId,
  }) async {
    final List<Map<String, dynamic>> values =
        await client.listImports(endpoint, session, personaId);
    return values.map(ImportJob.fromJson).toList(growable: false);
  }

  @override
  Future<ImportJob> create({
    required ApiEndpoint endpoint,
    required Session session,
    required ImportDraft draft,
  }) async {
    return ImportJob.fromJson(
        await client.createImport(endpoint, session, draft.toJson()));
  }

  @override
  Future<Map<String, dynamic>> missingChunks({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    required int expectedChunks,
  }) =>
      client.missingChunks(endpoint, session, importId,
          expectedChunks: expectedChunks);

  @override
  Future<Map<String, dynamic>> putChunk({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    required int index,
    required List<int> bytes,
    required String sha256,
  }) =>
      client.putChunk(endpoint, session, importId, index, bytes, sha256);

  @override
  Future<ImportJob> complete({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    String? wholeSha256,
  }) async {
    return ImportJob.fromJson(await client.completeImport(
      endpoint,
      session,
      importId,
      wholeSha256: wholeSha256,
    ));
  }
}
