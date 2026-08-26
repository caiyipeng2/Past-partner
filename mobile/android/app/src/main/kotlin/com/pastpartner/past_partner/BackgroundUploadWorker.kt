package com.pastpartner.past_partner

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters

/**
 * WorkManager is intentionally a wake/notification boundary. It does not
 * receive bearer tokens or duplicate the HTTP upload protocol. Tapping the
 * notification brings the Flutter client back to its secure resume flow.
 */
internal class BackgroundUploadWorker(
    appContext: Context,
    workerParams: WorkerParameters,
) : CoroutineWorker(appContext, workerParams) {
    override suspend fun doWork(): Result {
        val importId = inputData.getString(importIdKey)
        if (importId.isNullOrBlank()) return Result.failure()
        BackgroundUploadNotification.show(
            applicationContext,
            importId,
            "queued",
            received = 0,
            total = 0,
            error = null,
        )
        return Result.success()
    }

    companion object {
        const val importIdKey = "import_id"
        const val uniquePrefix = "past_partner_upload_"
    }
}
