import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('Android background upload bridge is declared in the app contract', () {
    final String manifest =
        File('android/app/src/main/AndroidManifest.xml').readAsStringSync();
    final String activity = File(
      'android/app/src/main/kotlin/com/pastpartner/past_partner/MainActivity.kt',
    ).readAsStringSync();
    final String worker = File(
      'android/app/src/main/kotlin/com/pastpartner/past_partner/BackgroundUploadWorker.kt',
    ).readAsStringSync();
    final String gradle =
        File('android/app/build.gradle.kts').readAsStringSync();

    expect(manifest, contains('android.permission.POST_NOTIFICATIONS'));
    expect(activity, contains('NetworkType.CONNECTED'));
    expect(activity, contains('setBackoffCriteria'));
    expect(activity, contains('past_partner/background_upload'));
    expect(worker, contains('CoroutineWorker'));
    expect(worker, contains('secure'));
    expect(gradle, contains('androidx.work:work-runtime-ktx'));
  });
}
