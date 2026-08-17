import 'package:flutter_test/flutter_test.dart';
import 'package:past_partner/core/config/api_endpoint.dart';
import 'package:past_partner/core/network/api_failure.dart';
import 'package:past_partner/core/session/session.dart';
import 'package:past_partner/features/chat/chat.dart';
import 'package:past_partner/features/chat/chat_controller.dart';
import 'package:past_partner/features/chat/chat_gateway.dart';

class FakeGateway implements ChatGateway {
  int listCalls = 0;
  int createCalls = 0;
  int getCalls = 0;
  int sendCalls = 0;
  bool failNextSend = false;
  late Conversation value = Conversation.fromJson(<String, dynamic>{
    'id': 'conversation-1',
    'persona_id': 'persona-1',
    'provider_id': 'test',
    'model_id': 'deterministic',
    'created_at': '2026-08-14T00:00:00Z',
    'updated_at': '2026-08-14T00:00:00Z',
    'messages': <Map<String, dynamic>>[],
  });

  @override
  Future<List<ConversationSummary>> list({
    required ApiEndpoint endpoint,
    required Session session,
    required String personaId,
  }) async {
    listCalls += 1;
    return <ConversationSummary>[
      ConversationSummary.fromJson(<String, dynamic>{
        'id': 'conversation-1',
        'persona_id': 'persona-1',
        'provider_id': 'test',
        'model_id': 'deterministic',
        'updated_at': '2026-08-14T00:00:00Z',
      }),
    ];
  }

  @override
  Future<Conversation> create({
    required ApiEndpoint endpoint,
    required Session session,
    required String personaId,
    required String providerId,
    required String modelId,
  }) async {
    createCalls += 1;
    return value;
  }

  @override
  Future<Conversation> get({
    required ApiEndpoint endpoint,
    required Session session,
    required String conversationId,
  }) async {
    getCalls += 1;
    return value;
  }

  @override
  Future<Conversation> send({
    required ApiEndpoint endpoint,
    required Session session,
    required String conversationId,
    required String content,
  }) async {
    sendCalls += 1;
    if (failNextSend) {
      failNextSend = false;
      throw const ApiFailure(
        'provider_not_configured',
        'unavailable',
        statusCode: 503,
      );
    }
    value = Conversation.fromJson(<String, dynamic>{
      'id': 'conversation-1',
      'persona_id': 'persona-1',
      'provider_id': 'test',
      'model_id': 'deterministic',
      'created_at': '2026-08-14T00:00:00Z',
      'updated_at': '2026-08-14T00:00:02Z',
      'messages': <Map<String, dynamic>>[
        <String, dynamic>{
          'id': 'm1',
          'role': 'user',
          'content': content,
          'created_at': '2026-08-14T00:00:01Z',
        },
        <String, dynamic>{
          'id': 'm2',
          'role': 'assistant',
          'content': '测试回复：$content',
          'created_at': '2026-08-14T00:00:02Z',
        },
      ],
    });
    return value;
  }
}

void main() {
  final ApiEndpoint endpoint = ApiEndpoint.parseDebug('http://127.0.0.1:8080');
  final Session session = Session(
    accessToken: 'token',
    ownerId: 'owner',
    expiresAt: DateTime.utc(2099),
  );

  test('loads a conversation and sends one user/assistant turn', () async {
    final FakeGateway gateway = FakeGateway();
    final ChatController controller = ChatController(
      endpoint: endpoint,
      session: session,
      personaId: 'persona-1',
      providerId: 'test',
      modelId: 'deterministic',
      gateway: gateway,
    );
    await controller.load();
    expect(controller.state, ChatState.ready);
    expect(await controller.send('你好'), isTrue);
    expect(
      controller.conversation!.messages.map(
        (ConversationMessage item) => item.role,
      ),
      <String>['user', 'assistant'],
    );
    expect(gateway.sendCalls, 1);
    expect(gateway.createCalls, 0);
    expect(gateway.getCalls, 1);
  });

  test(
    'provider failure exposes retryable state without inventing a reply',
    () async {
      final FakeGateway gateway = FakeGateway()..failNextSend = true;
      final ChatController controller = ChatController(
        endpoint: endpoint,
        session: session,
        personaId: 'persona-1',
        providerId: 'test',
        modelId: 'deterministic',
        gateway: gateway,
      );
      await controller.load();
      expect(await controller.send('稍等'), isFalse);
      expect(controller.state, ChatState.error);
      expect(controller.errorMessage, contains('更换模型'));
      expect(controller.conversation!.messages, isEmpty);
      expect(await controller.retryLast(), isTrue);
      expect(controller.conversation!.messages.length, 2);
    },
  );
}
