import 'package:flutter/foundation.dart';

abstract final class BuildPolicy {
  static const bool isRelease = bool.fromEnvironment('dart.vm.product');

  static bool get supportsDevelopmentPairing => !kReleaseMode && !isRelease;
}
