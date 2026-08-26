import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/features/models/model_selection_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  test('model selection round trips only provider and model identifiers', () {
    const ModelSelection selection = ModelSelection(
      providerId: 'deepseek',
      modelId: 'deepseek-v4-flash',
    );

    final ModelSelection decoded =
        ModelSelection.fromJson(jsonDecode(jsonEncode(selection.toJson())));

    expect(decoded.providerId, 'deepseek');
    expect(decoded.modelId, 'deepseek-v4-flash');
    expect(selection.toJson().keys,
        containsAll(<String>['provider_id', 'model_id']));
    expect(selection.toJson().toString(), isNot(contains('token')));
  });

  test('shared preferences store is scoped by owner and drops malformed values',
      () async {
    SharedPreferences.setMockInitialValues(<String, Object>{
      'past_partner.model_selection.owner-bad': '{not-json',
    });
    final SharedPreferencesModelSelectionStore store =
        SharedPreferencesModelSelectionStore();

    await store.write(
      'owner-a',
      const ModelSelection(providerId: 'qwen', modelId: 'qwen3.7-plus'),
    );

    expect((await store.read('owner-a'))?.modelId, 'qwen3.7-plus');
    expect(await store.read('owner-b'), isNull);
    expect(await store.read('owner-bad'), isNull);
  });

  test('in-memory store can clear one owner without affecting another',
      () async {
    final InMemoryModelSelectionStore store = InMemoryModelSelectionStore();
    await store.write(
      'owner-a',
      const ModelSelection(
          providerId: 'deepseek', modelId: 'deepseek-v4-flash'),
    );
    await store.write(
      'owner-b',
      const ModelSelection(providerId: 'qwen', modelId: 'qwen3.7-plus'),
    );

    await store.clear('owner-a');

    expect(await store.read('owner-a'), isNull);
    expect((await store.read('owner-b'))?.providerId, 'qwen');
  });
}
