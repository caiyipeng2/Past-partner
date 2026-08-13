import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'session.dart';
import 'session_store.dart';

class SecureSessionStore implements SessionStore {
  SecureSessionStore({FlutterSecureStorage? storage}) : _storage = storage ?? const FlutterSecureStorage();

  static const String _accessTokenKey = 'past_partner.access_token';
  static const String _ownerIdKey = 'past_partner.owner_id';
  static const String _expiresAtKey = 'past_partner.expires_at';
  final FlutterSecureStorage _storage;

  @override
  Future<Session?> read() async {
    final String? accessToken = await _storage.read(key: _accessTokenKey);
    final String? ownerId = await _storage.read(key: _ownerIdKey);
    final String? expiresAt = await _storage.read(key: _expiresAtKey);
    if (accessToken == null || ownerId == null || expiresAt == null) return null;
    try {
      return Session(accessToken: accessToken, ownerId: ownerId, expiresAt: DateTime.parse(expiresAt).toUtc());
    } on FormatException {
      await clear();
      return null;
    }
  }

  @override
  Future<void> write(Session session) async {
    await _storage.write(key: _accessTokenKey, value: session.accessToken);
    await _storage.write(key: _ownerIdKey, value: session.ownerId);
    await _storage.write(key: _expiresAtKey, value: session.expiresAt.toIso8601String());
  }

  @override
  Future<void> clear() async {
    await _storage.delete(key: _accessTokenKey);
    await _storage.delete(key: _ownerIdKey);
    await _storage.delete(key: _expiresAtKey);
  }
}
