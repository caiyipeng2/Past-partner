package com.pastpartner.past_partner

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

internal object BackgroundUploadNotification {
    private const val channelId = "past_partner_uploads"
    private const val channelName = "后台上传"
    private const val notificationBaseId = 7300

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val channel = NotificationChannel(
            channelId,
            channelName,
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Past Partner 导入任务状态"
        }
        context.getSystemService(NotificationManager::class.java)
            ?.createNotificationChannel(channel)
    }

    fun show(context: Context, importId: String, state: String, received: Int, total: Int, error: String?) {
        ensureChannel(context)
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra(BackgroundUploadWorker.importIdKey, importId)
        }
        val pendingIntent = PendingIntent.getActivity(
            context,
            notificationId(importId),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val (title, text) = when (state) {
            "queued" -> "导入任务已排队" to "连接可用时将提醒恢复上传"
            "running" -> "正在上传导入资料" to progressText(received, total)
            "retrying" -> "导入上传等待重试" to (error ?: "网络恢复后将继续")
            "completed" -> "导入资料上传完成" to "可以打开任务查看处理进度"
            "cancelled" -> "导入上传已取消" to ""
            else -> "导入任务状态已更新" to ""
        }
        val builder = NotificationCompat.Builder(context, channelId)
            .setSmallIcon(android.R.drawable.stat_sys_upload)
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(pendingIntent)
            .setAutoCancel(state == "completed" || state == "cancelled")
            .setOnlyAlertOnce(true)
            .setOngoing(state == "queued" || state == "running" || state == "retrying")
            .setPriority(NotificationCompat.PRIORITY_LOW)
        if (state == "running" && total > 0) {
            builder.setProgress(total, received.coerceIn(0, total), false)
        }
        try {
            // Android 13+ may deny POST_NOTIFICATIONS until the app requests
            // it; an OS permission decision must not crash upload orchestration.
            NotificationManagerCompat.from(context).notify(notificationId(importId), builder.build())
        } catch (_: SecurityException) {
            // Progress remains available from the server and next app refresh.
        }
    }

    fun cancel(context: Context, importId: String) {
        try {
            NotificationManagerCompat.from(context).cancel(notificationId(importId))
        } catch (_: SecurityException) {
            // Missing notification permission is already a stable no-op.
        }
    }

    private fun notificationId(importId: String): Int =
        notificationBaseId + (importId.hashCode() and 0x7fff)

    private fun progressText(received: Int, total: Int): String =
        if (total <= 0) "后台任务正在运行" else "$received / $total 字节"
}
