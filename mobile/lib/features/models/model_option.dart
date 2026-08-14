class ModelPricing {
  const ModelPricing({
    this.inputPricePerMillionTokens,
    this.outputPricePerMillionTokens,
    this.currency = 'USD',
    this.source,
    this.lastRefreshedAt,
  });

  final double? inputPricePerMillionTokens;
  final double? outputPricePerMillionTokens;
  final String currency;
  final String? source;
  final String? lastRefreshedAt;

  factory ModelPricing.fromJson(dynamic value) {
    if (value == null) return const ModelPricing();
    if (value is! Map) throw const FormatException('Invalid model pricing.');
    final Map<Object?, Object?> json = value;
    final String currency = _text(json['currency'], 'currency', 16) ?? 'USD';
    return ModelPricing(
      inputPricePerMillionTokens: _price(json['input_price_per_million_tokens'],
          'input_price_per_million_tokens'),
      outputPricePerMillionTokens: _price(
          json['output_price_per_million_tokens'],
          'output_price_per_million_tokens'),
      currency: currency,
      source: _text(json['source'], 'source', 64),
      lastRefreshedAt:
          _text(json['last_refreshed_at'], 'last_refreshed_at', 128),
    );
  }

  bool get hasTokenPricing =>
      inputPricePerMillionTokens != null && outputPricePerMillionTokens != null;
}

class ModelOption {
  const ModelOption({
    required this.providerId,
    required this.providerName,
    required this.id,
    required this.displayName,
    required this.capabilities,
    required this.pricing,
    this.contextLength,
    this.regions = const <String>[],
    this.privacyMetadata = const <String>[],
  });

  final String providerId;
  final String providerName;
  final String id;
  final String displayName;
  final List<String> capabilities;
  final int? contextLength;
  final List<String> regions;
  final List<String> privacyMetadata;
  final ModelPricing pricing;

  factory ModelOption.fromJson(Map<String, dynamic> json) {
    final String providerId = _requiredText(json['provider_id'], 'provider_id');
    final String id = _requiredText(json['id'], 'id');
    final String displayName =
        _requiredText(json['display_name'], 'display_name');
    final List<String> capabilities =
        _textList(json['capabilities'], 'capabilities');
    final List<String> regions = _textList(json['regions'], 'regions');
    final List<String> privacy =
        _textList(json['privacy_metadata'], 'privacy_metadata');
    final int? contextLength =
        _positiveInt(json['context_length'], 'context_length');
    return ModelOption(
      providerId: providerId,
      providerName: _providerLabel(providerId),
      id: id,
      displayName: displayName,
      capabilities: capabilities,
      contextLength: contextLength,
      regions: regions,
      privacyMetadata: privacy,
      pricing: ModelPricing.fromJson(json['pricing']),
    );
  }

  String get refreshedAtLabel {
    final String? value = pricing.lastRefreshedAt;
    if (value == null || value.isEmpty) return '价格更新时间：未知';
    try {
      // Keep the provider's published timestamp stable; the API value is the
      // audit reference users compare with the catalog refresh metadata.
      final DateTime date = DateTime.parse(value);
      final String month = date.month.toString().padLeft(2, '0');
      final String day = date.day.toString().padLeft(2, '0');
      final String hour = date.hour.toString().padLeft(2, '0');
      final String minute = date.minute.toString().padLeft(2, '0');
      return '价格更新时间：${date.year}-$month-$day $hour:$minute';
    } on FormatException {
      return '价格更新时间：未知';
    }
  }

  String get contextLabel =>
      contextLength == null ? '上下文未知' : _compactNumber(contextLength!);
}

class ModelCostEstimate {
  const ModelCostEstimate({
    required this.providerId,
    required this.modelId,
    required this.currency,
    required this.estimatedCost,
    this.priceLastRefreshedAt,
  });

  final String providerId;
  final String modelId;
  final String currency;
  final double estimatedCost;
  final String? priceLastRefreshedAt;

  factory ModelCostEstimate.fromJson(Map<String, dynamic> json) {
    return ModelCostEstimate(
      providerId: _requiredText(json['provider_id'], 'provider_id'),
      modelId: _requiredText(json['model_id'], 'model_id'),
      currency: _requiredText(json['currency'], 'currency'),
      estimatedCost: _requiredPrice(json['estimated_cost'], 'estimated_cost'),
      priceLastRefreshedAt: _text(
          json['price_last_refreshed_at'], 'price_last_refreshed_at', 128),
    );
  }
}

String _requiredText(dynamic value, String field) {
  final String? text = _text(value, field, 256);
  if (text == null) throw FormatException('Invalid model $field.');
  return text;
}

String? _text(dynamic value, String field, int maxLength) {
  if (value == null) return null;
  if (value is! String || value.isEmpty || value.length > maxLength) {
    throw FormatException('Invalid model $field.');
  }
  return value;
}

List<String> _textList(dynamic value, String field) {
  if (value == null) return const <String>[];
  if (value is! List || value.length > 64) {
    throw FormatException('Invalid model $field.');
  }
  return value.map((dynamic item) {
    return _requiredText(item, field);
  }).toList(growable: false);
}

int? _positiveInt(dynamic value, String field) {
  if (value == null) return null;
  if (value is! int || value < 1 || value > 100000000) {
    throw FormatException('Invalid model $field.');
  }
  return value;
}

double? _price(dynamic value, String field) {
  if (value == null) return null;
  if (value is! num || !value.isFinite || value < 0 || value > 1000000000) {
    throw FormatException('Invalid model $field.');
  }
  return value.toDouble();
}

double _requiredPrice(dynamic value, String field) {
  final double? price = _price(value, field);
  if (price == null) throw FormatException('Invalid model $field.');
  return price;
}

String _providerLabel(String value) {
  const Map<String, String> labels = <String, String>{
    'openai': 'OpenAI',
    'anthropic': 'Anthropic',
    'gemini': 'Google Gemini',
    'deepseek': 'DeepSeek',
    'xiaomi_mimo': 'Xiaomi MiMo',
    'qwen': 'Alibaba Qwen',
    'ollama': 'Ollama',
    'custom_openai': '自定义 OpenAI',
    'custom_http': '自定义 HTTP',
  };
  return labels[value] ?? value;
}

String _compactNumber(int value) {
  if (value >= 1000000 && value % 1000000 == 0) {
    return '${value ~/ 1000000}M 上下文';
  }
  if (value >= 1000 && value % 1000 == 0) return '${value ~/ 1000}K 上下文';
  return '$value 上下文';
}
