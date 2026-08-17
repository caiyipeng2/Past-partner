import 'package:flutter/foundation.dart';

import 'privacy_export.dart';
import 'privacy_gateway.dart';

enum PrivacyState { idle, loading, ready, deleting, error }

class PrivacyController extends ChangeNotifier {
  PrivacyController({required this.gateway});

  final PrivacyGateway gateway;
  PrivacyState state = PrivacyState.idle;
  PrivacyExportSummary? summary;
  String? errorMessage;

  Future<void> loadExport() async {
    if (state == PrivacyState.loading || state == PrivacyState.deleting) {
      return;
    }
    state = PrivacyState.loading;
    errorMessage = null;
    notifyListeners();
    try {
      summary = await gateway.exportData();
      state = PrivacyState.ready;
    } catch (_) {
      state = PrivacyState.error;
      errorMessage = '隐私摘要加载失败，请重试。';
    }
    notifyListeners();
  }

  Future<bool> deletePersona(String personaId) async {
    final String normalizedId = personaId.trim();
    if (normalizedId.isEmpty || state == PrivacyState.deleting) return false;
    state = PrivacyState.deleting;
    errorMessage = null;
    notifyListeners();
    try {
      await gateway.deletePersona(normalizedId);
      state = PrivacyState.ready;
      notifyListeners();
      return true;
    } catch (_) {
      state = PrivacyState.error;
      errorMessage = '人物及其关联数据删除失败，请重试。';
      notifyListeners();
      return false;
    }
  }
}
