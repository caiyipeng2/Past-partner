import 'package:shared_preferences/shared_preferences.dart';

import 'conversation_appearance.dart';

abstract interface class AppearanceStore {
  Future<ConversationAppearance> read();
  Future<void> write(ConversationAppearance appearance);
}

class SharedPreferencesAppearanceStore implements AppearanceStore {
  static const String key = 'past_partner.conversation_appearance';

  @override
  Future<ConversationAppearance> read() async {
    final SharedPreferences preferences = await SharedPreferences.getInstance();
    final String? value = preferences.getString(key);
    return ConversationAppearance.values.firstWhere(
      (ConversationAppearance item) => item.name == value,
      orElse: () => ConversationAppearance.simplified,
    );
  }

  @override
  Future<void> write(ConversationAppearance appearance) async {
    final SharedPreferences preferences = await SharedPreferences.getInstance();
    await preferences.setString(key, appearance.name);
  }
}
