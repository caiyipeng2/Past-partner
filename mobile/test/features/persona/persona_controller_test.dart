import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/network/api_client.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/core/session/session_controller.dart';
import 'package:past_partner/core/session/session_store.dart';
import 'package:past_partner/features/persona/persona.dart';
import 'package:past_partner/features/persona/persona_controller.dart';
import 'package:past_partner/features/persona/persona_gateway.dart';

class _FakeGateway implements PersonaGateway {
  final List<Persona> records = <Persona>[];
  int listCalls = 0;

  @override
  Future<List<Persona>> list(
      {required ApiEndpoint endpoint, required Session session}) async {
    listCalls++;
    return List<Persona>.of(records);
  }

  @override
  Future<Persona> create(
      {required ApiEndpoint endpoint,
      required Session session,
      required PersonaDraft draft}) async {
    final Persona persona = Persona(
      id: 'created-${records.length + 1}',
      displayName: draft.displayName,
      relationshipType: draft.relationshipType,
      customLabel: draft.customLabel,
      relationshipDescription: draft.relationshipDescription,
    );
    records.add(persona);
    return persona;
  }
}

class _FakeStore implements SessionStore {
  @override
  Future<void> clear() async {}
  @override
  Future<Session?> read() async => null;
  @override
  Future<void> write(Session session) async {}
}

void main() {
  final Session session = Session(
    accessToken: 'token',
    ownerId: 'owner',
    expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
  );
  final ApiEndpoint endpoint = ApiEndpoint.parseDebug('http://127.0.0.1:8080');

  test('loads personas and creates a custom relationship', () async {
    final _FakeGateway gateway = _FakeGateway();
    final SessionController sessionController =
        SessionController(_FakeStore(), ApiClient())
          ..session = session
          ..endpoint = endpoint;
    final PersonaController controller =
        PersonaController(sessionController, gateway: gateway);

    await controller.load();
    expect(controller.state, PersonaState.ready);
    expect(controller.personas, isEmpty);
    expect(gateway.listCalls, 1);

    final Persona? created = await controller.create(
      const PersonaDraft(
        displayName: '小雅',
        relationshipType: PersonaRelationship.custom,
        customLabel: '旧友',
        relationshipDescription: '温和、愿意倾听',
      ),
    );
    expect(created?.displayName, '小雅');
    expect(controller.personas.single.customLabel, '旧友');
    expect(controller.state, PersonaState.ready);
  });

  test('reports a stable error when the session is unavailable', () async {
    final SessionController sessionController =
        SessionController(_FakeStore(), ApiClient());
    final PersonaController controller =
        PersonaController(sessionController, gateway: _FakeGateway());

    await controller.load();

    expect(controller.state, PersonaState.error);
    expect(controller.errorMessage, '请先连接本地服务。');
  });
}
