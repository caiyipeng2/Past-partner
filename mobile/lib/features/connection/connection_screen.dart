import 'package:flutter/material.dart';

import '../../core/config/build_policy.dart';
import '../../core/session/session_controller.dart';

class ConnectionScreen extends StatefulWidget {
  const ConnectionScreen({required this.controller, super.key});

  final SessionController controller;

  @override
  State<ConnectionScreen> createState() => _ConnectionScreenState();
}

class _ConnectionScreenState extends State<ConnectionScreen> {
  final TextEditingController endpointController = TextEditingController(text: 'http://127.0.0.1:8080');
  final TextEditingController tokenController = TextEditingController();

  @override
  void dispose() {
    endpointController.dispose();
    tokenController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!BuildPolicy.supportsDevelopmentPairing) {
      return const Scaffold(
        body: SafeArea(
          child: Center(
            child: Padding(
              padding: EdgeInsets.all(24),
              child: Text('Development connection is unavailable in this build.'),
            ),
          ),
        ),
      );
    }
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (BuildContext context, Widget? child) => Scaffold(
        appBar: AppBar(title: const Text('Connect to local service')),
        body: SafeArea(
          child: ListView(
            padding: const EdgeInsets.all(24),
            keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
            children: <Widget>[
              const Text('Use a loopback forward or private-LAN HTTPS endpoint.', style: TextStyle(height: 1.4)),
              const SizedBox(height: 24),
              TextField(
                controller: endpointController,
                keyboardType: TextInputType.url,
                autocorrect: false,
                decoration: const InputDecoration(labelText: 'Service endpoint', border: OutlineInputBorder()),
              ),
              const SizedBox(height: 16),
              Semantics(
                label: 'Development pairing token',
                child: TextField(
                  controller: tokenController,
                  obscureText: true,
                  autocorrect: false,
                  decoration: const InputDecoration(labelText: 'Pairing token (physical device only)', border: OutlineInputBorder()),
                ),
              ),
              if (widget.controller.errorMessage != null) ...<Widget>[
                const SizedBox(height: 12),
                Text(widget.controller.errorMessage!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
              ],
              const SizedBox(height: 24),
              SizedBox(
                height: 48,
                child: FilledButton.icon(
                  onPressed: widget.controller.state == SessionState.pairingInProgress
                      ? null
                      : () async {
                          try {
                            await widget.controller.pair(endpointController.text, tokenController.text);
                          } finally {
                            tokenController.clear();
                          }
                        },
                  icon: const Icon(Icons.link_rounded),
                  label: Text(widget.controller.state == SessionState.pairingInProgress ? 'Connecting...' : 'Connect'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
