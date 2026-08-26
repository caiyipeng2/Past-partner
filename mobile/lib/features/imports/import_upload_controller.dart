import 'dart:math' as math;

import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';

import '../../core/config/api_endpoint.dart';
import '../../core/session/session.dart';
import 'import_file.dart';
import 'import_gateway.dart';
import 'import_job.dart';
import 'import_resume.dart';

enum ImportUploadState { idle, preparing, uploading, completing, ready, error }

class ImportUploadController extends ChangeNotifier {
  ImportUploadController({
    required this.endpoint,
    required this.session,
    required this.personaId,
    required this.gateway,
    required this.createImport,
    this.resumeStore,
    this.chunkSize = 8 * 1024 * 1024,
  }) : assert(chunkSize > 0);

  final ApiEndpoint endpoint;
  final Session session;
  final String personaId;
  final ImportUploadGateway gateway;
  final Future<ImportJob> Function(ImportDraft draft) createImport;
  final ImportResumeStore? resumeStore;
  final int chunkSize;

  ImportUploadState state = ImportUploadState.idle;
  ImportJob? job;
  String? errorMessage;
  String? cleanupError;
  bool resumeUnavailable = false;
  int receivedBytes = 0;
  int totalBytes = 0;
  int currentChunk = -1;
  List<LocalImportFile> _files = const <LocalImportFile>[];

  double get progress =>
      totalBytes == 0 ? 0 : (receivedBytes / totalBytes).clamp(0.0, 1.0);

  Future<void> upload(
    List<LocalImportFile> files, {
    ImportJob? existingJob,
  }) async {
    _files = List<LocalImportFile>.unmodifiable(files);
    errorMessage = null;
    cleanupError = null;
    resumeUnavailable = false;
    if (_files.isEmpty) {
      _fail('请选择至少一个文件。');
      return;
    }
    totalBytes = _files.fold<int>(
        0, (int total, LocalImportFile file) => total + file.length);
    if (totalBytes <= 0) {
      _fail('不能上传空文件。');
      return;
    }
    if (existingJob != null && !_matches(existingJob)) {
      _fail('选择的文件与原导入任务不匹配。');
      return;
    }

    state = ImportUploadState.preparing;
    receivedBytes = 0;
    currentChunk = -1;
    notifyListeners();
    try {
      job = existingJob ?? await createImport(_draft());
      await _saveResumeManifest(job!);
      final int expectedChunks = (totalBytes + chunkSize - 1) ~/ chunkSize;
      final Map<String, dynamic> missing = await gateway.missingChunks(
        endpoint: endpoint,
        session: session,
        importId: job!.id,
        expectedChunks: expectedChunks,
      );
      final Set<int> missingIndexes = _intSet(missing['missing_chunks']);
      receivedBytes = _nonNegativeInt(missing['received_bytes']) ?? 0;
      state = ImportUploadState.uploading;
      notifyListeners();
      for (final int index in missingIndexes.toList()..sort()) {
        currentChunk = index;
        final List<int> bytes = await _readCombined(index * chunkSize,
            math.min(chunkSize, totalBytes - index * chunkSize));
        final String digest = sha256.convert(bytes).toString();
        final Map<String, dynamic> receipt = await gateway.putChunk(
          endpoint: endpoint,
          session: session,
          importId: job!.id,
          index: index,
          bytes: bytes,
          sha256: digest,
        );
        receivedBytes = _nonNegativeInt(receipt['received_bytes']) ??
            math.min(totalBytes, receivedBytes + bytes.length);
        notifyListeners();
      }
      state = ImportUploadState.completing;
      notifyListeners();
      job = await gateway.complete(
        endpoint: endpoint,
        session: session,
        importId: job!.id,
      );
      receivedBytes = totalBytes;
      state = ImportUploadState.ready;
      currentChunk = -1;
      await _clearResumeManifest(job!);
    } catch (_) {
      _fail('文件上传失败，请重试。', notify: false);
    }
    notifyListeners();
  }

  Future<void> retry() => upload(_files, existingJob: job);

  Future<bool> resume(ImportJob existingJob) async {
    resumeUnavailable = false;
    errorMessage = null;
    final ImportResumeStore? store = resumeStore;
    if (store == null) {
      resumeUnavailable = true;
      _fail('本地恢复记录不存在，请重新选择原文件。');
      return false;
    }
    final ImportUploadResume? resumeManifest;
    try {
      resumeManifest = await store.read(existingJob.id);
    } on Object {
      _fail('本地恢复记录读取失败，请重试。');
      return false;
    }
    if (resumeManifest == null || resumeManifest.personaId != personaId) {
      resumeUnavailable = true;
      _fail('本地恢复记录不存在，请重新选择原文件。');
      return false;
    }
    final List<LocalImportFile> files = resumeManifest.files
        .map((ImportResumeFile file) => file.toLocalFile())
        .toList(growable: false);
    final List<bool> available = await Future.wait(
      files.map((LocalImportFile file) => file.isAvailable()),
    );
    if (available.any((bool value) => !value)) {
      resumeUnavailable = true;
      _fail('本地恢复文件不可用，请重新选择原文件。');
      return false;
    }
    await upload(
      files,
      existingJob: existingJob,
    );
    return state == ImportUploadState.ready;
  }

  Future<void> _saveResumeManifest(ImportJob target) async {
    final ImportResumeStore? store = resumeStore;
    if (store == null) return;
    final List<ImportResumeFile?> entries =
        _files.map(ImportResumeFile.fromLocalFile).toList(growable: false);
    if (entries.any((ImportResumeFile? file) => file == null)) return;
    try {
      await store.write(ImportUploadResume(
        importId: target.id,
        personaId: personaId,
        files: entries.cast<ImportResumeFile>(),
      ));
    } on Object {
      throw const ImportResumeError('本地恢复记录保存失败，请重试。');
    }
  }

  Future<void> _clearResumeManifest(ImportJob target) async {
    final ImportResumeStore? store = resumeStore;
    if (store == null) return;
    try {
      await store.delete(target.id);
    } on Object {
      cleanupError = '上传已完成，但本地恢复记录清理失败，请稍后重试。';
    }
  }

  ImportDraft _draft() => ImportDraft(
        personaId: personaId,
        sourceName: _files.first.sourceName,
        totalBytes: totalBytes,
        mediaType: _files.first.mediaType,
        files:
            _files.asMap().entries.map((MapEntry<int, LocalImportFile> item) {
          return ImportFileEntry(
            fileId: 'mobile-${item.key}',
            sourceName: item.value.sourceName,
            mediaType: item.value.mediaType,
            totalBytes: item.value.length,
          );
        }).toList(growable: false),
      );

  bool _matches(ImportJob existing) {
    if (existing.personaId != personaId || existing.totalBytes != totalBytes) {
      return false;
    }
    if (existing.files.isEmpty) {
      return _files.length == 1 &&
          _files.single.sourceName == existing.sourceName &&
          _files.single.mediaType == existing.mediaType;
    }
    if (existing.files.length != _files.length) return false;
    for (int i = 0; i < _files.length; i++) {
      final ImportFileEntry expected = existing.files[i];
      final LocalImportFile actual = _files[i];
      if (expected.sourceName != actual.sourceName ||
          expected.mediaType != actual.mediaType ||
          expected.totalBytes != actual.length) {
        return false;
      }
    }
    return true;
  }

  Future<List<int>> _readCombined(int offset, int count) async {
    final List<int> result = <int>[];
    int remainingOffset = offset;
    int remaining = count;
    for (final LocalImportFile file in _files) {
      if (remaining <= 0) {
        break;
      }
      if (remainingOffset >= file.length) {
        remainingOffset -= file.length;
        continue;
      }
      final int take = math.min(remaining, file.length - remainingOffset);
      result.addAll(await file.readRange(remainingOffset, take));
      remaining -= take;
      remainingOffset = 0;
    }
    if (result.length != count) {
      throw const ImportFileError('文件内容在上传时发生变化。');
    }
    return result;
  }

  static Set<int> _intSet(dynamic value) {
    if (value is! List) throw const FormatException('缺片响应无效。');
    return value.map<int>((dynamic item) {
      if (item is! int || item < 0) {
        throw const FormatException('缺片响应无效。');
      }
      return item;
    }).toSet();
  }

  static int? _nonNegativeInt(dynamic value) =>
      value is int && value >= 0 ? value : null;

  void _fail(String message, {bool notify = true}) {
    state = ImportUploadState.error;
    errorMessage = message;
    if (notify) notifyListeners();
  }
}
