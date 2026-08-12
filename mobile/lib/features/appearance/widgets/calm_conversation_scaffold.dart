import 'package:flutter/material.dart';

class CalmConversationScaffold extends StatelessWidget {
  const CalmConversationScaffold({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xfff0f1f2),
      body: SafeArea(
        child: Column(
          children: <Widget>[
            _CalmHeader(),
            const Expanded(child: _CalmConversation()),
            const _CalmComposer(),
          ],
        ),
      ),
    );
  }
}

class _CalmHeader extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 56,
      child: Row(
        children: <Widget>[
          IconButton(
            tooltip: 'Back',
            onPressed: () {},
            icon: const Icon(Icons.arrow_back_ios_new_rounded),
          ),
          const Expanded(
            child: Text('Whisky', textAlign: TextAlign.center, style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
          ),
          IconButton(
            tooltip: 'Conversation details',
            onPressed: () {},
            icon: const Icon(Icons.more_horiz_rounded),
          ),
        ],
      ),
    );
  }
}

class _CalmConversation extends StatelessWidget {
  const _CalmConversation();

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 20, 20, 12),
      children: <Widget>[
        const Center(child: Text('Today 12:18', style: TextStyle(color: Color(0xff8d9297)))),
        const SizedBox(height: 22),
        Align(
          alignment: Alignment.centerRight,
          child: Container(
            constraints: const BoxConstraints(maxWidth: 310),
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            decoration: BoxDecoration(color: const Color(0xffd9f4bd), borderRadius: BorderRadius.circular(8)),
            child: const Text('I saved a little space for you today.', style: TextStyle(fontSize: 16, height: 1.45)),
          ),
        ),
        const SizedBox(height: 18),
        Align(
          alignment: Alignment.centerLeft,
          child: Container(
            constraints: const BoxConstraints(maxWidth: 310),
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(8)),
            child: const Text('That sounds like a gentle place to start.', style: TextStyle(fontSize: 16, height: 1.45)),
          ),
        ),
      ],
    );
  }
}

class _CalmComposer extends StatefulWidget {
  const _CalmComposer();

  @override
  State<_CalmComposer> createState() => _CalmComposerState();
}

class _CalmComposerState extends State<_CalmComposer> {
  bool expanded = false;

  @override
  Widget build(BuildContext context) {
    final double bottomInset = MediaQuery.viewInsetsOf(context).bottom;
    return AnimatedSize(
      duration: const Duration(milliseconds: 220),
      child: Padding(
        padding: EdgeInsets.fromLTRB(12, 8, 12, 8 + bottomInset),
        child: Column(
          children: <Widget>[
            Row(
              children: <Widget>[
                IconButton(tooltip: 'Voice preview', onPressed: () {}, icon: const Icon(Icons.mic_none_rounded)),
                const Expanded(
                  child: TextField(decoration: InputDecoration(hintText: 'Write a message', filled: true, border: InputBorder.none)),
                ),
                IconButton(tooltip: 'Expressions', onPressed: () {}, icon: const Icon(Icons.sentiment_satisfied_alt_outlined)),
                IconButton(
                  tooltip: 'More actions',
                  onPressed: () => setState(() => expanded = !expanded),
                  icon: Icon(expanded ? Icons.close_rounded : Icons.add_circle_outline_rounded),
                ),
              ],
            ),
            if (expanded)
              Semantics(
                label: 'More actions panel',
                child: const Padding(
                  padding: EdgeInsets.only(top: 10),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceAround,
                    children: <Widget>[
                      _UtilityAction(icon: Icons.photo_outlined, label: 'Gallery'),
                      _UtilityAction(icon: Icons.camera_alt_outlined, label: 'Camera'),
                      _UtilityAction(icon: Icons.videocam_outlined, label: 'Video'),
                      _UtilityAction(icon: Icons.location_on_outlined, label: 'Location'),
                    ],
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _UtilityAction extends StatelessWidget {
  const _UtilityAction({required this.icon, required this.label});

  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) => Column(children: <Widget>[Icon(icon), Text(label)]);
}
