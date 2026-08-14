import '../../core/config/api_endpoint.dart';
import '../../core/network/api_client.dart';
import '../../core/session/session.dart';
import 'import_review.dart';

abstract interface class ImportReviewGateway {
  Future<ImportPreview> preview({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    int limit = 20,
  });

  Future<Map<String, String>> mapping({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
  });

  Future<void> saveMapping({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    required Map<String, String> mapping,
  });

  Future<void> saveCorrections({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    required List<ImportCorrection> corrections,
  });
}

class ApiClientImportReviewGateway implements ImportReviewGateway {
  const ApiClientImportReviewGateway(this.client);

  final ApiClient client;

  @override
  Future<ImportPreview> preview({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    int limit = 20,
  }) async {
    return ImportPreview.fromJson(
      await client.getImportPreview(endpoint, session, importId, limit: limit),
    );
  }

  @override
  Future<Map<String, String>> mapping({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
  }) async {
    final Map<String, dynamic> response =
        await client.getParticipantMapping(endpoint, session, importId);
    final dynamic value = response['mapping'];
    if (value is! Map || value.length > 4096) {
      throw const FormatException('Invalid participant mapping response.');
    }
    final Map<String, String> result = <String, String>{};
    value.forEach((dynamic key, dynamic role) {
      if (key is! String || key.isEmpty || key.length > 256) {
        throw const FormatException('Invalid participant mapping key.');
      }
      result[key] = ParticipantRole.fromValue(role).value;
    });
    return result;
  }

  @override
  Future<void> saveMapping({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    required Map<String, String> mapping,
  }) async {
    if (mapping.length > 4096) {
      throw const FormatException('Participant mapping is too large.');
    }
    await client.saveParticipantMapping(endpoint, session, importId, mapping);
  }

  @override
  Future<void> saveCorrections({
    required ApiEndpoint endpoint,
    required Session session,
    required String importId,
    required List<ImportCorrection> corrections,
  }) async {
    if (corrections.length > 4096) {
      throw const FormatException('Import corrections are too large.');
    }
    await client.saveImportCorrections(
      endpoint,
      session,
      importId,
      corrections.map((ImportCorrection value) => value.toJson()).toList(),
    );
  }
}
