import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

enum BackgroundUploadState {
  queued,
  running,
  retrying,
  completed,
  cancelled,
}

/// Only stable routing metadata is passed to the platform scheduler. Session
/// and pairing tokens remain in secure storage and never enter WorkManager
/// input data or notification text.
class BackgroundUploadRequest {
  const BackgroundUploadRequest({
    required this.importId,
    required this.personaId,
    required this.totalBytes,
    required this.chunkCount,
  });

  final String importId;
  final String personaId;
  final int totalBytes;
  final int chunkCount;

  Map<String, dynamic> toJson() => <String, dynamic>{
        'import_id': importId,
        'persona_id': personaId,
        'total_bytes': totalBytes,
        'chunk_count': chunkCount,
      };
}

class BackgroundUploadUpdate {
  const BackgroundUploadUpdate({
    required this.importId,
    required this.state,
    required this.receivedBytes,
    required this.totalBytes,
    this.errorMessage,
  });

  final String importId;
  final BackgroundUploadState state;
  final int receivedBytes;
  final int totalBytes;
  final String? errorMessage;

  Map<String, dynamic> toJson() => <String, dynamic>{
        'import_id': importId,
        'state': state.name,
        'received_bytes': receivedBytes,
        'total_bytes': totalBytes,
        if (errorMessage != null) 'error_message': errorMessage,
      };
}

abstract interface class BackgroundUploadScheduler {
  Future<void> enqueue(BackgroundUploadRequest request);

  Future<void> report(BackgroundUploadUpdate update);

  Future<void> cancel(String importId);
}

/// Used on iOS and in tests until a native iOS background path is designed.
class NoopBackgroundUploadScheduler implements BackgroundUploadScheduler {
  const NoopBackgroundUploadScheduler();

  @override
  Future<void> enqueue(BackgroundUploadRequest request) async {}

  @override
  Future<void> report(BackgroundUploadUpdate update) async {}

  @override
  Future<void> cancel(String importId) async {}
}

/// Android implementation backed by the small native WorkManager bridge.
///
/// The bridge is deliberately narrow: the platform can schedule, update a
/// bounded notification, and cancel a wake-up, while Dart remains the source
/// of truth for authenticated chunk upload and resume state.
class MethodChannelBackgroundUploadScheduler
    implements BackgroundUploadScheduler {
  MethodChannelBackgroundUploadScheduler({
    MethodChannel? channel,
    this.onWake,
  }) : _channel = channel ?? const MethodChannel(_channelName) {
    if (onWake != null) {
      _channel.setMethodCallHandler(_handleMethodCall);
    }
  }

  static const String _channelName = 'past_partner/background_upload';
  final MethodChannel _channel;
  final BackgroundUploadWakeHandler? onWake;

  @override
  Future<void> enqueue(BackgroundUploadRequest request) async {
    await _channel.invokeMethod<void>('enqueue', request.toJson());
  }

  @override
  Future<void> report(BackgroundUploadUpdate update) async {
    await _channel.invokeMethod<void>('report', update.toJson());
  }

  @override
  Future<void> cancel(String importId) async {
    await _channel.invokeMethod<void>('cancel', <String, dynamic>{
      'import_id': importId,
    });
  }

  Future<void> _handleMethodCall(MethodCall call) async {
    if (call.method != 'wake' || onWake == null) return;
    final dynamic arguments = call.arguments;
    if (arguments is! Map || arguments['import_id'] is! String) return;
    final String importId = (arguments['import_id'] as String).trim();
    if (importId.isEmpty) return;
    await onWake!(importId);
  }
}

typedef BackgroundUploadWakeHandler = Future<void> Function(String importId);

BackgroundUploadScheduler backgroundUploadSchedulerForPlatform({
  TargetPlatform? platform,
  MethodChannel? channel,
  BackgroundUploadWakeHandler? onWake,
}) {
  final TargetPlatform target = platform ?? defaultTargetPlatform;
  if (target != TargetPlatform.android) {
    return const NoopBackgroundUploadScheduler();
  }
  return MethodChannelBackgroundUploadScheduler(
    channel: channel,
    onWake: onWake,
  );
}
