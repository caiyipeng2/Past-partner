enum ConversationAppearance { simplified, lively }

extension ConversationAppearanceLabel on ConversationAppearance {
  String get label {
    switch (this) {
      case ConversationAppearance.simplified:
        return 'Simplified conversation';
      case ConversationAppearance.lively:
        return 'Lively conversation';
    }
  }
}
