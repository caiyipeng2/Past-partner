import 'package:flutter_test/flutter_test.dart';

import '../../../lib/core/session/session.dart';

void main() {
  test('session parses only bounded response metadata', () {
    final Session session = Session.fromJson(<String, dynamic>{
      'access_token': 'bearer',
      'owner_id': 'owner',
      'expires_at': '2099-01-01T00:00:00Z',
    });
    expect(session.ownerId, 'owner');
    expect(session.isExpired, isFalse);
  });

  test('malformed session response is rejected', () {
    expect(() => Session.fromJson(<String, dynamic>{'access_token': 'bearer'}), throwsFormatException);
  });
}
