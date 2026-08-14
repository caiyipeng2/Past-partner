import 'package:flutter/material.dart';

import '../features/appearance/appearance_controller.dart';
import '../features/connection/connection_screen.dart';
import '../features/persona/persona_controller.dart';
import '../features/persona/persona_workspace_screen.dart';
import '../features/models/model_controller.dart';
import '../features/models/model_gateway.dart';
import '../features/consents/consent_controller.dart';
import '../features/consents/consent_gateway.dart';
import '../features/imports/import_controller.dart';
import '../features/imports/import_file.dart';
import '../features/imports/import_gateway.dart';
import '../features/imports/import_upload_controller.dart';
import '../features/imports/import_resume.dart';
import '../features/imports/import_review_controller.dart';
import '../features/imports/import_review_gateway.dart';
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
                  importFileSource: const FilePickerImportSource(),
                  importUploadControllerFactory: (persona, job) {
                    final snapshot = widget.sessionController;
                    return ImportUploadController(
                      endpoint: snapshot.endpoint!,
                      session: snapshot.session!,
                      personaId: persona.id,
                      gateway: ApiClientImportGateway(snapshot.client),
                      resumeStore: SecureImportResumeStore(),
                      createImport: (draft) =>
                          ApiClientImportGateway(snapshot.client).create(
                        endpoint: snapshot.endpoint!,
                        session: snapshot.session!,
                        draft: draft,
                      ),
                    );
                  },
                  importReviewControllerFactory: (persona, job) {
                    final SessionController snapshot = widget.sessionController;
                    return ImportReviewController(
                      endpoint: snapshot.endpoint!,
                      session: snapshot.session!,
                      importId: job.id,
                      gateway: ApiClientImportReviewGateway(snapshot.client),
                    );
                  },
                  modelSelectionControllerFactory: (selected) {
                    final SessionController snapshot = widget.sessionController;
                    return ModelSelectionController(
                      endpoint: snapshot.endpoint!,
                      session: snapshot.session!,
                      gateway: ApiClientModelGateway(snapshot.client),
                      initialSelection: selected,
                    );
                  },
                  consentControllerFactory: (persona) {
                    final SessionController snapshot = widget.sessionController;
                    return ConsentController(
                      endpoint: snapshot.endpoint!,
                      session: snapshot.session!,
                      personaId: persona.id,
                      gateway: ApiClientConsentGateway(snapshot.client),
                    );
                  },
                )
              : ConnectionScreen(controller: widget.sessionController),
        );
      },
    );
  }
}
