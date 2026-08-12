import 'dart:io';

sealed class EndpointKind {
  const EndpointKind();
}

final class PhysicalDeviceEndpoint extends EndpointKind {
  const PhysicalDeviceEndpoint();
}

final class LoopbackForwardingEndpoint extends EndpointKind {
  const LoopbackForwardingEndpoint();
}

class ApiEndpoint {
  const ApiEndpoint._(this.uri, this.kind);

  final Uri uri;
  final EndpointKind kind;

  bool get isPhysicalDevice => kind is PhysicalDeviceEndpoint;
  bool get isLoopbackForwarding => kind is LoopbackForwardingEndpoint;

  static ApiEndpoint parseDebug(String raw) {
    final Uri uri = _parseUri(raw);
    final InternetAddress? address = InternetAddress.tryParse(uri.host);
    if (address == null) {
      throw const FormatException('Endpoint must use a literal IP address.');
    }
    if (uri.scheme == 'http' && _isLoopback(address)) {
      return ApiEndpoint._(uri, const LoopbackForwardingEndpoint());
    }
    if (uri.scheme == 'https' && (_isRfc1918(address) || _isUla(address))) {
      return ApiEndpoint._(uri, const PhysicalDeviceEndpoint());
    }
    throw const FormatException('Endpoint transport or address is not allowed.');
  }

  static ApiEndpoint parseRelease([String? override]) {
    if (override != null && override.trim().isNotEmpty) {
      throw const FormatException('Release builds do not accept endpoint overrides.');
    }
    throw const FormatException('Development connection is unavailable in release builds.');
  }

  Uri path(String value) => uri.replace(path: value, query: null, fragment: null);

  static Uri _parseUri(String raw) {
    final Uri uri;
    try {
      uri = Uri.parse(raw.trim());
    } on FormatException {
      throw const FormatException('Endpoint is not a valid URI.');
    }
    if (uri.host.isEmpty || uri.userInfo.isNotEmpty || uri.query.isNotEmpty || uri.fragment.isNotEmpty) {
      throw const FormatException('Endpoint cannot contain user info, query, or fragment.');
    }
    if (uri.path.isNotEmpty && uri.path != '/') {
      throw const FormatException('Endpoint must not contain a path.');
    }
    if (uri.port < 0 || uri.port > 65535) {
      throw const FormatException('Endpoint port is invalid.');
    }
    return uri.replace(path: '/');
  }

  static bool _isLoopback(InternetAddress address) =>
      (address.type == InternetAddressType.IPv4 && address.address == '127.0.0.1') ||
      (address.type == InternetAddressType.IPv6 && address.address == '::1');

  static bool _isRfc1918(InternetAddress address) {
    if (address.type != InternetAddressType.IPv4) return false;
    final List<int> bytes = address.rawAddress;
    return bytes[0] == 10 ||
        (bytes[0] == 172 && bytes[1] >= 16 && bytes[1] <= 31) ||
        (bytes[0] == 192 && bytes[1] == 168);
  }

  static bool _isUla(InternetAddress address) =>
      address.type == InternetAddressType.IPv6 && (address.rawAddress.first & 0xfe) == 0xfc;
}
