import '../core/network/api_client.dart';
import '../core/session/secure_session_store.dart';
import '../core/session/session_controller.dart';
import '../features/appearance/appearance_controller.dart';
import '../features/appearance/appearance_store.dart';
import '../features/persona/persona_controller.dart';
import '../features/models/model_selection_store.dart';

class AppDependencies {
  AppDependencies() {
    final ApiClient client = ApiClient();
    sessionController = SessionController(SecureSessionStore(), client);
    appearanceController =
        AppearanceController(SharedPreferencesAppearanceStore());
    personaController = PersonaController(sessionController);
    modelSelectionStore = SharedPreferencesModelSelectionStore();
  }

  late final SessionController sessionController;
  late final AppearanceController appearanceController;
  late final PersonaController personaController;
  late final ModelSelectionStore modelSelectionStore;
}
