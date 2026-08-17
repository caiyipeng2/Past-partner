import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/features/persona/persona.dart';
import 'package:past_partner/features/privacy/privacy_controller.dart';
import 'package:past_partner/features/privacy/privacy_export.dart';
import 'package:past_partner/features/privacy/privacy_gateway.dart';
import 'package:past_partner/features/privacy/privacy_screen.dart';

class _FakePrivacyGateway implements PrivacyGateway {
  _FakePrivacyGateway(this.summary);

  final PrivacyExportSummary summary;
  String? deletedPersonaId;

  @override
  Future<PrivacyExportSummary> exportData() async => summary;

  @override
  Future<void> deletePersona(String personaId) async {
    deletedPersonaId = personaId;
  }
}

PrivacyExportSummary _summary() => const PrivacyExportSummary(
      exportVersion: 1,
      generatedAt: '2026-08-17T00:00:00Z',
      rawPayloadsIncluded: false,
      omitted: <String>['raw_import_payloads', 'provider_side_data'],
      personaCount: 1,
      importCount: 2,
      consentCount: 1,
      trainingJobCount: 0,
      conversationCount: 3,
    );

void main() {
  testWidgets('shows a bounded export summary and deletion confirmation',
      (WidgetTester tester) async {
    final _FakePrivacyGateway gateway = _FakePrivacyGateway(_summary());
    final PrivacyController controller = PrivacyController(gateway: gateway);
    bool refreshed = false;

    await tester.pumpWidget(
      MaterialApp(
        home: PrivacyScreen(
          controller: controller,
          personas: const <Persona>[
            Persona(
              id: 'persona-1',
              displayName: '小雅',
              relationshipType: PersonaRelationship.friend,
            ),
          ],
          onPersonaDeleted: () async => refreshed = true,
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('隐私管理'), findsOneWidget);
    expect(find.text('人物 1 · 导入 2 · 会话 3'), findsOneWidget);
    expect(find.text('原始内容未包含在导出摘要中'), findsOneWidget);

    await tester.tap(find.byKey(const Key('privacy-delete-persona-1')));
    await tester.pumpAndSettle();
    expect(find.text('确认删除人物？'), findsOneWidget);
    expect(find.text('删除后将级联移除该人物的导入、授权和会话记录。此操作不可撤销。'), findsOneWidget);

    await tester.tap(find.byKey(const Key('privacy-confirm-delete')));
    await tester.pumpAndSettle();
    expect(gateway.deletedPersonaId, 'persona-1');
    expect(refreshed, isTrue);
    expect(find.text('小雅'), findsNothing);
  });
}
