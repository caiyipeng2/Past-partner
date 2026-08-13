import 'package:flutter/foundation.dart';

import '../../core/config/api_endpoint.dart';
import '../../core/session/session_controller.dart';
import '../../core/session/session.dart';
import 'persona.dart';
import 'persona_gateway.dart';

enum PersonaState { idle, loading, ready, saving, error }

class PersonaController extends ChangeNotifier {
  PersonaController(this.sessionController, {PersonaGateway? gateway})
      : gateway = gateway ?? ApiClientPersonaGateway(sessionController.client);

  final SessionController sessionController;
  final PersonaGateway gateway;
  PersonaState state = PersonaState.idle;
  List<Persona> personas = const <Persona>[];
  String? errorMessage;

  Future<void> load() async {
    final SessionSnapshot? snapshot = _snapshot;
    if (snapshot == null) {
      _setError('请先连接本地服务。');
      return;
    }
    state = PersonaState.loading;
    errorMessage = null;
    notifyListeners();
    try {
      personas = await gateway.list(
          endpoint: snapshot.endpoint, session: snapshot.session);
      state = PersonaState.ready;
    } catch (_) {
      _setError('人物列表加载失败，请重试。', notify: false);
    }
    notifyListeners();
  }

  Future<Persona?> create(PersonaDraft draft) async {
    final SessionSnapshot? snapshot = _snapshot;
    if (snapshot == null) {
      _setError('请先连接本地服务。');
      return null;
    }
    state = PersonaState.saving;
    errorMessage = null;
    notifyListeners();
    try {
      final Persona created = await gateway.create(
          endpoint: snapshot.endpoint, session: snapshot.session, draft: draft);
      personas = <Persona>[...personas, created];
      state = PersonaState.ready;
      notifyListeners();
      return created;
    } catch (_) {
      _setError('人物创建失败，请检查输入后重试。', notify: false);
      notifyListeners();
      return null;
    }
  }

  SessionSnapshot? get _snapshot {
    final session = sessionController.session;
    final endpoint = sessionController.endpoint;
    if (session == null || endpoint == null || session.isExpired) return null;
    return SessionSnapshot(endpoint: endpoint, session: session);
  }

  void _setError(String message, {bool notify = true}) {
    state = PersonaState.error;
    errorMessage = message;
    if (notify) notifyListeners();
  }
}

class SessionSnapshot {
  const SessionSnapshot({required this.endpoint, required this.session});

  final ApiEndpoint endpoint;
  final Session session;
}
