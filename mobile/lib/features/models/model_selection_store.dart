import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

class ModelSelection {
  const ModelSelection({required this.providerId, required this.modelId});

  final String providerId;
  final String modelId;

  factory ModelSelection.fromJson(dynamic value) {
    if (value is! Map) throw const FormatException('模型选择记录无效。');
    final dynamic providerId = value['provider_id'];
    final dynamic modelId = value['model_id'];
    if (!_validIdentifier(providerId) || !_validIdentifier(modelId)) {
      throw const FormatException('模型选择记录无效。');
    }
    return ModelSelection(
      providerId: providerId as String,
      modelId: modelId as String,
    );
  }

  Map<String, String> toJson() => <String, String>{
        'provider_id': providerId,
        'model_id': modelId,
      };

  static bool _validIdentifier(dynamic value) =>
      value is String && value.trim().isNotEmpty && value.length <= 128;
}

abstract interface class ModelSelectionStore {
  Future<ModelSelection?> read(String ownerId);
  Future<void> write(String ownerId, ModelSelection selection);
  Future<void> clear(String ownerId);
}

class InMemoryModelSelectionStore implements ModelSelectionStore {
  final Map<String, ModelSelection> _values = <String, ModelSelection>{};

  @override
  Future<ModelSelection?> read(String ownerId) async => _values[ownerId];

  @override
  Future<void> write(String ownerId, ModelSelection selection) async {
    _values[ownerId] = selection;
  }

  @override
  Future<void> clear(String ownerId) async {
    _values.remove(ownerId);
  }
}

class SharedPreferencesModelSelectionStore implements ModelSelectionStore {
  static const String _prefix = 'past_partner.model_selection.';

  String _key(String ownerId) {
    final String normalized = ownerId.trim();
    if (normalized.isEmpty || normalized.length > 128) {
      throw ArgumentError.value(ownerId, 'ownerId');
    }
    return '$_prefix$normalized';
  }

  @override
  Future<ModelSelection?> read(String ownerId) async {
    final SharedPreferences preferences = await SharedPreferences.getInstance();
    final String? raw = preferences.getString(_key(ownerId));
    if (raw == null) return null;
    try {
      return ModelSelection.fromJson(jsonDecode(raw));
    } on Object {
      // A corrupt local preference must not prevent the user from opening the
      // model picker; remove only this owner's invalid value.
      await preferences.remove(_key(ownerId));
      return null;
    }
  }

  @override
  Future<void> write(String ownerId, ModelSelection selection) async {
    final SharedPreferences preferences = await SharedPreferences.getInstance();
    await preferences.setString(_key(ownerId), jsonEncode(selection.toJson()));
  }

  @override
  Future<void> clear(String ownerId) async {
    final SharedPreferences preferences = await SharedPreferences.getInstance();
    await preferences.remove(_key(ownerId));
  }
}
