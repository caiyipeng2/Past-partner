import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/network/api_client.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/core/session/session_controller.dart';
import 'package:past_partner/core/session/session_store.dart';
import 'package:past_partner/features/persona/persona.dart';
import 'package:past_partner/features/persona/persona_controller.dart';
import 'package:past_partner/features/persona/persona_gateway.dart';
import 'package:past_partner/features/persona/persona_workspace_screen.dart';
import 'package:past_partner/features/imports/import_controller.dart';
import 'package:past_partner/features/imports/import_gateway.dart';
import 'package:past_partner/features/imports/import_job.dart';
import 'package:past_partner/features/imports/import_workspace_screen.dart';

class _FakeStore implements SessionStore {
  @override
  Future<void> clear() async {}
  @override
  Future<Session?> read() async => null;
  @override
  Future<void> write(Session session) async {}
}

class _FakeGateway implements PersonaGateway {
  final List<Persona> records = <Persona>[];

  @override
  Future<List<Persona>> list(
          {required ApiEndpoint endpoint, required Session session}) async =>
      List<Persona>.of(records);

  @override
  Future<Persona> create(
      {required ApiEndpoint endpoint,
      required Session session,
      required PersonaDraft draft}) async {
    final Persona persona = Persona(
        id: 'p1',
        displayName: draft.displayName,
        relationshipType: draft.relationshipType,
        customLabel: draft.customLabel);
    records.add(persona);
    return persona;
  }
}

void main() {
  testWidgets('empty state opens form and refreshes after create',
      (WidgetTester tester) async {
    final SessionController sessionController =
        SessionController(_FakeStore(), ApiClient())
          ..session = Session(
              accessToken: 'token',
              ownerId: 'owner',
              expiresAt: DateTime.utc(2099))
          ..endpoint = ApiEndpoint.parseDebug('http://127.0.0.1:8080');
    final PersonaController controller =
        PersonaController(sessionController, gateway: _FakeGateway());

    await tester.pumpWidget(
        MaterialApp(home: PersonaWorkspaceScreen(controller: controller)));
    await tester.pumpAndSettle();
    expect(find.text('还没有人物'), findsOneWidget);
    await tester.tap(find.text('创建人物'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('persona-display-name')), '小雅');
    await tester.tap(find.text('情侣'));
    await tester.tap(find.text('保存人物'));
    await tester.pumpAndSettle();

    expect(find.text('小雅'), findsOneWidget);
    expect(find.text('情侣'), findsOneWidget);
  });

  testWidgets('persona card opens the import workspace for that persona',
      (WidgetTester tester) async {
    final SessionController sessionController =
        SessionController(_FakeStore(), ApiClient())
          ..session = Session(
              accessToken: 'token',
              ownerId: 'owner',
              expiresAt: DateTime.utc(2099))
          ..endpoint = ApiEndpoint.parseDebug('http://127.0.0.1:8080');
    final PersonaController personaController = PersonaController(
      sessionController,
      gateway: _FakeGateway()
        ..records.add(const Persona(
            id: 'persona-1',
            displayName: '小雅',
            relationshipType: PersonaRelationship.friend)),
    );

    await tester.pumpWidget(MaterialApp(
      home: PersonaWorkspaceScreen(
        controller: personaController,
        importControllerFactory: (Persona persona) => ImportController(
          sessionController,
          personaId: persona.id,
          gateway: _EmptyImportGateway(),
        ),
      ),
    ));
    await tester.pumpAndSettle();
    await tester.tap(find.text('小雅'));
    await tester.pumpAndSettle();

    expect(find.byType(ImportWorkspaceScreen), findsOneWidget);
    expect(find.text('小雅的导入'), findsOneWidget);
  });
}

class _EmptyImportGateway implements ImportGateway {
  @override
  Future<List<ImportJob>> list({
    required ApiEndpoint endpoint,
    required Session session,
    required String personaId,
  }) async =>
      <ImportJob>[];

  @override
  Future<ImportJob> create({
    required ApiEndpoint endpoint,
    required Session session,
    required ImportDraft draft,
  }) async =>
      throw UnimplementedError();
}
