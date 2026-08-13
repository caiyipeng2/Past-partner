class ApiFailure implements Exception {
  const ApiFailure(this.code, this.message, {this.statusCode});

  final String code;
  final String message;
  final int? statusCode;

  bool get isUnauthorized => statusCode == 401 || code == 'authentication_required';

  @override
  String toString() => 'ApiFailure($code)';
}
