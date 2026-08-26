import 'dart:io';

import 'package:file_picker/file_picker.dart';

class ImportFileError implements Exception {
  const ImportFileError(this.message);

  final String message;

  @override
  String toString() => message;
}

abstract interface class LocalImportFile {
  String get sourceName;
  String get mediaType;
  int get length;

  /// A secure resume manifest may retain this local reference when available.
  /// In-memory test sources deliberately return null so their bytes cannot be
  /// mistaken for a process-restart-safe file handle.
  String? get resumablePath => null;

  /// Checks that the source can still be read after a process restart.
  ///
  /// In-memory sources are valid only for the current process and therefore
  /// are never written to a resume manifest; they can use the default value.
  Future<bool> isAvailable() async => true;

  Future<List<int>> readRange(int offset, int length);
}

abstract interface class ImportFileSource {
  Future<List<LocalImportFile>> pick();
}

class MemoryImportFile implements LocalImportFile {
  MemoryImportFile({
    required this.sourceName,
    required this.mediaType,
    required List<int> bytes,
  }) : _bytes = List<int>.unmodifiable(bytes);

  @override
  final String sourceName;

  @override
  final String mediaType;

  final List<int> _bytes;

  @override
  String? get resumablePath => null;

  @override
  Future<bool> isAvailable() async => true;

  @override
  int get length => _bytes.length;

  @override
  Future<List<int>> readRange(int offset, int length) async {
    if (offset < 0 || length <= 0 || offset >= _bytes.length) {
      throw const ImportFileError('文件读取范围无效。');
    }
    final int end = offset + length;
    if (end > _bytes.length) {
      throw const ImportFileError('文件读取范围超出文件大小。');
    }
    return _bytes.sublist(offset, end);
  }
}

class MemoryImportSource implements ImportFileSource {
  const MemoryImportSource();

  @override
  Future<List<LocalImportFile>> pick() async => const <LocalImportFile>[];
}

class FilePickerImportSource implements ImportFileSource {
  const FilePickerImportSource({FilePicker? picker}) : _picker = picker;

  final FilePicker? _picker;

  @override
  Future<List<LocalImportFile>> pick() async {
    final FilePickerResult? result;
    try {
      result = await (_picker ?? FilePicker.platform)
          .pickFiles(allowMultiple: true, withData: false, type: FileType.any);
    } on Object {
      throw const ImportFileError('文件选择器暂时不可用，请重试。');
    }
    if (result == null || result.files.isEmpty) {
      return const <LocalImportFile>[];
    }
    final List<LocalImportFile> files = <LocalImportFile>[];
    for (final PlatformFile item in result.files) {
      final String? path = item.path;
      if (path == null || path.isEmpty) {
        throw const ImportFileError('选中的文件无法读取，请重新选择。');
      }
      files.add(
        RandomAccessImportFile(
          path: path,
          sourceName: item.name,
          mediaType: _mediaType(item.extension, item.name),
          length: item.size,
        ),
      );
    }
    return files;
  }

  static String _mediaType(String? extension, String name) {
    final String value = (extension ?? name.split('.').last).toLowerCase();
    const Map<String, String> types = <String, String>{
      'txt': 'text/plain',
      'json': 'application/json',
      'csv': 'text/csv',
      'html': 'text/html',
      'htm': 'text/html',
      'xml': 'application/xml',
      'zip': 'application/zip',
      'db': 'application/vnd.sqlite3',
      'sqlite': 'application/vnd.sqlite3',
      'jpg': 'image/jpeg',
      'jpeg': 'image/jpeg',
      'png': 'image/png',
      'gif': 'image/gif',
      'webp': 'image/webp',
      'mp3': 'audio/mpeg',
      'm4a': 'audio/mp4',
      'wav': 'audio/wav',
      'mp4': 'video/mp4',
      'mov': 'video/quicktime',
      'avi': 'video/x-msvideo',
    };
    return types[value] ?? 'application/octet-stream';
  }
}

class RandomAccessImportFile implements LocalImportFile {
  const RandomAccessImportFile({
    required this.path,
    required this.sourceName,
    required this.mediaType,
    required this.length,
  });

  final String path;

  @override
  final String sourceName;

  @override
  final String mediaType;

  @override
  final int length;

  @override
  String get resumablePath => path;

  @override
  Future<bool> isAvailable() async {
    try {
      final FileStat stat = await File(path).stat();
      return stat.type == FileSystemEntityType.file && stat.size == length;
    } on FileSystemException {
      return false;
    }
  }

  @override
  Future<List<int>> readRange(int offset, int count) async {
    if (offset < 0 ||
        count <= 0 ||
        offset >= length ||
        offset + count > length) {
      throw const ImportFileError('文件读取范围无效。');
    }
    RandomAccessFile? file;
    try {
      file = await File(path).open();
      final RandomAccessFile opened = file;
      await opened.setPosition(offset);
      final List<int> bytes = await opened.read(count);
      if (bytes.length != count) {
        throw const ImportFileError('文件在读取时发生变化。');
      }
      return bytes;
    } on ImportFileError {
      rethrow;
    } on FileSystemException {
      throw const ImportFileError('文件当前不可读取，请重试。');
    } finally {
      await file?.close();
    }
  }
}
