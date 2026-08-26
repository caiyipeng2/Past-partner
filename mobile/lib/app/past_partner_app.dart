import 'package:flutter/material.dart';

import '../features/appearance/appearance_controller.dart';
import '../features/privacy/privacy_controller.dart';
import '../features/privacy/privacy_gateway.dart';
import '../features/connection/connection_screen.dart';
import '../features/persona/persona_controller.dart';
import '../features/persona/persona_workspace_screen.dart';
import '../features/models/model_controller.dart';
import '../features/models/model_gateway.dart';
import '../features/models/model_selection_store.dart';
import '../features/consents/consent_controller.dart';
import '../features/consents/consent_gateway.dart';
import '../features/chat/chat_controller.dart';
import '../features/chat/chat_gateway.dart';
import '../features/imports/import_controller.dart';
import '../features/imports/background_upload.dart';
import '../features/imports/import_file.dart';
import '../features/imports/import_gateway.dart';
import '../features/imports/import_upload_controller.dart';
import '../features/imports/import_resume.dart';
import '../features/imports/import_review_controller.dart';
import '../features/imports/import_review_gateway.dart';
import '../core/session/session_controller.dart';

class PastPartnerApp extends StatefulWidget {
  const PastPartnerApp({
    required this.sessionController,
    required this.appearanceController,
    required this.personaController,
    this.modelSelectionStore,
    this.backgroundUploadScheduler,
    super.key,
  });

  final SessionController sessionController;
  final AppearanceController appearanceController;
  final PersonaController personaController;
  final ModelSelectionStore? modelSelectionStore;
  final BackgroundUploadScheduler? backgroundUploadScheduler;

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
      animation: Listenable.merge(<Listenable>[
        widget.sessionController,
        widget.appearanceController,
      ]),
      builder: (BuildContext context, Widget? child) {
        final bool connected =
            widget.sessionController.state == SessionState.connected;
        return MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: ThemeData(
            colorScheme: ColorScheme.fromSeed(
              seedColor: const Color(0xff3275c5),
            ),
            useMaterial3: true,
          ),
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
                      backgroundScheduler: widget.backgroundUploadScheduler ??
                          backgroundUploadSchedulerForPlatform(),
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
                      selectionStore: widget.modelSelectionStore,
                      selectionScope: snapshot.session!.ownerId,
                    );
                  },
                  modelSelectionRestore: widget.modelSelectionStore == null
                      ? null
                      : () async {
                          final SessionController snapshot =
                              widget.sessionController;
                          final ModelSelectionController controller =
                              ModelSelectionController(
                            endpoint: snapshot.endpoint!,
                            session: snapshot.session!,
                            gateway: ApiClientModelGateway(snapshot.client),
                            selectionStore: widget.modelSelectionStore,
                            selectionScope: snapshot.session!.ownerId,
                          );
                          await controller.load();
                          return controller.selected;
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
                  chatControllerFactory: (persona, selected) {
                    if (selected == null) return null;
                    final SessionController snapshot = widget.sessionController;
                    return ChatController(
                      endpoint: snapshot.endpoint!,
                      session: snapshot.session!,
                      personaId: persona.id,
                      providerId: selected.providerId,
                      modelId: selected.id,
                      gateway: ApiClientChatGateway(snapshot.client),
                    );
                  },
                  privacyControllerFactory: () {
                    final SessionController snapshot = widget.sessionController;
                    return PrivacyController(
                      gateway: ApiClientPrivacyGateway(
                        client: snapshot.client,
                        endpoint: snapshot.endpoint!,
                        session: snapshot.session!,
                      ),
                    );
                  },
                  appearanceController: widget.appearanceController,
                )
              : ConnectionScreen(controller: widget.sessionController),
        );
      },
    );
  }
}
