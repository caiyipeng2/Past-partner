import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../../../lib/features/appearance/widgets/bright_conversation_scaffold.dart';
import '../../../lib/features/appearance/widgets/calm_conversation_scaffold.dart';

void main() {
  testWidgets('calm preview exposes expandable generic actions', (WidgetTester tester) async {
    await tester.pumpWidget(const MaterialApp(home: CalmConversationScaffold()));
    expect(find.byTooltip('More actions'), findsOneWidget);
    await tester.tap(find.byTooltip('More actions'));
    await tester.pumpAndSettle();
    expect(find.bySemanticsLabel('More actions panel'), findsOneWidget);
  });

  testWidgets('bright preview uses compact message composer', (WidgetTester tester) async {
    await tester.pumpWidget(const MaterialApp(home: BrightConversationScaffold()));
    expect(find.byTooltip('Voice preview'), findsOneWidget);
    expect(find.text('Send'), findsOneWidget);
  });
}
