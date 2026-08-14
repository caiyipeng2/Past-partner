import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/features/appearance/appearance_controller.dart';
import 'package:past_partner/features/appearance/appearance_store.dart';
import 'package:past_partner/features/appearance/conversation_appearance.dart';
import 'package:past_partner/features/chat/chat_controller.dart';
import 'package:past_partner/features/chat/chat_screen.dart';
import 'chat_controller_test.dart' show FakeGateway;

class _AppearanceStore implements AppearanceStore {
  @override
  Future<ConversationAppearance> read() async =>
      ConversationAppearance.simplified;

  @override
  Future<void> write(ConversationAppearance appearance) async {}
}

void main() {
  testWidgets('real chat screen renders history and sends from composer', (
    WidgetTester tester,
  ) async {
    final ChatController controller = ChatController(
      endpoint: ApiEndpoint.parseDebug('http://127.0.0.1:8080'),
      session: Session(
        accessToken: 'token',
        ownerId: 'owner',
        expiresAt: DateTime.utc(2099),
      ),
      personaId: 'persona-1',
      providerId: 'test',
      modelId: 'deterministic',
      gateway: FakeGateway(),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: ChatScreen(
          personaName: '小雅',
          modelLabel: '测试 · Deterministic',
          controller: controller,
          appearanceController: AppearanceController(_AppearanceStore()),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('小雅'), findsOneWidget);
    expect(find.byType(TextField), findsOneWidget);

    await tester.enterText(find.byType(TextField), '你好');
    await tester.tap(find.text('发送'));
    await tester.pumpAndSettle();
    expect(find.text('你好'), findsOneWidget);
    expect(find.text('测试回复：你好'), findsOneWidget);
    expect(find.text('测试回复：你好'), findsOneWidget);
  });
}
