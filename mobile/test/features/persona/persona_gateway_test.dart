import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/network/api_client.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/features/persona/persona.dart';
import 'package:past_partner/features/persona/persona_gateway.dart';

class _StubApiClient extends ApiClient {
  _StubApiClient();
  ApiEndpoint? listEndpoint;
  Session? listSession;
  ApiEndpoint? createEndpoint;
  Session? createSession;
  Map<String, dynamic>? payload;

  @override
  Future<List<Map<String, dynamic>>> listPersonas(
      ApiEndpoint endpoint, Session session) async {
    listEndpoint = endpoint;
    listSession = session;
    return <Map<String, dynamic>>[
      <String, dynamic>{
        'id': 'p1',
        'display_name': '妈妈',
        'relationship_type': 'mother',
        'custom_label': null
      },
    ];
  }

  @override
  Future<Map<String, dynamic>> createPersona(
      ApiEndpoint endpoint, Session session, Map<String, dynamic> value) async {
    createEndpoint = endpoint;
    createSession = session;
    payload = value;
    return <String, dynamic>{
      'id': 'p2',
      'display_name': '小雅',
      'relationship_type': 'custom',
      'custom_label': '旧友'
    };
  }
}

void main() {
  final ApiEndpoint endpoint = ApiEndpoint.parseDebug('http://127.0.0.1:8080');
  final Session session = Session(
      accessToken: 'secret', ownerId: 'owner', expiresAt: DateTime.utc(2099));

  test('decodes list and sends authenticated create payload', () async {
    final _StubApiClient client = _StubApiClient();
    final ApiClientPersonaGateway gateway = ApiClientPersonaGateway(client);

    final List<Persona> personas =
        await gateway.list(endpoint: endpoint, session: session);
    final Persona created = await gateway.create(
      endpoint: endpoint,
      session: session,
      draft: const PersonaDraft(
          displayName: '小雅',
          relationshipType: PersonaRelationship.custom,
          customLabel: '旧友'),
    );

    expect(personas.single.relationshipType, PersonaRelationship.mother);
    expect(created.customLabel, '旧友');
    expect(client.listEndpoint, endpoint);
    expect(client.listSession, session);
    expect(client.createEndpoint, endpoint);
    expect(client.createSession, session);
    expect(client.payload, <String, dynamic>{
      'display_name': '小雅',
      'relationship_type': 'custom',
      'custom_label': '旧友'
    });
  });
}
