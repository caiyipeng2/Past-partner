import 'package:flutter/material.dart';

import '../features/appearance/appearance_controller.dart';
import '../features/connection/connection_screen.dart';
import '../features/persona/persona_controller.dart';
import '../features/persona/persona_workspace_screen.dart';
import '../features/imports/import_controller.dart';
import '../core/session/session_controller.dart';

class PastPartnerApp extends StatefulWidget {
  const PastPartnerApp(
      {required this.sessionController,
      required this.appearanceController,
      required this.personaController,
      super.key});

  final SessionController sessionController;
  final AppearanceController appearanceController;
  final PersonaController personaController;

  @override
  State<PastPartnerApp> createState() => _PastPartnerAppState();
}

class _PastPartnerAppState extends State<PastPartnerApp> {
  @override
  void initState() {
    super.initState();
    widget.appearanceController.restore();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: Listenable.merge(
          <Listenable>[widget.sessionController, widget.appearanceController]),
      builder: (BuildContext context, Widget? child) {
        final bool connected =
            widget.sessionController.state == SessionState.connected;
        return MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: ThemeData(
              colorScheme:
                  ColorScheme.fromSeed(seedColor: const Color(0xff3275c5)),
              useMaterial3: true),
          home: connected
              ? PersonaWorkspaceScreen(
                  controller: widget.personaController,
                  importControllerFactory: (persona) => ImportController(
                    widget.sessionController,
                    personaId: persona.id,
                  ),
                )
              : ConnectionScreen(controller: widget.sessionController),
        );
      },
    );
  }
}
