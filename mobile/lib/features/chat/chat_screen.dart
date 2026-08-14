import 'package:flutter/material.dart';

import '../../features/appearance/appearance_controller.dart';
import '../../features/appearance/conversation_appearance.dart';
import 'chat.dart';
import 'chat_controller.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({
    required this.personaName,
    required this.modelLabel,
    required this.controller,
    required this.appearanceController,
    super.key,
  });

  final String personaName;
  final String modelLabel;
  final ChatController controller;
  final AppearanceController appearanceController;

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final TextEditingController _input = TextEditingController();
  final ScrollController _scroll = ScrollController();

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_scrollToLatest);
    widget.controller.load();
  }

  @override
  void dispose() {
    widget.controller.removeListener(_scrollToLatest);
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _scrollToLatest() {
    if (!mounted) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 180),
          curve: Curves.easeOut,
        );
      }
    });
  }

  Future<void> _send() async {
    final String text = _input.text;
    final bool sent = await widget.controller.send(text);
    if (sent && mounted) _input.clear();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: Listenable.merge(<Listenable>[
        widget.controller,
        widget.appearanceController,
      ]),
      builder: (BuildContext context, Widget? child) {
        final bool lively = widget.appearanceController.appearance ==
            ConversationAppearance.lively;
        return Scaffold(
          backgroundColor:
              lively ? const Color(0xfff7f8fb) : const Color(0xfff0f1f2),
          body: SafeArea(
            child: Column(
              children: <Widget>[
                _ChatHeader(
                  personaName: widget.personaName,
                  modelLabel: widget.modelLabel,
                  lively: lively,
                  onAppearanceChanged: widget.appearanceController.select,
                ),
                Expanded(
                  child: _ConversationBody(
                    controller: widget.controller,
                    lively: lively,
                    scroll: _scroll,
                  ),
                ),
                _Composer(
                  controller: _input,
                  sending: widget.controller.state == ChatState.sending,
                  onSend: _send,
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _ChatHeader extends StatelessWidget {
  const _ChatHeader({
    required this.personaName,
    required this.modelLabel,
    required this.lively,
    required this.onAppearanceChanged,
  });

  final String personaName;
  final String modelLabel;
  final bool lively;
  final ValueChanged<ConversationAppearance> onAppearanceChanged;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 64,
      child: Row(
        children: <Widget>[
          IconButton(
            tooltip: '返回人物列表',
            onPressed: () => Navigator.of(context).maybePop(),
            icon: const Icon(Icons.arrow_back_ios_new_rounded),
          ),
          Expanded(
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.center,
              children: <Widget>[
                Text(
                  personaName,
                  style: const TextStyle(
                    fontSize: 19,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                Text(
                  modelLabel,
                  style: TextStyle(
                    fontSize: 12,
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
          PopupMenuButton<ConversationAppearance>(
            tooltip: '切换对话外观',
            initialValue: lively
                ? ConversationAppearance.lively
                : ConversationAppearance.simplified,
            onSelected: onAppearanceChanged,
            itemBuilder: (BuildContext context) =>
                const <PopupMenuEntry<ConversationAppearance>>[
              PopupMenuItem(
                value: ConversationAppearance.simplified,
                child: Text('简洁对话'),
              ),
              PopupMenuItem(
                value: ConversationAppearance.lively,
                child: Text('活泼对话'),
              ),
            ],
            icon: const Icon(Icons.more_horiz_rounded),
          ),
        ],
      ),
    );
  }
}

class _ConversationBody extends StatelessWidget {
  const _ConversationBody({
    required this.controller,
    required this.lively,
    required this.scroll,
  });

  final ChatController controller;
  final bool lively;
  final ScrollController scroll;

  @override
  Widget build(BuildContext context) {
    if (controller.state == ChatState.loading &&
        controller.conversation == null) {
      return const Center(child: CircularProgressIndicator());
    }
    final String? error = controller.errorMessage;
    return Column(
      children: <Widget>[
        if (error != null)
          MaterialBanner(
            content: Text(error),
            leading: const Icon(Icons.info_outline_rounded),
            actions: <Widget>[
              TextButton(
                onPressed: controller.retryLast,
                child: const Text('重试'),
              ),
            ],
          ),
        Expanded(
          child: ListView.builder(
            controller: scroll,
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 12),
            itemCount: controller.conversation?.messages.length ?? 0,
            itemBuilder: (BuildContext context, int index) {
              final ConversationMessage message =
                  controller.conversation!.messages[index];
              return _MessageBubble(message: message, lively: lively);
            },
          ),
        ),
      ],
    );
  }
}

class _MessageBubble extends StatelessWidget {
  const _MessageBubble({required this.message, required this.lively});

  final ConversationMessage message;
  final bool lively;

  @override
  Widget build(BuildContext context) {
    final bool user = message.isUser;
    final Color color = user
        ? (lively ? const Color(0xffbfe3ff) : const Color(0xffd9f4bd))
        : Theme.of(context).colorScheme.surface;
    return Semantics(
      label: '${user ? '我' : '对方'}：${message.content}',
      container: true,
      child: Align(
        alignment: user ? Alignment.centerRight : Alignment.centerLeft,
        child: Container(
          constraints: const BoxConstraints(maxWidth: 320),
          margin: const EdgeInsets.only(bottom: 12),
          padding: const EdgeInsets.symmetric(horizontal: 15, vertical: 11),
          decoration: BoxDecoration(
            color: color,
            borderRadius: BorderRadius.circular(lively ? 18 : 8),
            boxShadow: const <BoxShadow>[
              BoxShadow(
                color: Color(0x12000000),
                blurRadius: 3,
                offset: Offset(0, 1),
              ),
            ],
          ),
          child: Text(
            message.content,
            style: const TextStyle(fontSize: 16, height: 1.42),
          ),
        ),
      ),
    );
  }
}

class _Composer extends StatelessWidget {
  const _Composer({
    required this.controller,
    required this.sending,
    required this.onSend,
  });

  final TextEditingController controller;
  final bool sending;
  final VoidCallback onSend;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(
        12,
        8,
        12,
        MediaQuery.viewInsetsOf(context).bottom + 10,
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: <Widget>[
          IconButton(
            tooltip: '语音输入（即将支持）',
            onPressed: null,
            icon: const Icon(Icons.mic_none_rounded),
          ),
          Expanded(
            child: TextField(
              controller: controller,
              minLines: 1,
              maxLines: 4,
              enabled: !sending,
              textInputAction: TextInputAction.send,
              onSubmitted: (_) => onSend(),
              decoration: const InputDecoration(
                hintText: '输入消息',
                filled: true,
                border: InputBorder.none,
                contentPadding: EdgeInsets.symmetric(
                  horizontal: 14,
                  vertical: 12,
                ),
              ),
            ),
          ),
          const SizedBox(width: 8),
          SizedBox(
            height: 48,
            child: FilledButton.icon(
              onPressed: sending ? null : onSend,
              icon: sending
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.send_rounded, size: 18),
              label: const Text('发送'),
            ),
          ),
        ],
      ),
    );
  }
}
