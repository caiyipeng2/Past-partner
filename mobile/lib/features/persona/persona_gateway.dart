import '../../core/config/api_endpoint.dart';
import '../../core/network/api_client.dart';
import '../../core/session/session.dart';
import 'persona.dart';

abstract interface class PersonaGateway {
  Future<List<Persona>> list(
      {required ApiEndpoint endpoint, required Session session});

  Future<Persona> create({
    required ApiEndpoint endpoint,
    required Session session,
    required PersonaDraft draft,
  });
}

class ApiClientPersonaGateway implements PersonaGateway {
  const ApiClientPersonaGateway(this.client);

  final ApiClient client;

  @override
  Future<List<Persona>> list(
      {required ApiEndpoint endpoint, required Session session}) async {
    final List<Map<String, dynamic>> values =
        await client.listPersonas(endpoint, session);
    return values.map(Persona.fromJson).toList(growable: false);
  }

  @override
  Future<Persona> create(
      {required ApiEndpoint endpoint,
      required Session session,
      required PersonaDraft draft}) async {
    return Persona.fromJson(
        await client.createPersona(endpoint, session, draft.toJson()));
  }
}
