import '../core/network/api_client.dart';
import '../core/session/secure_session_store.dart';
import '../core/session/session_controller.dart';
import '../features/appearance/appearance_controller.dart';
import '../features/appearance/appearance_store.dart';

class AppDependencies {
  AppDependencies()
      : sessionController = SessionController(SecureSessionStore(), ApiClient()),
        appearanceController = AppearanceController(SharedPreferencesAppearanceStore());

  final SessionController sessionController;
  final AppearanceController appearanceController;
}
