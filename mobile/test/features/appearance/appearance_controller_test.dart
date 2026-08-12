import 'package:flutter_test/flutter_test.dart';

import 'package:past_partner/features/appearance/appearance_controller.dart';
import 'package:past_partner/features/appearance/appearance_store.dart';
import 'package:past_partner/features/appearance/conversation_appearance.dart';

class MemoryAppearanceStore implements AppearanceStore {
  ConversationAppearance value = ConversationAppearance.simplified;

  @override
  Future<ConversationAppearance> read() async => value;

  @override
  Future<void> write(ConversationAppearance appearance) async => value = appearance;
}

void main() {
  test('selection persists immediately without session data', () async {
    final MemoryAppearanceStore store = MemoryAppearanceStore();
    final AppearanceController controller = AppearanceController(store);
    await controller.select(ConversationAppearance.lively);
    expect(controller.appearance, ConversationAppearance.lively);
    expect(store.value, ConversationAppearance.lively);
  });
}
