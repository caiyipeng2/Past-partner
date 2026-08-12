import 'package:flutter/material.dart';

import 'conversation_appearance.dart';
import 'widgets/bright_conversation_scaffold.dart';
import 'widgets/calm_conversation_scaffold.dart';

class ConversationPreviewScreen extends StatelessWidget {
  const ConversationPreviewScreen({required this.appearance, required this.onAppearanceChanged, super.key});

  final ConversationAppearance appearance;
  final ValueChanged<ConversationAppearance> onAppearanceChanged;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Stack(
        children: <Widget>[
          Positioned.fill(
            child: appearance == ConversationAppearance.simplified
                ? const CalmConversationScaffold()
                : const BrightConversationScaffold(),
          ),
          Positioned(
            top: MediaQuery.paddingOf(context).top + 8,
            left: 12,
            child: Material(
              color: Colors.white.withAlpha(224),
              borderRadius: BorderRadius.circular(12),
              child: PopupMenuButton<ConversationAppearance>(
                tooltip: 'Conversation appearance',
                initialValue: appearance,
                onSelected: onAppearanceChanged,
                itemBuilder: (BuildContext context) => ConversationAppearance.values
                    .map((ConversationAppearance value) => PopupMenuItem<ConversationAppearance>(value: value, child: Text(value.label)))
                    .toList(),
                child: const Padding(
                  padding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                  child: Icon(Icons.palette_outlined),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
