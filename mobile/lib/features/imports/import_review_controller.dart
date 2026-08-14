import 'package:flutter/foundation.dart';

import '../../core/config/api_endpoint.dart';
import '../../core/session/session.dart';
import 'import_review.dart';
import 'import_review_gateway.dart';

enum ImportReviewState { idle, loading, ready, saving, error }

class ImportReviewController extends ChangeNotifier {
  ImportReviewController({
    required this.endpoint,
    required this.session,
    required this.importId,
    required this.gateway,
    this.previewLimit = 20,
  });

  final ApiEndpoint endpoint;
  final Session session;
  final String importId;
  final ImportReviewGateway gateway;
  final int previewLimit;

  ImportReviewState state = ImportReviewState.idle;
  ImportPreview? preview;
  Map<String, String> mapping = <String, String>{};
  String? errorMessage;
  final Map<String, ReviewState> _reviewOverrides = <String, ReviewState>{};
  final Map<String, ImportCorrection> _correctionOverrides =
      <String, ImportCorrection>{};

  Future<void> load() async {
    state = ImportReviewState.loading;
    errorMessage = null;
    notifyListeners();
    try {
      final List<Object> values = await Future.wait<Object>(<Future<Object>>[
        gateway.preview(
          endpoint: endpoint,
          session: session,
          importId: importId,
          limit: previewLimit,
        ),
        gateway.mapping(
          endpoint: endpoint,
          session: session,
          importId: importId,
        ),
      ]);
      preview = values[0] as ImportPreview;
      mapping = Map<String, String>.of(values[1] as Map<String, String>);
      state = ImportReviewState.ready;
    } catch (_) {
      state = ImportReviewState.error;
      errorMessage = '导入审核加载失败，请重试。';
    }
    notifyListeners();
  }

  void setMapping(String participantId, ParticipantRole role) {
    if (participantId.isEmpty || participantId.length > 256) return;
    mapping = <String, String>{...mapping, participantId: role.value};
    notifyListeners();
  }

  ParticipantRole roleFor(String participantId) {
    return ParticipantRole.fromValue(mapping[participantId] ?? 'unknown');
  }

  void setReviewState(String recordId, ReviewState reviewState) {
    if (!_hasRecord(recordId)) return;
    _reviewOverrides[recordId] = reviewState;
    notifyListeners();
  }

  void setCorrection(ImportCorrection correction) {
    if (!_hasRecord(correction.recordId)) return;
    _correctionOverrides[correction.recordId] = correction;
    _reviewOverrides[correction.recordId] = correction.reviewState;
    notifyListeners();
  }

  ReviewState reviewStateFor(ImportPreviewRecord record) {
    return _reviewOverrides[record.recordId] ?? record.reviewState;
  }

  Future<void> saveMapping() async {
    await _save(() => gateway.saveMapping(
          endpoint: endpoint,
          session: session,
          importId: importId,
          mapping: Map<String, String>.of(mapping),
        ));
  }

  Future<void> saveCorrections() async {
    final ImportPreview? current = preview;
    if (current == null) return;
    final List<ImportCorrection> corrections = current.records
        .where((ImportPreviewRecord record) =>
            _reviewOverrides.containsKey(record.recordId) ||
            _correctionOverrides.containsKey(record.recordId))
        .map((ImportPreviewRecord record) =>
            _correctionOverrides[record.recordId] ??
            ImportCorrection(
              recordId: record.recordId,
              senderId: record.senderId,
              senderName: record.senderName,
              content: record.content,
              timestamp: record.timestamp,
              messageType: record.messageType,
              reviewState: reviewStateFor(record),
            ))
        .toList(growable: false);
    if (corrections.isEmpty) return;
    await _save(() => gateway.saveCorrections(
          endpoint: endpoint,
          session: session,
          importId: importId,
          corrections: corrections,
        ));
  }

  Future<void> _save(Future<void> Function() action) async {
    if (state == ImportReviewState.saving) return;
    state = ImportReviewState.saving;
    errorMessage = null;
    notifyListeners();
    try {
      await action();
      state = ImportReviewState.ready;
    } catch (_) {
      state = ImportReviewState.error;
      errorMessage = '导入审核保存失败，请重试。';
    }
    notifyListeners();
  }

  bool _hasRecord(String recordId) {
    return preview?.records
            .any((ImportPreviewRecord record) => record.recordId == recordId) ??
        false;
  }
}
