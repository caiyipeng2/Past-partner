import 'package:flutter/material.dart';

class BrightConversationScaffold extends StatelessWidget {
  const BrightConversationScaffold({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xfff7f8fb),
      body: SafeArea(
        child: Column(
          children: <Widget>[
            SizedBox(
              height: 56,
              child: Row(
                children: <Widget>[
                  IconButton(tooltip: 'Back', onPressed: () {}, icon: const Icon(Icons.arrow_back_rounded)),
                  const Expanded(child: Text('Whisky', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600))),
                  IconButton(tooltip: 'Menu', onPressed: () {}, icon: const Icon(Icons.menu_rounded)),
                ],
              ),
            ),
            const Expanded(child: _BrightConversation()),
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 8, 14, 10),
              child: Row(
                children: <Widget>[
                  const Expanded(child: TextField(decoration: InputDecoration(hintText: 'Write a message', filled: true))),
                  const SizedBox(width: 8),
                  SizedBox(
                    height: 48,
                    child: FilledButton(onPressed: () {}, child: const Text('Send')),
                  ),
                ],
              ),
            ),
            const _BrightQuickActions(),
          ],
        ),
      ),
    );
  }
}

class _BrightConversation extends StatelessWidget {
  const _BrightConversation();

  @override
  Widget build(BuildContext context) => ListView(
        padding: const EdgeInsets.all(20),
        children: <Widget>[
          const Center(child: Text('Today 12:18', style: TextStyle(color: Color(0xff848b96)))),
          const SizedBox(height: 18),
          Align(
            alignment: Alignment.centerRight,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
              decoration: BoxDecoration(color: const Color(0xffbfe3ff), borderRadius: BorderRadius.circular(18)),
              child: const Text('A small hello for the afternoon.', style: TextStyle(fontSize: 16, height: 1.4)),
            ),
          ),
        ],
      );
}

class _BrightQuickActions extends StatelessWidget {
  const _BrightQuickActions();

  @override
  Widget build(BuildContext context) => SizedBox(
        height: 64,
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceEvenly,
          children: <Widget>[
            IconButton(tooltip: 'Voice preview', onPressed: () {}, icon: const Icon(Icons.mic_none_rounded)),
            IconButton(tooltip: 'Gallery', onPressed: () {}, icon: const Icon(Icons.image_outlined)),
            IconButton(tooltip: 'Expressions', onPressed: () {}, icon: const Icon(Icons.sentiment_satisfied_alt_outlined)),
            IconButton(tooltip: 'More actions', onPressed: () {}, icon: const Icon(Icons.add_circle_outline_rounded)),
          ],
        ),
      );
}
