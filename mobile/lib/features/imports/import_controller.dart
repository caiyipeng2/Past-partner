import 'package:flutter/foundation.dart';

import '../../core/config/api_endpoint.dart';
import '../../core/session/session.dart';
import '../../core/session/session_controller.dart';
import 'import_gateway.dart';
import 'import_job.dart';

enum ImportStateView { idle, loading, ready, saving, error }

class ImportController extends ChangeNotifier {
  ImportController(
    this.sessionController, {
    required this.personaId,
    ImportGateway? gateway,
  }) : gateway = gateway ?? ApiClientImportGateway(sessionController.client);

  final SessionController sessionController;
  final String personaId;
  final ImportGateway gateway;
  ImportStateView state = ImportStateView.idle;
  List<ImportJob> jobs = const <ImportJob>[];
  String? errorMessage;

  Future<void> load() async {
    final _ImportSessionSnapshot? snapshot = _snapshot;
    if (snapshot == null) {
      _setError('请先连接本地服务。');
      return;
    }
    state = ImportStateView.loading;
    errorMessage = null;
    notifyListeners();
    try {
      jobs = await gateway.list(
        endpoint: snapshot.endpoint,
        session: snapshot.session,
        personaId: personaId,
      );
      state = ImportStateView.ready;
    } catch (_) {
      _setError('导入任务加载失败，请重试。', notify: false);
    }
    notifyListeners();
  }

  Future<ImportJob?> create(ImportDraft draft) async {
    final _ImportSessionSnapshot? snapshot = _snapshot;
    if (snapshot == null) {
      _setError('请先连接本地服务。');
      return null;
    }
    state = ImportStateView.saving;
    errorMessage = null;
    notifyListeners();
    try {
      final ImportJob created = await gateway.create(
        endpoint: snapshot.endpoint,
        session: snapshot.session,
        draft: draft,
      );
      jobs = <ImportJob>[created, ...jobs];
      state = ImportStateView.ready;
      notifyListeners();
      return created;
    } catch (_) {
      _setError('导入任务创建失败，请重试。', notify: false);
      notifyListeners();
      return null;
    }
  }

  _ImportSessionSnapshot? get _snapshot {
    final Session? session = sessionController.session;
    final ApiEndpoint? endpoint = sessionController.endpoint;
    if (session == null || endpoint == null || session.isExpired) return null;
    return _ImportSessionSnapshot(endpoint: endpoint, session: session);
  }

  void _setError(String message, {bool notify = true}) {
    state = ImportStateView.error;
    errorMessage = message;
    if (notify) notifyListeners();
  }
}

class _ImportSessionSnapshot {
  const _ImportSessionSnapshot({required this.endpoint, required this.session});

  final ApiEndpoint endpoint;
  final Session session;
}
