import '../../core/config/api_endpoint.dart';
import '../../core/network/api_client.dart';
import '../../core/session/session.dart';
import 'privacy_export.dart';

abstract interface class PrivacyGateway {
  Future<PrivacyExportSummary> exportData();

  Future<void> deletePersona(String personaId);
}

class ApiClientPrivacyGateway implements PrivacyGateway {
  const ApiClientPrivacyGateway({
    required this.client,
    required this.endpoint,
    required this.session,
  });

  final ApiClient client;
  final ApiEndpoint endpoint;
  final Session session;

  @override
  Future<PrivacyExportSummary> exportData() async {
    return PrivacyExportSummary.fromJson(
      await client.exportData(endpoint, session),
    );
  }

  @override
  Future<void> deletePersona(String personaId) async {
    final Map<String, dynamic> response = await client.deletePersona(
      endpoint,
      session,
      personaId,
    );
    if (response['deleted'] != true || response['persona_id'] != personaId) {
      throw const FormatException('Persona deletion response is invalid.');
    }
  }
}
