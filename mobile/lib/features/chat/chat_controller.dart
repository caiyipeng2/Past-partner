import 'package:flutter/foundation.dart';

import '../../core/config/api_endpoint.dart';
import '../../core/network/api_failure.dart';
import '../../core/session/session.dart';
import 'chat.dart';
import 'chat_gateway.dart';

enum ChatState { idle, loading, ready, sending, error }

class ChatController extends ChangeNotifier {
  ChatController({
    required this.endpoint,
    required this.session,
    required this.personaId,
    required this.providerId,
    required this.modelId,
    required this.gateway,
  });

  final ApiEndpoint endpoint;
  final Session session;
  final String personaId;
  final String providerId;
  final String modelId;
  final ChatGateway gateway;

  ChatState state = ChatState.idle;
  Conversation? conversation;
  String? errorMessage;
  String? lastAttempt;

  Future<void> load() async {
    if (state == ChatState.loading || state == ChatState.sending) return;
    state = ChatState.loading;
    errorMessage = null;
    notifyListeners();
    try {
      final List<ConversationSummary> summaries = await gateway.list(
        endpoint: endpoint,
        session: session,
        personaId: personaId,
      );
      ConversationSummary? existing;
      for (final ConversationSummary summary in summaries) {
        if (summary.providerId == providerId && summary.modelId == modelId) {
          existing = summary;
          break;
        }
      }
      conversation = existing == null
          ? await gateway.create(
              endpoint: endpoint,
              session: session,
              personaId: personaId,
              providerId: providerId,
              modelId: modelId,
            )
          : await gateway.get(
              endpoint: endpoint,
              session: session,
              conversationId: existing.id,
            );
      state = ChatState.ready;
    } catch (_) {
      state = ChatState.error;
      errorMessage = '对话初始化失败，请重试。';
    }
    notifyListeners();
  }

  Future<bool> send(String content) async {
    final String normalized = content.trim();
    if (state == ChatState.sending) return false;
    if (normalized.isEmpty) {
      errorMessage = '请输入消息后再发送。';
      state = ChatState.error;
      notifyListeners();
      return false;
    }
    final Conversation? current = conversation;
    if (current == null) {
      errorMessage = '对话尚未准备好，请重试。';
      state = ChatState.error;
      notifyListeners();
      return false;
    }
    state = ChatState.sending;
    errorMessage = null;
    lastAttempt = normalized;
    notifyListeners();
    try {
      conversation = await gateway.send(
        endpoint: endpoint,
        session: session,
        conversationId: current.id,
        content: normalized,
      );
      state = ChatState.ready;
      lastAttempt = null;
      notifyListeners();
      return true;
    } catch (error) {
      state = ChatState.error;
      errorMessage = _friendlyError(error);
      notifyListeners();
      return false;
    }
  }

  Future<bool> retryLast() async {
    final String? value = lastAttempt;
    if (value == null) return false;
    return send(value);
  }

  static String _friendlyError(Object error) {
    if (error is ApiFailure && error.code == 'provider_not_configured') {
      return '当前模型暂不可用，请更换模型后重试。';
    }
    if (error is ApiFailure && error.code == 'transport_unavailable') {
      return '本地服务暂时不可用，请确认服务已启动。';
    }
    return '消息发送失败，请重试。';
  }
}
