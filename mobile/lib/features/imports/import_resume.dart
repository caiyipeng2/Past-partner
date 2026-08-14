import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'import_file.dart';

class ImportResumeError implements Exception {
  const ImportResumeError(this.message);

  final String message;

  @override
  String toString() => message;
}

class ImportResumeFile {
  const ImportResumeFile({
    required this.path,
    required this.sourceName,
    required this.mediaType,
    required this.length,
  });

  final String path;
  final String sourceName;
  final String mediaType;
  final int length;

  static ImportResumeFile? fromLocalFile(LocalImportFile file) {
    final String? path = file.resumablePath;
    if (path == null || path.isEmpty) return null;
    return ImportResumeFile(
      path: path,
      sourceName: file.sourceName,
      mediaType: file.mediaType,
      length: file.length,
    );
  }

  factory ImportResumeFile.fromJson(dynamic value) {
    if (value is! Map) throw const ImportResumeError('恢复清单无效。');
    final Map<String, dynamic> json = Map<String, dynamic>.from(value);
    final dynamic path = json['path'];
    final dynamic sourceName = json['source_name'];
    final dynamic mediaType = json['media_type'];
    final dynamic length = json['length'];
    if (path is! String || path.trim().isEmpty ||
        sourceName is! String || sourceName.trim().isEmpty ||
        mediaType is! String || mediaType.trim().isEmpty ||
        length is! int || length <= 0) {
      throw const ImportResumeError('恢复文件信息无效。');
    }
    return ImportResumeFile(
      path: path,
      sourceName: sourceName,
      mediaType: mediaType,
      length: length,
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'path': path,
        'source_name': sourceName,
        'media_type': mediaType,
        'length': length,
      };

  LocalImportFile toLocalFile() => RandomAccessImportFile(
        path: path,
        sourceName: sourceName,
        mediaType: mediaType,
        length: length,
      );
}

class ImportUploadResume {
  const ImportUploadResume({
    required this.importId,
    required this.personaId,
    required this.files,
  });

  static const int schemaVersion = 1;

  final String importId;
  final String personaId;
  final List<ImportResumeFile> files;

  factory ImportUploadResume.fromJson(dynamic value) {
    if (value is! Map) throw const ImportResumeError('恢复清单无效。');
    final Map<String, dynamic> json = Map<String, dynamic>.from(value);
    if (json['schema_version'] != schemaVersion ||
        json['import_id'] is! String ||
        (json['import_id'] as String).trim().isEmpty ||
        json['persona_id'] is! String ||
        (json['persona_id'] as String).trim().isEmpty ||
        json['files'] is! List || (json['files'] as List).isEmpty) {
      throw const ImportResumeError('恢复清单无效。');
    }
    return ImportUploadResume(
      importId: json['import_id'] as String,
      personaId: json['persona_id'] as String,
      files: List<ImportResumeFile>.unmodifiable(
        (json['files'] as List).map(ImportResumeFile.fromJson),
      ),
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'schema_version': schemaVersion,
        'import_id': importId,
        'persona_id': personaId,
        'files': files.map((ImportResumeFile file) => file.toJson()).toList(),
      };
}

abstract interface class ImportResumeStore {
  Future<ImportUploadResume?> read(String importId);
  Future<void> write(ImportUploadResume resume);
  Future<void> delete(String importId);
}

class InMemoryImportResumeStore implements ImportResumeStore {
  final Map<String, ImportUploadResume> _values =
      <String, ImportUploadResume>{};

  @override
  Future<ImportUploadResume?> read(String importId) async => _values[importId];

  @override
  Future<void> write(ImportUploadResume resume) async {
    _values[resume.importId] = resume;
  }

  @override
  Future<void> delete(String importId) async {
    _values.remove(importId);
  }
}

class SecureImportResumeStore implements ImportResumeStore {
  SecureImportResumeStore({FlutterSecureStorage? storage})
      : _storage = storage ?? const FlutterSecureStorage();

  static const String _prefix = 'past_partner.import_resume.';
  final FlutterSecureStorage _storage;

  String _key(String importId) => '$_prefix$importId';

  @override
  Future<ImportUploadResume?> read(String importId) async {
    final String? value = await _storage.read(key: _key(importId));
    if (value == null) return null;
    try {
      return ImportUploadResume.fromJson(jsonDecode(value));
    } on Object {
      await delete(importId);
      return null;
    }
  }

  @override
  Future<void> write(ImportUploadResume resume) async {
    await _storage.write(key: _key(resume.importId), value: jsonEncode(resume.toJson()));
  }

  @override
  Future<void> delete(String importId) async {
    await _storage.delete(key: _key(importId));
  }
}
