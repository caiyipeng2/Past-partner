import 'session.dart';

abstract interface class SessionStore {
  Future<Session?> read();
  Future<void> write(Session session);
  Future<void> clear();
}
