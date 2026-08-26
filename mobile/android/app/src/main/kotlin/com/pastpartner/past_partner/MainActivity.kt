package com.pastpartner.past_partner

import android.content.Intent
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.util.concurrent.TimeUnit

class MainActivity : FlutterActivity() {
    private var backgroundChannel: MethodChannel? = null
    private var pendingWakeImportId: String? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val channel = MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            CHANNEL_NAME,
        )
        backgroundChannel = channel
        channel.setMethodCallHandler { call, result ->
            when (call.method) {
                "enqueue" -> enqueue(call, result)
                "report" -> report(call, result)
                "cancel" -> cancel(call, result)
                else -> result.notImplemented()
            }
        }
        dispatchWake(pendingWakeImportId ?: intent?.getStringExtra(BackgroundUploadWorker.importIdKey))
        pendingWakeImportId = null
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        dispatchWake(intent.getStringExtra(BackgroundUploadWorker.importIdKey))
    }

    private fun enqueue(call: MethodCall, result: MethodChannel.Result) {
        val values = call.arguments as? Map<*, *>
        val importId = values?.string("import_id")
        if (importId.isNullOrBlank()) {
            result.error("invalid_request", "Import id is required.", null)
            return
        }
        val request = OneTimeWorkRequestBuilder<BackgroundUploadWorker>()
            .setInputData(androidx.work.workDataOf(BackgroundUploadWorker.importIdKey to importId))
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build(),
            )
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .addTag(BackgroundUploadWorker.uniquePrefix + importId)
            .build()
        WorkManager.getInstance(applicationContext).enqueueUniqueWork(
            BackgroundUploadWorker.uniquePrefix + importId,
            ExistingWorkPolicy.REPLACE,
            request,
        )
        result.success(null)
    }

    private fun report(call: MethodCall, result: MethodChannel.Result) {
        val values = call.arguments as? Map<*, *>
        val importId = values?.string("import_id")
        val state = values?.string("state")
        if (importId.isNullOrBlank() || state.isNullOrBlank()) {
            result.error("invalid_request", "Upload update is invalid.", null)
            return
        }
        val received = values.integer("received_bytes")
        val total = values.integer("total_bytes")
        val error = values.string("error_message")
        BackgroundUploadNotification.show(
            applicationContext,
            importId,
            state,
            received = received.coerceAtLeast(0),
            total = total.coerceAtLeast(0),
            error = error,
        )
        if (state == "completed" || state == "cancelled") {
            WorkManager.getInstance(applicationContext)
                .cancelUniqueWork(BackgroundUploadWorker.uniquePrefix + importId)
        }
        result.success(null)
    }

    private fun cancel(call: MethodCall, result: MethodChannel.Result) {
        val values = call.arguments as? Map<*, *>
        val importId = values?.string("import_id")
        if (importId.isNullOrBlank()) {
            result.error("invalid_request", "Import id is required.", null)
            return
        }
        WorkManager.getInstance(applicationContext)
            .cancelUniqueWork(BackgroundUploadWorker.uniquePrefix + importId)
        BackgroundUploadNotification.cancel(applicationContext, importId)
        result.success(null)
    }

    private fun dispatchWake(importId: String?) {
        if (importId.isNullOrBlank()) return
        if (backgroundChannel == null) {
            pendingWakeImportId = importId
            return
        }
        backgroundChannel?.invokeMethod("wake", mapOf("import_id" to importId))
    }

    private fun Map<*, *>.string(key: String): String? = this[key] as? String

    private fun Map<*, *>.integer(key: String): Int = when (val value = this[key]) {
        is Int -> value
        is Long -> value.coerceIn(0, Int.MAX_VALUE.toLong()).toInt()
        else -> 0
    }

    companion object {
        private const val CHANNEL_NAME = "past_partner/background_upload"
    }
}
