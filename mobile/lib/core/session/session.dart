class Session {
  const Session({required this.accessToken, required this.ownerId, required this.expiresAt});

  final String accessToken;
  final String ownerId;
  final DateTime expiresAt;

  bool get isExpired => !expiresAt.isAfter(DateTime.now().toUtc());

  factory Session.fromJson(Map<String, dynamic> json) {
    final dynamic token = json['access_token'];
    final dynamic owner = json['owner_id'];
    final dynamic expires = json['expires_at'];
    if (token is! String || token.isEmpty || owner is! String || owner.isEmpty || expires is! String) {
      throw const FormatException('The session response is invalid.');
    }
    final DateTime? expiresAt = DateTime.tryParse(expires)?.toUtc();
    if (expiresAt == null) throw const FormatException('The session expiry is invalid.');
    return Session(accessToken: token, ownerId: owner, expiresAt: expiresAt);
  }
}
