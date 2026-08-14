enum ReviewState {
  accepted('accepted', '已接受'),
  needsReview('needs_review', '待审核'),
  rejected('rejected', '已拒绝');

  const ReviewState(this.value, this.label);

  final String value;
  final String label;

  static ReviewState fromValue(Object? value) {
    for (final ReviewState state in ReviewState.values) {
      if (state.value == value) return state;
    }
    throw const FormatException('Invalid review state.');
  }
}

enum ParticipantRole {
  persona('persona', '人物'),
  user('user', '我'),
  other('other', '其他'),
  unknown('unknown', '未确认');

  const ParticipantRole(this.value, this.label);

  final String value;
  final String label;

  static ParticipantRole fromValue(Object? value) {
    for (final ParticipantRole role in ParticipantRole.values) {
      if (role.value == value) return role;
    }
    throw const FormatException('Invalid participant role.');
  }
}

class ImportPreviewSummary {
  const ImportPreviewSummary({
    required this.recordCount,
    required this.warningCount,
    required this.confidence,
    required this.truncated,
  });

  final int recordCount;
  final int warningCount;
  final double? confidence;
  final bool truncated;

  factory ImportPreviewSummary.fromJson(Object? value) {
    if (value is! Map) throw const FormatException('Invalid preview summary.');
    final Object? recordCount = value['record_count'];
    final Object? warningCount = value['warning_count'];
    if (recordCount is! int ||
        recordCount < 0 ||
        warningCount is! int ||
        warningCount < 0) {
      throw const FormatException('Invalid preview summary counts.');
    }
    final Object? confidence = value['confidence'];
    final double? parsedConfidence = confidence is num
        ? confidence.toDouble()
        : confidence == null
            ? null
            : throw const FormatException('Invalid preview confidence.');
    if (parsedConfidence != null &&
        (parsedConfidence < 0 || parsedConfidence > 1)) {
      throw const FormatException('Invalid preview confidence range.');
    }
    final Object? truncated = value['truncated'];
    if (truncated != null && truncated is! bool) {
      throw const FormatException('Invalid preview truncation flag.');
    }
    return ImportPreviewSummary(
      recordCount: recordCount,
      warningCount: warningCount,
      confidence: parsedConfidence,
      truncated: truncated as bool? ?? false,
    );
  }
}

class ImportPreviewRecord {
  ImportPreviewRecord({
    required this.recordId,
    this.senderId,
    this.senderName,
    this.content,
    this.timestamp,
    this.messageType,
    this.conversationId,
    this.fileId,
    this.sourceName,
    this.mediaType,
    this.sourceType,
    this.reviewState = ReviewState.needsReview,
    this.senderRole,
  });

  final String recordId;
  final String? senderId;
  final String? senderName;
  final String? content;
  final String? timestamp;
  final String? messageType;
  final String? conversationId;
  final String? fileId;
  final String? sourceName;
  final String? mediaType;
  final String? sourceType;
  final ReviewState reviewState;
  final ParticipantRole? senderRole;

  factory ImportPreviewRecord.fromJson(Object? value) {
    if (value is! Map) throw const FormatException('Invalid preview record.');
    final String recordId = _requiredString(value['record_id'], 64);
    if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(recordId)) {
      throw const FormatException('Invalid preview record id.');
    }
    return ImportPreviewRecord(
      recordId: recordId,
      senderId: _optionalString(value['sender_id'], 256),
      senderName: _optionalString(value['sender_name'], 256),
      content: _optionalString(value['content'], 10000, allowNewlines: true),
      timestamp: _optionalString(value['timestamp'], 128),
      messageType: _optionalString(value['message_type'], 128),
      conversationId: _optionalString(value['conversation_id'], 256),
      fileId: _optionalString(value['file_id'], 256),
      sourceName: _optionalString(value['source_name'], 256),
      mediaType: _optionalString(value['media_type'], 128),
      sourceType: _optionalString(value['source_type'], 128),
      reviewState: value['review_state'] == null
          ? ReviewState.needsReview
          : ReviewState.fromValue(value['review_state']),
      senderRole: value['sender_role'] == null
          ? null
          : ParticipantRole.fromValue(value['sender_role']),
    );
  }

  Map<String, dynamic> toCorrectionJson({ReviewState? state}) {
    return <String, dynamic>{
      'record_id': recordId,
      if (senderId != null) 'sender_id': senderId,
      if (senderName != null) 'sender_name': senderName,
      if (content != null) 'content': content,
      if (timestamp != null) 'timestamp': timestamp,
      if (messageType != null) 'message_type': messageType,
      'review_state': (state ?? reviewState).value,
    };
  }

  static String _requiredString(Object? value, int maxLength) {
    if (value is! String || value.isEmpty || value.length > maxLength) {
      throw const FormatException('Invalid preview record field.');
    }
    return value;
  }

  static String? _optionalString(Object? value, int maxLength,
      {bool allowNewlines = false}) {
    if (value == null) return null;
    if (value is! String || value.length > maxLength) {
      throw const FormatException('Invalid preview record field.');
    }
    for (final int codeUnit in value.codeUnits) {
      if (codeUnit < 0x20 &&
          (codeUnit != 0x09 &&
              (!allowNewlines || (codeUnit != 0x0a && codeUnit != 0x0d)))) {
        throw const FormatException(
            'Invalid preview record control character.');
      }
    }
    return value;
  }
}

class ImportPreview {
  ImportPreview({
    required this.importId,
    required this.state,
    required this.sourceName,
    required this.mediaType,
    required this.sourceType,
    required this.summary,
    required this.warnings,
    required this.records,
    required this.fileSummaries,
  });

  final String importId;
  final String state;
  final String sourceName;
  final String mediaType;
  final String sourceType;
  final ImportPreviewSummary summary;
  final List<String> warnings;
  final List<ImportPreviewRecord> records;
  final List<Map<String, dynamic>> fileSummaries;

  factory ImportPreview.fromJson(Object? value) {
    if (value is! Map) throw const FormatException('Invalid preview response.');
    final String importId = _requiredTopLevelString(value['import_id']);
    final String state = _requiredTopLevelString(value['state']);
    final String sourceName = _requiredTopLevelString(value['source_name']);
    final String mediaType = _requiredTopLevelString(value['media_type']);
    final String sourceType = _requiredTopLevelString(value['source_type']);
    final List<Object?> warnings = _boundedList(value['warnings'], 100);
    final List<Object?> records = _boundedList(value['records'], 100);
    final List<Object?> files = _boundedList(value['file_summaries'], 32);
    return ImportPreview(
      importId: importId,
      state: state,
      sourceName: sourceName,
      mediaType: mediaType,
      sourceType: sourceType,
      summary: ImportPreviewSummary.fromJson(value['summary']),
      warnings: warnings.map((Object? warning) {
        if (warning is! String || warning.length > 2000) {
          throw const FormatException('Invalid preview warning.');
        }
        return warning;
      }).toList(growable: false),
      records:
          records.map(ImportPreviewRecord.fromJson).toList(growable: false),
      fileSummaries: files.map((Object? file) {
        if (file is! Map) throw const FormatException('Invalid file summary.');
        return Map<String, dynamic>.from(file);
      }).toList(growable: false),
    );
  }

  static String _requiredTopLevelString(Object? value) {
    if (value is! String || value.isEmpty || value.length > 512) {
      throw const FormatException('Invalid preview response field.');
    }
    return value;
  }

  static List<Object?> _boundedList(Object? value, int maxLength) {
    if (value is! List || value.length > maxLength) {
      throw const FormatException('Invalid preview response list.');
    }
    return List<Object?>.of(value);
  }
}

class ImportCorrection {
  const ImportCorrection({
    required this.recordId,
    this.senderId,
    this.senderName,
    this.content,
    this.timestamp,
    this.messageType,
    required this.reviewState,
  });

  final String recordId;
  final String? senderId;
  final String? senderName;
  final String? content;
  final String? timestamp;
  final String? messageType;
  final ReviewState reviewState;

  Map<String, dynamic> toJson() => <String, dynamic>{
        'record_id': recordId,
        if (senderId != null) 'sender_id': senderId,
        if (senderName != null) 'sender_name': senderName,
        if (content != null) 'content': content,
        if (timestamp != null) 'timestamp': timestamp,
        if (messageType != null) 'message_type': messageType,
        'review_state': reviewState.value,
      };
}
