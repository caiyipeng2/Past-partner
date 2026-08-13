import 'package:flutter_test/flutter_test.dart';

import 'package:past_partner/core/config/api_endpoint.dart';

void main() {
  test('debug accepts private HTTPS and loopback forwarding', () {
    expect(ApiEndpoint.parseDebug('https://192.168.50.7:8443').isPhysicalDevice, isTrue);
    expect(ApiEndpoint.parseDebug('http://127.0.0.1:8080').isLoopbackForwarding, isTrue);
    expect(ApiEndpoint.parseDebug('https://[fd12:3456:789a::7]:8443').isPhysicalDevice, isTrue);
  });

  test('debug rejects public, hostname, and physical HTTP endpoints', () {
    expect(() => ApiEndpoint.parseDebug('http://192.168.50.7:8080'), throwsFormatException);
    expect(() => ApiEndpoint.parseDebug('https://api.example.test'), throwsFormatException);
    expect(() => ApiEndpoint.parseDebug('https://8.8.8.8:443'), throwsFormatException);
    expect(() => ApiEndpoint.parseDebug('https://127.0.0.1:443?token=secret'), throwsFormatException);
  });

  test('release does not accept endpoint overrides', () {
    expect(() => ApiEndpoint.parseRelease('http://127.0.0.1:8080'), throwsFormatException);
    expect(() => ApiEndpoint.parseRelease(), throwsFormatException);
  });
}
