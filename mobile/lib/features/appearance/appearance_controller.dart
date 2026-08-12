import 'package:flutter/foundation.dart';

import 'appearance_store.dart';
import 'conversation_appearance.dart';

class AppearanceController extends ChangeNotifier {
  AppearanceController(this.store);

  final AppearanceStore store;
  ConversationAppearance appearance = ConversationAppearance.simplified;

  Future<void> restore() async {
    appearance = await store.read();
    notifyListeners();
  }

  Future<void> select(ConversationAppearance value) async {
    if (appearance == value) return;
    await store.write(value);
    appearance = value;
    notifyListeners();
  }
}
