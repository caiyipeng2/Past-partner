enum ConsentStatus {
  active('active', '已生效'),
  revoked('revoked', '已撤回');

  const ConsentStatus(this.value, this.label);

  final String value;
  final String label;

  static ConsentStatus fromValue(Object? value) {
    for (final ConsentStatus status in ConsentStatus.values) {
      if (status.value == value) return status;
    }
    throw const FormatException('Invalid consent status.');
  }
}

enum ConsentDataCategory {
  image('image', '图片', '图片理解', 'persona-image-analysis'),
  audio('audio', '音频', '音频转写', 'persona-audio-transcription'),
  video('video', '视频', '视频分析', 'persona-video-analysis');

  const ConsentDataCategory(
      this.value, this.label, this.defaultPurpose, this.defaultScope);

  final String value;
  final String label;
  final String defaultPurpose;
  final String defaultScope;

  static ConsentDataCategory fromValue(Object? value) {
    for (final ConsentDataCategory category in ConsentDataCategory.values) {
      if (category.value == value) return category;
    }
    throw const FormatException('Invalid consent data category.');
  }
}

class Consent {
  const Consent({
    required this.id,
    required this.personaId,
    required this.providerId,
    required this.modelId,
    required this.dataCategory,
    required this.estimatedCost,
    required this.purpose,
    required this.authorizationScope,
    required this.createdAt,
    this.status = ConsentStatus.active,
    this.revokedAt,
  });

  final String id;
  final String personaId;
  final String providerId;
  final String modelId;
  final ConsentDataCategory dataCategory;
  final double estimatedCost;
  final String purpose;
  final String authorizationScope;
  final String createdAt;
  final ConsentStatus status;
  final String? revokedAt;

  factory Consent.fromJson(Object? value) {
    if (value is! Map) throw const FormatException('Invalid consent record.');
    final String id = _requiredText(value['id'], 'id', 128);
    final String personaId =
        _requiredText(value['persona_id'], 'persona_id', 128);
    final String providerId =
        _requiredText(value['provider_id'], 'provider_id', 128);
    final String modelId = _requiredText(value['model_id'], 'model_id', 256);
    final ConsentDataCategory category =
        ConsentDataCategory.fromValue(value['data_category']);
    final Object? rawCost = value['estimated_cost'];
    if (rawCost is! num ||
        !rawCost.isFinite ||
        rawCost < 0 ||
        rawCost > 1000000000) {
      throw const FormatException('Invalid consent estimated cost.');
    }
    final String purpose = _requiredText(value['purpose'], 'purpose', 512);
    final String scope =
        _requiredText(value['authorization_scope'], 'authorization_scope', 256);
    final String createdAt =
        _requiredText(value['created_at'], 'created_at', 128);
    final ConsentStatus status = ConsentStatus.fromValue(value['status']);
    final Object? rawRevokedAt = value['revoked_at'];
    if (status == ConsentStatus.active && rawRevokedAt != null) {
      throw const FormatException('Active consent cannot have revoked_at.');
    }
    final String? revokedAt = rawRevokedAt == null
        ? null
        : _requiredText(rawRevokedAt, 'revoked_at', 128);
    return Consent(
      id: id,
      personaId: personaId,
      providerId: providerId,
      modelId: modelId,
      dataCategory: category,
      estimatedCost: rawCost.toDouble(),
      purpose: purpose,
      authorizationScope: scope,
      createdAt: createdAt,
      status: status,
      revokedAt: revokedAt,
    );
  }

  Consent revoke(String revokedAt) {
    if (status == ConsentStatus.revoked) return this;
    return Consent(
      id: id,
      personaId: personaId,
      providerId: providerId,
      modelId: modelId,
      dataCategory: dataCategory,
      estimatedCost: estimatedCost,
      purpose: purpose,
      authorizationScope: authorizationScope,
      createdAt: createdAt,
      status: ConsentStatus.revoked,
      revokedAt: revokedAt,
    );
  }
}

class ConsentDraft {
  const ConsentDraft({
    required this.personaId,
    required this.providerId,
    required this.modelId,
    required this.dataCategory,
    required this.estimatedCost,
    required this.purpose,
    required this.authorizationScope,
  });

  final String personaId;
  final String providerId;
  final String modelId;
  final ConsentDataCategory dataCategory;
  final double estimatedCost;
  final String purpose;
  final String authorizationScope;

  Map<String, dynamic> toJson() => <String, dynamic>{
        'persona_id': personaId.trim(),
        'provider_id': providerId.trim(),
        'model_id': modelId.trim(),
        'data_category': dataCategory.value,
        'estimated_cost': estimatedCost,
        'purpose': purpose.trim(),
        'authorization_scope': authorizationScope.trim(),
      };
}

String _requiredText(Object? value, String field, int maximum) {
  if (value is! String || value.isEmpty || value.length > maximum) {
    throw FormatException('Invalid consent $field.');
  }
  if (value.codeUnits.any((int codeUnit) => codeUnit < 0x20)) {
    throw FormatException('Invalid consent $field.');
  }
  return value;
}
