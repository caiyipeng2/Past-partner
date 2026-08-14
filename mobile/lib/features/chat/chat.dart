import 'package:flutter/foundation.dart';

class ConversationMessage {
  const ConversationMessage({
    required this.id,
    required this.role,
    required this.content,
    required this.createdAt,
  });

  final String id;
  final String role;
  final String content;
  final DateTime createdAt;

  bool get isUser => role == 'user';

  factory ConversationMessage.fromJson(Map<String, dynamic> json) {
    final String id = _requiredText(json['id'], 'id');
    final String role = _requiredText(json['role'], 'role');
    if (role != 'user' && role != 'assistant') {
      throw const FormatException('Invalid conversation message role.');
    }
    final String content = _requiredText(json['content'], 'content');
    final String createdAt = _requiredText(json['created_at'], 'created_at');
    return ConversationMessage(
      id: id,
      role: role,
      content: content,
      createdAt: DateTime.tryParse(createdAt) ??
          (throw const FormatException('Invalid conversation timestamp.')),
    );
  }
}

class Conversation {
  const Conversation({
    required this.id,
    required this.personaId,
    required this.providerId,
    required this.modelId,
    required this.createdAt,
    required this.updatedAt,
    required this.messages,
  });

  final String id;
  final String personaId;
  final String providerId;
  final String modelId;
  final DateTime createdAt;
  final DateTime updatedAt;
  final List<ConversationMessage> messages;

  factory Conversation.fromJson(Map<String, dynamic> json) {
    final dynamic rawMessages = json['messages'];
    if (rawMessages is! List || rawMessages.length > 256) {
      throw const FormatException('Invalid conversation messages.');
    }
    final DateTime createdAt = _date(json['created_at'], 'created_at');
    final DateTime updatedAt = _date(json['updated_at'], 'updated_at');
    return Conversation(
      id: _requiredText(json['id'], 'id'),
      personaId: _requiredText(json['persona_id'], 'persona_id'),
      providerId: _requiredText(json['provider_id'], 'provider_id'),
      modelId: _requiredText(json['model_id'], 'model_id'),
      createdAt: createdAt,
      updatedAt: updatedAt,
      messages: rawMessages.map((dynamic value) {
        if (value is! Map) {
          throw const FormatException('Invalid conversation message.');
        }
        return ConversationMessage.fromJson(
          Map<String, dynamic>.from(value),
        );
      }).toList(growable: false),
    );
  }
}

class ConversationSummary {
  const ConversationSummary({
    required this.id,
    required this.personaId,
    required this.providerId,
    required this.modelId,
    required this.updatedAt,
  });

  final String id;
  final String personaId;
  final String providerId;
  final String modelId;
  final DateTime updatedAt;

  factory ConversationSummary.fromJson(Map<String, dynamic> json) {
    return ConversationSummary(
      id: _requiredText(json['id'], 'id'),
      personaId: _requiredText(json['persona_id'], 'persona_id'),
      providerId: _requiredText(json['provider_id'], 'provider_id'),
      modelId: _requiredText(json['model_id'], 'model_id'),
      updatedAt: _date(json['updated_at'], 'updated_at'),
    );
  }
}

String _requiredText(dynamic value, String field) {
  if (value is! String || value.isEmpty || value.length > 20000) {
    throw FormatException('Invalid conversation $field.');
  }
  return value;
}

DateTime _date(dynamic value, String field) {
  final String text = _requiredText(value, field);
  final DateTime? parsed = DateTime.tryParse(text);
  if (parsed == null) throw FormatException('Invalid conversation $field.');
  return parsed;
}

@visibleForTesting
Conversation conversationFromJson(Map<String, dynamic> json) =>
    Conversation.fromJson(json);
