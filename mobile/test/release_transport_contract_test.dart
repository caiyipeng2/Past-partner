import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('release Android manifest forbids cleartext traffic', () {
    final String manifest =
        File('android/app/src/main/AndroidManifest.xml').readAsStringSync();
    expect(manifest, contains('android:usesCleartextTraffic="false"'));
  });

  test('Android app declares internet access in the shared manifest', () {
    final String manifest =
        File('android/app/src/main/AndroidManifest.xml').readAsStringSync();
    expect(
      manifest,
      matches(RegExp(
          r'<uses-permission\s+android:name="android\.permission\.INTERNET"\s*/>')),
    );
  });

  test('release iOS plist contains no ATS exception', () {
    final String plist = File('ios/Runner/Info.plist').readAsStringSync();
    expect(plist, isNot(contains('NSAllowsArbitraryLoads')));
    expect(plist, isNot(contains('NSExceptionDomains')));
  });

  test('development cleartext is isolated to the debug manifest', () {
    final String releaseManifest =
        File('android/app/src/main/AndroidManifest.xml').readAsStringSync();
    final String debugManifest =
        File('android/app/src/debug/AndroidManifest.xml').readAsStringSync();
    expect(releaseManifest,
        isNot(contains('android:usesCleartextTraffic="true"')));
    expect(debugManifest, contains('android:usesCleartextTraffic="true"'));
  });
}
