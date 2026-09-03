import 'package:flutter/foundation.dart';

import '../../core/config/api_endpoint.dart';
import '../../core/network/api_client.dart';
import '../../core/network/api_failure.dart';
import '../../core/session/session.dart';

enum MediaAnalysisState { idle, loading, success, error }

class MediaAnalysisRequestData {
  const MediaAnalysisRequestData({
    required this.endpoint,
    required this.session,
    required this.importId,
    required this.consentId,
    required this.providerId,
    required this.modelId,
    required this.dataCategory,
    required this.authorizationScope,
    required this.prompt,
    this.fileId,
    this.analysisKind = 'description',
  });

  final ApiEndpoint endpoint;
  final Session session;
  final String importId;
  final String consentId;
  final String providerId;
  final String modelId;
  final String dataCategory;
  final String authorizationScope;
  final String prompt;
  final String? fileId;
  final String analysisKind;
}

class MediaAnalysisResult {
  const MediaAnalysisResult({
    required this.importId,
    required this.fileId,
    required this.providerId,
    required this.modelId,
    required this.mediaCategory,
    required this.mediaType,
    required this.description,
    required this.usage,
    required this.providerTransfer,
    this.analysisKind = 'description',
    this.structuredData,
    this.providerRequestId,
  });

  final String importId;
  final String fileId;
  final String providerId;
  final String modelId;
  final String mediaCategory;
  final String mediaType;
  final String description;
  final Map<String, int>? usage;
  final bool providerTransfer;
  final String analysisKind;
  final Map<String, dynamic>? structuredData;
  final String? providerRequestId;

  factory MediaAnalysisResult.fromJson(Map<String, dynamic> json) {
    final dynamic importId = json['import_id'];
    final dynamic fileId = json['file_id'];
    final dynamic providerId = json['provider_id'];
    final dynamic modelId = json['model_id'];
    final dynamic mediaCategory = json['media_category'];
    final dynamic mediaType = json['media_type'];
    final dynamic analysisKind = json['analysis_kind'] ?? 'description';
    final dynamic description = json['description'];
    final dynamic providerTransfer = json['provider_transfer'];
    final dynamic providerRequestId = json['provider_request_id'];
    if (importId is! String ||
        importId.isEmpty ||
        fileId is! String ||
        fileId.isEmpty ||
        providerId is! String ||
        providerId.isEmpty ||
        modelId is! String ||
        modelId.isEmpty ||
        mediaCategory is! String ||
        mediaCategory.isEmpty ||
        mediaType is! String ||
        mediaType.isEmpty ||
        analysisKind is! String ||
        (analysisKind != 'description' && analysisKind != 'ocr') ||
        description is! String ||
        description.isEmpty ||
        providerTransfer != true ||
        (providerRequestId != null && providerRequestId is! String)) {
      throw const FormatException('The media analysis response is invalid.');
    }
    final dynamic rawUsage = json['usage'];
    Map<String, int>? usage;
    if (rawUsage != null) {
      if (rawUsage is! Map) {
        throw const FormatException('The media analysis usage is invalid.');
      }
      final Map<String, int> parsed = <String, int>{};
      for (final MapEntry<dynamic, dynamic> entry in rawUsage.entries) {
        if (entry.key is! String ||
            (entry.key as String).isEmpty ||
            entry.value is! int ||
            entry.value is bool ||
            (entry.value as int) < 0) {
          throw const FormatException('The media analysis usage is invalid.');
        }
        parsed[entry.key as String] = entry.value as int;
      }
      usage = Map<String, int>.unmodifiable(parsed);
    }
    return MediaAnalysisResult(
      importId: importId,
      fileId: fileId,
      providerId: providerId,
      modelId: modelId,
      mediaCategory: mediaCategory,
      mediaType: mediaType,
      description: description,
      usage: usage,
      providerTransfer: true,
      analysisKind: analysisKind,
      structuredData: _parseStructuredData(json['structured_data']),
      providerRequestId: providerRequestId as String?,
    );
  }
}

abstract interface class MediaAnalysisGateway {
  Future<MediaAnalysisResult> analyze(MediaAnalysisRequestData request);
}

class ApiClientMediaAnalysisGateway implements MediaAnalysisGateway {
  const ApiClientMediaAnalysisGateway(this.client);

  final ApiClient client;

  @override
  Future<MediaAnalysisResult> analyze(MediaAnalysisRequestData request) async {
    return MediaAnalysisResult.fromJson(
      await client.analyzeMedia(
        request.endpoint,
        request.session,
        request.importId,
        consentId: request.consentId,
        providerId: request.providerId,
        modelId: request.modelId,
        dataCategory: request.dataCategory,
        authorizationScope: request.authorizationScope,
        prompt: request.prompt,
        fileId: request.fileId,
        analysisKind: request.analysisKind,
      ),
    );
  }
}

class MediaAnalysisController extends ChangeNotifier {
  MediaAnalysisController({
    required this.endpoint,
    required this.session,
    required this.importId,
    required this.gateway,
  });

  final ApiEndpoint endpoint;
  final Session session;
  final String importId;
  final MediaAnalysisGateway gateway;

  MediaAnalysisState state = MediaAnalysisState.idle;
  MediaAnalysisResult? result;
  String? errorCode;
  String? errorMessage;
  MediaAnalysisRequestData? _lastRequest;

  Future<bool> analyze({
    required String consentId,
    required String providerId,
    required String modelId,
    required String dataCategory,
    required String authorizationScope,
    required String prompt,
    String analysisKind = 'description',
    String? fileId,
  }) async {
    if (state == MediaAnalysisState.loading) return false;
    final MediaAnalysisRequestData request = MediaAnalysisRequestData(
      endpoint: endpoint,
      session: session,
      importId: importId,
      consentId: consentId,
      providerId: providerId,
      modelId: modelId,
      dataCategory: dataCategory,
      authorizationScope: authorizationScope,
      prompt: prompt,
      fileId: fileId,
      analysisKind: analysisKind,
    );
    _lastRequest = request;
    return _run(request);
  }

  Future<bool> retry() async {
    final MediaAnalysisRequestData? request = _lastRequest;
    if (request == null || state == MediaAnalysisState.loading) return false;
    return _run(request);
  }

  Future<bool> _run(MediaAnalysisRequestData request) async {
    state = MediaAnalysisState.loading;
    errorCode = null;
    errorMessage = null;
    notifyListeners();
    try {
      result = await gateway.analyze(request);
      state = MediaAnalysisState.success;
      notifyListeners();
      return true;
    } catch (error) {
      state = MediaAnalysisState.error;
      errorCode = error is ApiFailure ? error.code : 'analysis_failed';
      errorMessage = _friendlyError(error);
      notifyListeners();
      return false;
    }
  }

  static String _friendlyError(Object error) {
    if (error is ApiFailure && error.code == 'capability_not_supported') {
      return '当前模型不支持该媒体类型，请更换模型。';
    }
    if (error is ApiFailure &&
        (error.code == 'provider_unavailable' ||
            error.code == 'provider_timeout')) {
      return '媒体模型暂时不可用，请重试。';
    }
    if (error is ApiFailure &&
        (error.code == 'consent_not_found' ||
            error.code == 'consent_revoked' ||
            error.code == 'consent_scope_mismatch')) {
      return '请先完成有效的媒体授权。';
    }
    if (error is ApiFailure && error.code == 'media_too_large') {
      return '媒体文件超过分析大小限制。';
    }
    return '媒体分析失败，请重试。';
  }
}

Map<String, dynamic>? _parseStructuredData(Object? value) {
  if (value == null) return null;
  if (value is! Map || value.length > 2) {
    throw const FormatException(
        'The media analysis structured result is invalid.');
  }
  final Object? rawText = value['text'];
  final Object? rawBlocks = value['blocks'];
  if (rawText is! String ||
      rawText.isEmpty ||
      rawText.length > 8192 ||
      rawBlocks is! List ||
      rawBlocks.length > 256) {
    throw const FormatException(
        'The media analysis structured result is invalid.');
  }
  final List<Map<String, dynamic>> blocks = <Map<String, dynamic>>[];
  for (final Object? rawBlock in rawBlocks) {
    if (rawBlock is! Map) {
      throw const FormatException(
          'The media analysis structured result is invalid.');
    }
    final Object? rawBlockText = rawBlock['text'];
    if (rawBlockText is! String ||
        rawBlockText.isEmpty ||
        rawBlockText.length > 2048) {
      throw const FormatException(
          'The media analysis structured result is invalid.');
    }
    final Map<String, dynamic> block = <String, dynamic>{'text': rawBlockText};
    final Object? confidence = rawBlock['confidence'];
    if (confidence != null) {
      if (confidence is! num ||
          !confidence.isFinite ||
          confidence < 0 ||
          confidence > 1) {
        throw const FormatException(
            'The media analysis structured result is invalid.');
      }
      block['confidence'] = confidence.toDouble();
    }
    final Object? rawBbox = rawBlock['bbox'];
    if (rawBbox != null) {
      if (rawBbox is! List ||
          rawBbox.length != 4 ||
          rawBbox.any((Object? coordinate) =>
              coordinate is! num ||
              !coordinate.isFinite ||
              coordinate < 0 ||
              coordinate > 1)) {
        throw const FormatException(
            'The media analysis structured result is invalid.');
      }
      block['bbox'] = List<double>.unmodifiable(
        rawBbox.map((Object? coordinate) => (coordinate as num).toDouble()),
      );
    }
    blocks.add(Map<String, dynamic>.unmodifiable(block));
  }
  return Map<String, dynamic>.unmodifiable(<String, dynamic>{
    'text': rawText,
    'blocks': List<Map<String, dynamic>>.unmodifiable(blocks),
  });
}
