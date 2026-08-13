enum ImportState {
  created,
  uploading,
  uploaded,
  processing,
  completed,
  failed,
  cancelled;

  static ImportState fromValue(String value) => ImportState.values.firstWhere(
        (ImportState item) => item.name == value,
        orElse: () =>
            throw const FormatException('The import state is invalid.'),
      );

  String get label => switch (this) {
        ImportState.created => '待上传',
        ImportState.uploading => '上传中',
        ImportState.uploaded => '已上传',
        ImportState.processing => '处理中',
        ImportState.completed => '已完成',
        ImportState.failed => '失败',
        ImportState.cancelled => '已取消',
      };
}

class ImportJob {
  const ImportJob({
    required this.id,
    required this.personaId,
    required this.sourceName,
    required this.mediaType,
    required this.totalBytes,
    required this.receivedBytes,
    required this.chunkCount,
    required this.state,
    required this.createdAt,
    required this.updatedAt,
    this.files = const <ImportFileEntry>[],
  });

  final String id;
  final String personaId;
  final String sourceName;
  final String mediaType;
  final int totalBytes;
  final int receivedBytes;
  final int chunkCount;
  final ImportState state;
  final String createdAt;
  final String updatedAt;
  final List<ImportFileEntry> files;

  factory ImportJob.fromJson(Map<String, dynamic> json) {
    final dynamic id = json['id'];
    final dynamic personaId = json['persona_id'];
    final dynamic sourceName = json['source_name'];
    final dynamic mediaType = json['media_type'];
    final dynamic totalBytes = json['total_bytes'];
    final dynamic receivedBytes = json['received_bytes'];
    final dynamic chunkCount = json['chunk_count'];
    final dynamic state = json['state'];
    final dynamic createdAt = json['created_at'];
    final dynamic updatedAt = json['updated_at'];
    final dynamic rawFiles = json['files'];
    if (id is! String ||
        id.isEmpty ||
        personaId is! String ||
        personaId.isEmpty ||
        sourceName is! String ||
        sourceName.isEmpty ||
        mediaType is! String ||
        mediaType.isEmpty ||
        totalBytes is! int ||
        totalBytes < 0 ||
        receivedBytes is! int ||
        receivedBytes < 0 ||
        chunkCount is! int ||
        chunkCount < 0 ||
        state is! String ||
        createdAt is! String ||
        updatedAt is! String) {
      throw const FormatException('The import response is invalid.');
    }
    final List<ImportFileEntry> files = <ImportFileEntry>[];
    if (rawFiles != null) {
      if (rawFiles is! List) {
        throw const FormatException('The import response is invalid.');
      }
      for (final dynamic value in rawFiles) {
        if (value is! Map) {
          throw const FormatException('The import response is invalid.');
        }
        files.add(ImportFileEntry.fromJson(Map<String, dynamic>.from(value)));
      }
    }
    return ImportJob(
      id: id,
      personaId: personaId,
      sourceName: sourceName,
      mediaType: mediaType,
      totalBytes: totalBytes,
      receivedBytes: receivedBytes,
      chunkCount: chunkCount,
      state: ImportState.fromValue(state),
      createdAt: createdAt,
      updatedAt: updatedAt,
      files: List<ImportFileEntry>.unmodifiable(files),
    );
  }

  String get progressLabel =>
      '${_formatBytes(receivedBytes)} / ${_formatBytes(totalBytes)}';

  static String _formatBytes(int bytes) {
    if (bytes < 1024) {
      return '$bytes B';
    }
    if (bytes < 1024 * 1024) {
      return '${(bytes / 1024).toStringAsFixed(1)} KB';
    }
    if (bytes < 1024 * 1024 * 1024) {
      return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
    }
    return '${(bytes / (1024 * 1024 * 1024)).toStringAsFixed(1)} GB';
  }
}

class ImportFileEntry {
  const ImportFileEntry({
    required this.fileId,
    required this.sourceName,
    required this.mediaType,
    required this.totalBytes,
    this.sha256,
  });

  final String fileId;
  final String sourceName;
  final String mediaType;
  final int totalBytes;
  final String? sha256;

  factory ImportFileEntry.fromJson(Map<String, dynamic> json) {
    final dynamic fileId = json['file_id'];
    final dynamic sourceName = json['source_name'];
    final dynamic mediaType = json['media_type'];
    final dynamic totalBytes = json['total_bytes'];
    final dynamic sha256 = json['sha256'];
    if (fileId is! String || fileId.isEmpty ||
        sourceName is! String || sourceName.isEmpty ||
        mediaType is! String || mediaType.isEmpty ||
        totalBytes is! int || totalBytes < 0 ||
        (sha256 != null && sha256 is! String)) {
      throw const FormatException('The import file response is invalid.');
    }
    return ImportFileEntry(
      fileId: fileId,
      sourceName: sourceName,
      mediaType: mediaType,
      totalBytes: totalBytes,
      sha256: sha256 as String?,
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'file_id': fileId,
        'source_name': sourceName,
        'media_type': mediaType,
        'total_bytes': totalBytes,
        'sha256': sha256,
      };
}

class ImportDraft {
  const ImportDraft({
    required this.personaId,
    required this.sourceName,
    required this.totalBytes,
    required this.mediaType,
    this.files,
  });

  final String personaId;
  final String sourceName;
  final int totalBytes;
  final String mediaType;
  final List<ImportFileEntry>? files;

  Map<String, dynamic> toJson() => <String, dynamic>{
        'persona_id': personaId,
        'source_name': sourceName.trim(),
        'total_bytes': totalBytes,
        'media_type': mediaType.trim(),
        if (files != null)
          'files': files!.map((ImportFileEntry item) => item.toJson()).toList(),
      };
}
