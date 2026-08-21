import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('Android Gradle release signing is explicit and environment-backed', () {
    final String gradle =
        File('android/app/build.gradle.kts').readAsStringSync();
    expect(gradle, contains('PAST_PARTNER_ANDROID_STORE_RELEASE'));
    expect(gradle, contains('PAST_PARTNER_ANDROID_KEYSTORE_FILE'));
    expect(gradle, contains('signingConfigs.create("release")'));
    expect(gradle, contains('GradleException'));
    expect(gradle, isNot(contains('storePassword = "')));
    expect(gradle, isNot(contains('keyPassword = "')));
  });

  test('release versions and transport policy remain aligned', () {
    final String pubspec = File('pubspec.yaml').readAsStringSync();
    final RegExpMatch version =
        RegExp(r'^version:\s*([^\s]+)', multiLine: true).firstMatch(pubspec)!;
    final List<String> parts = version.group(1)!.split('+');
    final String plist = File('ios/Runner/Info.plist').readAsStringSync();
    final String releaseManifest =
        File('android/app/src/main/AndroidManifest.xml').readAsStringSync();
    expect(plist, contains('<key>CFBundleShortVersionString</key><string>${parts[0]}</string>'));
    expect(plist, contains('<key>CFBundleVersion</key><string>${parts[1]}</string>'));
    expect(releaseManifest, contains('android:usesCleartextTraffic="false"'));
    expect(plist, isNot(contains('NSAllowsArbitraryLoads')));
    expect(plist, isNot(contains('NSExceptionDomains')));
  });
}
