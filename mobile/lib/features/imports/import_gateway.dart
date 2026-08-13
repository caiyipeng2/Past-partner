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

class ApiClientImportGateway implements ImportGateway {
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
}
