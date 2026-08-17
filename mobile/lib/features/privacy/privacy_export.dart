class PrivacyExportSummary {
  const PrivacyExportSummary({
    required this.exportVersion,
    required this.generatedAt,
    required this.rawPayloadsIncluded,
    required this.omitted,
    required this.personaCount,
    required this.importCount,
    required this.consentCount,
    required this.trainingJobCount,
    required this.conversationCount,
  });

  final int exportVersion;
  final String generatedAt;
  final bool rawPayloadsIncluded;
  final List<String> omitted;
  final int personaCount;
  final int importCount;
  final int consentCount;
  final int trainingJobCount;
  final int conversationCount;

  factory PrivacyExportSummary.fromJson(Map<String, dynamic> value) {
    final dynamic rawScope = value['scope'];
    if (value['export_version'] is! int ||
        value['generated_at'] is! String ||
        rawScope is! Map) {
      throw const FormatException('Invalid privacy export summary.');
    }
    final Map<String, dynamic> scope = Map<String, dynamic>.from(rawScope);
    final dynamic rawPayloadsIncluded = scope['raw_payloads_included'];
    final dynamic rawOmitted = scope['omitted'];
    if (rawPayloadsIncluded is! bool || rawOmitted is! List) {
      throw const FormatException('Invalid privacy export scope.');
    }
    if (rawOmitted.length > 32 ||
        rawOmitted
            .any((dynamic item) => item is! String || item.length > 128)) {
      throw const FormatException('Invalid privacy export omissions.');
    }
    return PrivacyExportSummary(
      exportVersion: value['export_version'] as int,
      generatedAt: value['generated_at'] as String,
      rawPayloadsIncluded: rawPayloadsIncluded,
      omitted: rawOmitted.cast<String>().toList(growable: false),
      personaCount: _count(value['personas'], 'personas'),
      importCount: _count(value['imports'], 'imports'),
      consentCount: _count(value['consents'], 'consents'),
      trainingJobCount: _count(value['training_jobs'], 'training_jobs'),
      conversationCount: _count(value['conversations'], 'conversations'),
    );
  }

  static int _count(dynamic value, String field) {
    if (value is! List || value.length > 2048) {
      throw FormatException('Invalid privacy export $field.');
    }
    return value.length;
  }
}
