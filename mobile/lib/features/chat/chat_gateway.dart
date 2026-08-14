import '../../core/config/api_endpoint.dart';
import '../../core/network/api_client.dart';
import '../../core/session/session.dart';
import 'chat.dart';

abstract interface class ChatGateway {
  Future<List<ConversationSummary>> list({
    required ApiEndpoint endpoint,
    required Session session,
    required String personaId,
  });

  Future<Conversation> create({
    required ApiEndpoint endpoint,
    required Session session,
    required String personaId,
    required String providerId,
    required String modelId,
  });

  Future<Conversation> get({
    required ApiEndpoint endpoint,
    required Session session,
    required String conversationId,
  });

  Future<Conversation> send({
    required ApiEndpoint endpoint,
    required Session session,
    required String conversationId,
    required String content,
  });
}

class ApiClientChatGateway implements ChatGateway {
  const ApiClientChatGateway(this.client);

  final ApiClient client;

  @override
  Future<List<ConversationSummary>> list({
    required ApiEndpoint endpoint,
    required Session session,
    required String personaId,
  }) async {
    final List<Map<String, dynamic>> values = await client.listConversations(
      endpoint,
      session,
      personaId: personaId,
    );
    return values.map(ConversationSummary.fromJson).toList(growable: false);
  }

  @override
  Future<Conversation> create({
    required ApiEndpoint endpoint,
    required Session session,
    required String personaId,
    required String providerId,
    required String modelId,
  }) async {
    return Conversation.fromJson(
      await client.createConversation(
        endpoint,
        session,
        personaId: personaId,
        providerId: providerId,
        modelId: modelId,
      ),
    );
  }

  @override
  Future<Conversation> get({
    required ApiEndpoint endpoint,
    required Session session,
    required String conversationId,
  }) async {
    return Conversation.fromJson(
      await client.getConversation(endpoint, session, conversationId),
    );
  }

  @override
  Future<Conversation> send({
    required ApiEndpoint endpoint,
    required Session session,
    required String conversationId,
    required String content,
  }) async {
    return Conversation.fromJson(
      await client.sendConversationMessage(
        endpoint,
        session,
        conversationId,
        content,
      ),
    );
  }
}
